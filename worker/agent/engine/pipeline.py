"""
TranscriptionPipeline — manages the persistent transcription subprocess.

Design:
    The persistent_worker subprocess is started ONCE when the worker boots.
    The model is loaded, warmup is performed, and the process stays alive.
    Every job sends a "transcribe" request over stdin and reads the "result"
    from the message queue (populated by a background stdout reader task).

SIGSTOP / SIGCONT:
    Sending SIGSTOP to self.pid freezes the entire subprocess including any
    Metal GPU computation in flight. SIGCONT resumes exactly where it stopped.
    This works identically to the previous single-run design.

Restart on crash:
    If the subprocess crashes (OOM, GPU error), the pipeline detects EOF on
    stdout, marks the current job as failed (transient), and restarts the
    subprocess. Model reloads on restart — unavoidable but rare.

Cancel:
    "cancel" is sent over stdin. The subprocess marks its next result as
    cancelled. Since mlx_whisper.transcribe() is a blocking C call, we cannot
    interrupt it mid-inference. The result is computed but discarded.
    The coordinator's ownership check will reject any stale upload anyway.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .metrics import InferenceTimer, TranscriptionMetrics

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, float], Awaitable[None]]

_MAX_RESTARTS = 3
_RESTART_DELAY = 5.0          # seconds between restart attempts
_STARTUP_TIMEOUT = 120.0      # seconds to wait for "ready" on startup
_HEARTBEAT_WARN_SECONDS = 30  # warn if no message in this window during inference


@dataclass
class TranscriptionResult:
    text: str
    language: str
    segments: list[dict[str, Any]]
    metrics: TranscriptionMetrics

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    @property
    def word_count(self) -> int:
        return sum(len(s.get("words", [])) for s in self.segments)


class PipelineStartupError(RuntimeError):
    """Raised when the persistent subprocess fails to start cleanly."""


class TranscriptionPipelineError(RuntimeError):
    def __init__(self, message: str, error_category: str = "transient") -> None:
        super().__init__(message)
        self.error_category = error_category


class JobCancelledError(RuntimeError):
    pass


class TranscriptionPipeline:
    """
    Manages a persistent mlx-whisper subprocess.
    Create one instance per worker at startup; call start() before first use.
    """

    def __init__(
        self,
        model_path: Path,
        language: str = "tr",
        term_timeout: float = 5.0,
    ) -> None:
        self._model_path = model_path
        self._language = language
        self._term_timeout = term_timeout

        self._proc: asyncio.subprocess.Process | None = None
        self._message_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._backend: str = "unknown"
        self._load_seconds: float = 0.0
        self._warmup_seconds: float = 0.0
        self._restart_count: int = 0
        self._started: bool = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the persistent subprocess and wait for model + warmup."""
        await self._launch()
        self._started = True
        logger.info(
            "TranscriptionPipeline ready",
            extra={
                "backend": self._backend,
                "model_load_seconds": self._load_seconds,
                "warmup_seconds": self._warmup_seconds,
            },
        )

    async def stop(self) -> None:
        """Shut down the persistent subprocess cleanly."""
        self._started = False
        await self._send_raw({"type": "shutdown"})
        await self._kill_process(graceful=True)
        await self._cancel_io_tasks()
        while not self._message_queue.empty():
            self._message_queue.get_nowait()

    async def cancel_inflight(self) -> None:
        """Discard any in-progress transcription (reconnect / lease loss)."""
        if self.is_running:
            await self._send_raw({"type": "cancel"})

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # ── Transcription ─────────────────────────────────────────────────────────

    async def transcribe(
        self,
        audio_path: Path,
        output_file: Path,
        job_id: uuid.UUID,
        command_queue: asyncio.Queue,
        audio_duration: float | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> TranscriptionResult:
        """
        Send a transcription request and wait for the result.

        While waiting:
        - Polls command_queue for PAUSE_JOB / RESUME_JOB / CANCEL_JOB.
        - Sends periodic progress estimates to coordinator.
        - Detects subprocess crash and raises TranscriptionPipelineError.

        PAUSE: sends SIGSTOP to subprocess — Metal GPU state preserved.
        RESUME: sends SIGCONT — continues from exact freeze point.
        CANCEL: sends "cancel" to subprocess stdin (result discarded).
        """
        if not self.is_running:
            await self._restart("Subprocess not running before transcribe()")

        await self._send_raw({
            "type": "transcribe",
            "job_id": str(job_id),
            "audio_path": str(audio_path),
            "output_file": str(output_file),
            "language": self._language,
            "word_timestamps": True,
        })

        timer = InferenceTimer()
        is_paused = False
        last_progress_at = 0.0
        last_message_at = time.monotonic()

        # RTF estimate for progress (Mac Studio / M-series often ~0.2–0.35)
        avg_rtf = 0.28
        estimated_total = (audio_duration * avg_rtf) if audio_duration else None

        while True:
            elapsed = timer.elapsed_effective

            # ── Subprocess crash detection ────────────────────────────────────
            if not self.is_running:
                raise TranscriptionPipelineError(
                    "Transcription subprocess crashed during inference",
                    error_category="transient",
                )

            # ── Stall detection ───────────────────────────────────────────────
            if (time.monotonic() - last_message_at) > _HEARTBEAT_WARN_SECONDS:
                logger.warning(
                    "No message from subprocess in %ds — possibly stalled",
                    _HEARTBEAT_WARN_SECONDS,
                    extra={"pid": self.pid},
                )
                last_message_at = time.monotonic()  # Reset to avoid spam

            # ── Command handling ──────────────────────────────────────────────
            try:
                while True:
                    cmd = command_queue.get_nowait()
                    cmd_type = cmd.get("type") or cmd.get("command", "")

                    if cmd_type == "PAUSE_JOB" and not is_paused:
                        self._send_signal(signal.SIGSTOP)
                        timer.pause()
                        is_paused = True
                        logger.info("Pipeline paused (SIGSTOP)", extra={"pid": self.pid})

                    elif cmd_type == "RESUME_JOB" and is_paused:
                        self._send_signal(signal.SIGCONT)
                        timer.resume()
                        is_paused = False
                        logger.info("Pipeline resumed (SIGCONT)", extra={"pid": self.pid})

                    elif cmd_type == "CANCEL_JOB":
                        cmd_job = cmd.get("job_id")
                        if cmd_job is not None and str(cmd_job) != str(job_id):
                            continue
                        if is_paused:
                            self._send_signal(signal.SIGCONT)
                            timer.resume()
                        await self._send_raw({"type": "cancel"})
                        raise JobCancelledError("Job cancelled by coordinator command")

            except asyncio.QueueEmpty:
                pass

            # ── Check message queue for result ────────────────────────────────
            try:
                msg = self._message_queue.get_nowait()
                last_message_at = time.monotonic()

                if msg.get("type") == "result":
                    job_id_resp = msg.get("job_id", "")
                    cancelled = msg.get("cancelled", False)

                    if cancelled:
                        raise JobCancelledError("Job was cancelled mid-transcription")

                    msg_metrics = msg.get("metrics", {})
                    metrics = TranscriptionMetrics(
                        backend=self._backend,
                        model_path=str(self._model_path),
                        audio_duration_seconds=audio_duration or 0.0,
                        inference_seconds=timer.elapsed_effective,
                        total_wall_seconds=timer.elapsed_wall,
                        paused_seconds=timer.paused_seconds,
                        model_load_seconds=self._load_seconds
                                           if self._restart_count == 0 else 0.0,
                        segment_count=msg_metrics.get("segment_count", 0),
                        word_count=msg_metrics.get("word_count", 0),
                        language_detected=msg_metrics.get("language_detected", ""),
                        language_probability=msg_metrics.get("language_probability", 0.0),
                    )

                    if not output_file.exists():
                        raise TranscriptionPipelineError(
                            "Result file not written by subprocess",
                            error_category="transient",
                        )

                    data = json.loads(output_file.read_text(encoding="utf-8"))
                    return TranscriptionResult(
                        text=data.get("text", ""),
                        language=data.get("language", self._language),
                        segments=data.get("segments", []),
                        metrics=metrics,
                    )

                elif msg.get("type") == "error":
                    raise TranscriptionPipelineError(
                        msg.get("message", "Unknown transcription error"),
                        error_category=msg.get("category", "transient"),
                    )

                elif msg.get("type") == "heartbeat":
                    last_message_at = time.monotonic()

                elif msg.get("type") == "_eof_":
                    raise TranscriptionPipelineError(
                        "Transcription subprocess exited unexpectedly",
                        error_category="transient",
                    )

            except asyncio.QueueEmpty:
                pass

            # ── Progress reporting (time-based; MLX has no chunk callbacks) ───
            if not is_paused and estimated_total:
                if last_progress_at == 0.0 or (elapsed - last_progress_at) >= 5.0:
                    progress = max(1.0, min(95.0, (elapsed / estimated_total) * 100))
                    if on_progress:
                        try:
                            await on_progress(progress, elapsed)
                        except Exception:
                            pass
                    last_progress_at = elapsed

            await asyncio.sleep(0.2)

    # ── Private: subprocess management ───────────────────────────────────────

    async def _launch(self) -> None:
        """Start the subprocess and wait for the 'ready' message."""
        module = "agent.engine.persistent_worker"

        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", module,
            str(self._model_path),
            self._language,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        await self._cancel_io_tasks()
        self._stderr_task = asyncio.create_task(
            self._read_stderr(), name="pipeline-stderr"
        )

        # Read "ready" message with timeout
        try:
            ready_line = await asyncio.wait_for(
                self._proc.stdout.readline(),
                timeout=_STARTUP_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await self._kill_process(graceful=False)
            raise PipelineStartupError(
                f"Subprocess did not send 'ready' within {_STARTUP_TIMEOUT}s. "
                "Check that the Whisper model exists at the configured path."
            )

        if not ready_line:
            rc = await self._proc.wait()
            raise PipelineStartupError(f"Subprocess exited immediately with code {rc}")

        ready = json.loads(ready_line.decode())
        if ready.get("type") != "ready":
            raise PipelineStartupError(f"Unexpected first message from subprocess: {ready}")

        self._backend = ready.get("backend", "unknown")
        self._load_seconds = ready.get("load_seconds", 0.0)
        self._warmup_seconds = ready.get("warmup_seconds", 0.0)

        # Start background stdout reader
        self._reader_task = asyncio.create_task(
            self._read_stdout_loop(), name="pipeline-stdout"
        )

    async def _restart(self, reason: str) -> None:
        """Restart the subprocess after a crash. Raises after too many failures."""
        if self._restart_count >= _MAX_RESTARTS:
            raise TranscriptionPipelineError(
                f"Subprocess crashed {self._restart_count} times; giving up. "
                f"Last reason: {reason}",
                error_category="deterministic",
            )

        self._restart_count += 1
        logger.warning(
            "Restarting transcription subprocess (attempt %d/%d): %s",
            self._restart_count, _MAX_RESTARTS, reason,
        )

        await self._kill_process(graceful=False)
        await self._cancel_io_tasks()

        await asyncio.sleep(_RESTART_DELAY * self._restart_count)

        # Drain the message queue before restarting
        while not self._message_queue.empty():
            self._message_queue.get_nowait()

        await self._launch()
        logger.info("Subprocess restarted successfully")

    async def _read_stdout_loop(self) -> None:
        """Background task: read all stdout lines and enqueue them."""
        assert self._proc and self._proc.stdout
        try:
            async for raw_line in self._proc.stdout:
                line = raw_line.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    await self._message_queue.put(msg)
                except json.JSONDecodeError:
                    logger.debug("Non-JSON line from subprocess: %r", line[:100])
        except Exception as exc:
            logger.warning("Stdout reader error: %s", exc)
        finally:
            # Subprocess exited — put sentinel so transcribe() detects crash
            await self._message_queue.put({"type": "_eof_"})

    async def _cancel_io_tasks(self) -> None:
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._reader_task = None
        self._stderr_task = None

    async def _read_stderr(self) -> None:
        """Forward subprocess stderr to our logger (error messages, stack traces)."""
        assert self._proc and self._proc.stderr
        try:
            async for raw_line in self._proc.stderr:
                line = raw_line.decode(errors="replace").rstrip()
                if line:
                    logger.debug("[subprocess] %s", line)
        except Exception:
            pass

    async def _send_raw(self, msg: dict) -> None:
        if self._proc and self._proc.stdin and not self._proc.stdin.is_closing():
            try:
                data = (json.dumps(msg) + "\n").encode()
                self._proc.stdin.write(data)
                await self._proc.stdin.drain()
            except Exception as exc:
                logger.warning("Failed to send to subprocess: %s", exc)

    def _send_signal(self, sig: signal.Signals) -> None:
        if self._proc and self._proc.returncode is None:
            try:
                os.kill(self._proc.pid, sig)
            except ProcessLookupError:
                pass

    async def _kill_process(self, graceful: bool = True) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            if graceful:
                # Unfreeze first (SIGTERM ignored by SIGSTOP-frozen process)
                try:
                    os.kill(self._proc.pid, signal.SIGCONT)
                except ProcessLookupError:
                    return
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=self._term_timeout)
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
            else:
                os.kill(self._proc.pid, signal.SIGCONT)  # Unfreeze before kill
                self._proc.kill()
                await self._proc.wait()
        except ProcessLookupError:
            pass
