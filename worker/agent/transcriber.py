"""
Transcriber — runs mlx-whisper in a subprocess with pause/resume support.

Why subprocess?
  SIGSTOP / SIGCONT require a separate OS process. The parent sends
  os.kill(pid, SIGSTOP) to freeze the inference and SIGCONT to resume.
  Metal GPU tensor state is preserved in memory with zero overhead.

Pipe deadlock fix:
  The original design wrote the transcript JSON to stdout (PIPE). For a
  2-hour recording the JSON can exceed 5 MB — far larger than the ~64 KB
  macOS pipe buffer. The subprocess would block waiting for the parent to
  drain the pipe; the parent would block waiting for returncode to become
  non-None. Classic deadlock.

  Fix: the subprocess writes its JSON result to a temp file. stdout only
  carries a tiny 2-byte "OK" sentinel to signal success. The temp file is
  read after the process exits. No pipe buffer limits.

Kill correctness fix:
  The original _kill_subprocess called asyncio.sleep(5) unconditionally
  after terminate(). Most processes exit in <100ms; the 5-second sleep
  blocked everything. Now we use asyncio.wait_for(proc.wait()) — we wait
  only as long as needed, up to term_timeout.

Pause timing fix:
  Hard timeout now tracks wall-clock time minus total paused duration, so
  a paused job is not penalised for the time it spent frozen.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# ── Subprocess script ─────────────────────────────────────────────────────────
# argv[5] = output file path. Result JSON written here (avoids pipe deadlock).
# stdout carries only a 2-byte sentinel "OK" — safely within the pipe buffer.

_TRANSCRIPTION_SCRIPT = """\
import json, sys
import mlx_whisper

audio_path  = sys.argv[1]
model_path  = sys.argv[2]
language    = sys.argv[3]
word_ts     = sys.argv[4].lower() == "true"
output_file = sys.argv[5]

result = mlx_whisper.transcribe(
    audio_path,
    path_or_hf_repo=model_path,
    language=language,
    word_timestamps=word_ts,
    verbose=False,
    fp16=False,
)

output = {
    "text": result.get("text", ""),
    "language": result.get("language", language),
    "segments": [],
}
for seg in result.get("segments", []):
    s = {
        "id":             seg.get("id", 0),
        "start":          round(float(seg.get("start", 0)), 3),
        "end":            round(float(seg.get("end", 0)), 3),
        "text":           seg.get("text", "").strip(),
        "avg_logprob":    round(float(seg.get("avg_logprob", 0)), 4),
        "no_speech_prob": round(float(seg.get("no_speech_prob", 0)), 4),
        "words":          [],
    }
    for w in seg.get("words", []):
        s["words"].append({
            "word":        w.get("word", ""),
            "start":       round(float(w.get("start", 0)), 3),
            "end":         round(float(w.get("end", 0)), 3),
            "probability": round(float(w.get("probability", 1.0)), 4),
        })
    output["segments"].append(s)

with open(output_file, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False)

# Tiny sentinel on stdout — signals success without pipe-buffer risk
print("OK", flush=True)
"""


@dataclass
class TranscriptionResult:
    text: str
    language: str
    segments: list[dict[str, Any]]

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    @property
    def word_count(self) -> int:
        return sum(len(s.get("words", [])) for s in self.segments)


class TranscriberError(RuntimeError):
    """Raised when transcription fails. error_category drives retry logic."""

    def __init__(self, message: str, error_category: str = "transient") -> None:
        super().__init__(message)
        self.error_category = error_category


class JobCancelledError(RuntimeError):
    """Raised when the job is cancelled mid-transcription."""


# Type alias for the progress callback
ProgressCallback = Callable[[float, float], Awaitable[None]]


class Transcriber:
    """
    Manages the mlx-whisper subprocess lifecycle.

    Usage:
        async with Transcriber(audio_path, model_path, ..., term_timeout=5.0) as t:
            result = await t.run(command_queue=state.command_queue,
                                 on_progress=on_progress_callback)
    """

    def __init__(
        self,
        audio_path: Path,
        model_path: Path,
        language: str,
        word_timestamps: bool,
        audio_duration: float | None,
        max_duration_seconds: int | None = None,
        average_rtf: float = 0.40,
        term_timeout: float = 5.0,
    ) -> None:
        self._audio_path = audio_path
        self._model_path = model_path
        self._language = language
        self._word_timestamps = word_timestamps
        self._audio_duration = audio_duration
        self._max_duration = max_duration_seconds
        self._average_rtf = average_rtf
        self._term_timeout = term_timeout
        self._proc: asyncio.subprocess.Process | None = None
        # Temp file for subprocess JSON output (avoids pipe deadlock)
        self._output_file: Path = audio_path.parent / "_whisper_output.json"

    async def __aenter__(self) -> "Transcriber":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._kill_subprocess()
        # Clean up temp output file
        self._output_file.unlink(missing_ok=True)

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    # ── Main transcription entry point ────────────────────────────────────────

    async def run(
        self,
        command_queue: asyncio.Queue,
        on_progress: ProgressCallback | None = None,
    ) -> TranscriptionResult:
        """
        Start the mlx-whisper subprocess and wait for completion.

        Polls command_queue for PAUSE_JOB / RESUME_JOB / CANCEL_JOB while waiting.

        The subprocess writes its result to a temp file. stdout only carries
        a tiny sentinel — no pipe buffer deadlock possible.

        Raises:
            TranscriberError: subprocess failed.
            JobCancelledError: CANCEL_JOB command received.
        """
        # Ensure clean state
        self._output_file.unlink(missing_ok=True)

        python_exe = sys.executable
        self._proc = await asyncio.create_subprocess_exec(
            python_exe, "-c", _TRANSCRIPTION_SCRIPT,
            str(self._audio_path),
            str(self._model_path),
            self._language,
            str(self._word_timestamps).lower(),
            str(self._output_file),
            # stdout: only "OK" sentinel — no pipe buffer risk
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        pid = self._proc.pid
        logger.info(
            "Transcription subprocess started",
            extra={
                "pid": pid,
                "audio": str(self._audio_path),
                "model": str(self._model_path),
            },
        )

        start_time = time.monotonic()
        last_progress_report = 0.0
        is_paused = False

        # Track time spent paused so it's not charged against the hard timeout.
        paused_since: float | None = None
        total_paused_seconds: float = 0.0

        # Hard timeout in effective (non-paused) seconds
        hard_timeout = self._max_duration or (
            int(self._audio_duration * 10) if self._audio_duration else 7200
        )
        estimated_total = (
            self._audio_duration * self._average_rtf
            if self._audio_duration and self._audio_duration > 0
            else None
        )

        while self._proc.returncode is None:
            now = time.monotonic()
            elapsed_wall = now - start_time

            # Effective elapsed = wall time minus total time spent paused
            if paused_since is not None:
                effective_elapsed = elapsed_wall - total_paused_seconds - (now - paused_since)
            else:
                effective_elapsed = elapsed_wall - total_paused_seconds

            # ── Hard timeout (only counts non-paused time) ─────────────────────
            if effective_elapsed > hard_timeout:
                await self._kill_subprocess()
                raise TranscriberError(
                    f"Job timed out after {effective_elapsed:.0f}s effective processing "
                    f"(limit: {hard_timeout}s)",
                    error_category="transient",
                )

            # ── Command processing ─────────────────────────────────────────────
            try:
                while True:
                    cmd = command_queue.get_nowait()
                    cmd_type = cmd.get("type", "")

                    if cmd_type == "PAUSE_JOB" and not is_paused:
                        self._send_signal(signal.SIGSTOP)
                        is_paused = True
                        paused_since = time.monotonic()
                        logger.info("Transcription paused (SIGSTOP)", extra={"pid": pid})

                    elif cmd_type == "RESUME_JOB" and is_paused:
                        self._send_signal(signal.SIGCONT)
                        is_paused = False
                        if paused_since is not None:
                            total_paused_seconds += time.monotonic() - paused_since
                            paused_since = None
                        logger.info("Transcription resumed (SIGCONT)", extra={"pid": pid})

                    elif cmd_type == "CANCEL_JOB":
                        if is_paused:
                            self._send_signal(signal.SIGCONT)  # Unfreeze before kill
                        await self._kill_subprocess()
                        raise JobCancelledError("Job cancelled by coordinator command")

            except asyncio.QueueEmpty:
                pass

            # ── Progress reporting (only when active — not paused) ─────────────
            if (
                not is_paused
                and estimated_total
                and (effective_elapsed - last_progress_report) >= 10.0
            ):
                progress = min(95.0, (effective_elapsed / estimated_total) * 100)
                if on_progress:
                    try:
                        await on_progress(progress, effective_elapsed)
                    except Exception:
                        pass  # Non-critical
                last_progress_report = effective_elapsed

            await asyncio.sleep(0.5)

        # ── Process finished: collect output ──────────────────────────────────
        # communicate() is safe here: stdout only has "OK\n" (tiny), stderr
        # has error messages if any. Neither can deadlock.
        stdout_bytes, stderr_bytes = await self._proc.communicate()
        exit_code = self._proc.returncode

        if exit_code != 0:
            err_text = stderr_bytes.decode(errors="replace")[:500]
            category = _classify_exit(exit_code, err_text)
            raise TranscriberError(
                f"mlx-whisper exited with code {exit_code}: {err_text}",
                error_category=category,
            )

        # ── Read result from temp file ────────────────────────────────────────
        if not self._output_file.exists():
            raise TranscriberError(
                "mlx-whisper produced no output file — unexpected exit",
                error_category="transient",
            )

        try:
            raw_output = self._output_file.read_text(encoding="utf-8")
            data = json.loads(raw_output)
        except (OSError, json.JSONDecodeError) as exc:
            raise TranscriberError(
                f"Could not parse mlx-whisper output: {exc}",
                error_category="transient",
            ) from exc

        elapsed_total = time.monotonic() - start_time
        logger.info(
            "Transcription complete",
            extra={
                "pid": pid,
                "elapsed_seconds": round(elapsed_total, 1),
                "paused_seconds": round(total_paused_seconds, 1),
                "segments": len(data.get("segments", [])),
            },
        )

        return TranscriptionResult(
            text=data.get("text", ""),
            language=data.get("language", self._language),
            segments=data.get("segments", []),
        )

    # ── Signal helpers ────────────────────────────────────────────────────────

    def _send_signal(self, sig: signal.Signals) -> None:
        """Send a signal to the subprocess; silent if already exited."""
        if self._proc and self._proc.returncode is None:
            try:
                os.kill(self._proc.pid, sig)
            except ProcessLookupError:
                pass

    async def _kill_subprocess(self) -> None:
        """
        Terminate the subprocess gracefully, then forcibly if needed.

        SIGSTOP fix: if the process is currently paused (SIGSTOP), it cannot
        receive SIGTERM until SIGCONT is sent first. We unconditionally send
        SIGCONT before SIGTERM so the process can exit.

        Timing fix: original implementation slept for 5 seconds unconditionally.
        Now we wait for actual exit (up to term_timeout), then SIGKILL.
        """
        if self._proc is None or self._proc.returncode is not None:
            return

        pid = self._proc.pid
        try:
            # Unfreeze first (SIGTERM is ignored by a SIGSTOP-frozen process)
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                return

            self._proc.terminate()

            try:
                await asyncio.wait_for(self._proc.wait(), timeout=self._term_timeout)
                logger.debug("Subprocess exited after SIGTERM", extra={"pid": pid})
            except asyncio.TimeoutError:
                logger.warning(
                    "Subprocess did not exit after SIGTERM; sending SIGKILL",
                    extra={"pid": pid},
                )
                self._proc.kill()
                await self._proc.wait()

        except ProcessLookupError:
            pass  # Already exited between our check and kill


def _classify_exit(exit_code: int, err_text: str) -> str:
    """
    Classify a non-zero subprocess exit into 'transient' or 'deterministic'.

    transient   → retrying makes sense (OOM, SIGKILL, SIGTERM, disk full)
    deterministic → retrying is futile (corrupt audio, format not supported)
    """
    # Memory-related signals → always transient
    if exit_code in (-9, -11):  # SIGKILL, SIGSEGV
        return "transient"

    # Graceful termination by coordinator → transient
    if exit_code == -15:  # SIGTERM
        return "transient"

    # Content-level errors detected from stderr text
    if any(pat in err_text for pat in (
        "Invalid audio", "No such file", "AudioFileError",
        "cannot read", "unsupported format",
    )):
        return "deterministic"

    # Exit code 1 from the transcription script = unhandled Python exception.
    # This could be either (OOM manifesting as Python exception, or truly bad file).
    # Default to transient to allow one retry; deterministic overrides above win.
    return "transient"
