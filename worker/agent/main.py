"""
Worker agent entry point.

Lifecycle:
  1. Startup: logging, config, stable ID, cleanup leftover temp files.
  2. Discovery: find coordinator via mDNS or environment variable.
  3. Registration: register with coordinator (idempotent, handles reconnects).
  4. Background tasks: heartbeat, WebSocket client.
  5. Job loop: claim → run → cleanup → repeat.
  6. Graceful shutdown: finish current job or report failure, cleanup, exit.

Reconnect strategy:
  Network failures in the job loop (failed claim, failed advance_state, etc.)
  are caught and retried with exponential backoff. The worker never exits due
  to coordinator unavailability — it keeps retrying until the coordinator comes
  back.

Signal handling:
  SIGTERM → graceful shutdown (finishes current job then exits).
  SIGINT  → same as SIGTERM.

Resume safety:
  On restart, the worker re-registers with current_job_id=None (it cleaned up
  temp files from the crashed run). The coordinator's lease recovery service
  will have already re-queued any in-progress jobs (within 30–90 seconds of
  the crash). The worker simply picks up the next available job.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
import socket
import sys
import uuid

from urllib.parse import urlparse

from .cleanup import cleanup_job, cleanup_on_startup
from .config import WorkerConfig, get_or_create_stable_worker_id
from .coordinator_client import CoordinatorClient
from .discovery import CoordinatorNotFoundError, discover_coordinator
from .engine import PipelineStartupError, TranscriptionPipeline
from .heartbeat import HeartbeatService
from .job_runner import JobRunner
from .logging_config import setup_logging
from .metrics import get_hardware_info
from .state import WorkerRunStatus, WorkerState
from .websocket_client import WorkerWebSocketClient

logger = logging.getLogger(__name__)

WORKER_VERSION = "1.0.0"


def _make_ws_base_url(coordinator_websocket_url: str) -> str:
    """
    Extract scheme+host+port from the coordinator's WebSocket URL.

    BUG-FIX: str.rstrip("/ws/worker") strips individual characters, not the
    literal suffix string. For port 8080:
      "ws://192.168.1.101:8080/ws/worker".rstrip("/ws/worker")
    stops at '0' (not in the charset) → yields "ws://192.168.1.101:808".

    Correct: parse the URL and reconstruct the base without its path.
    """
    url = coordinator_websocket_url.replace("http://", "ws://").replace("https://", "wss://")
    parsed = urlparse(url)
    scheme = "wss" if parsed.scheme in ("https", "wss") else "ws"
    return f"{scheme}://{parsed.netloc}"


# ── Reconnect strategy ────────────────────────────────────────────────────────

class ReconnectStrategy:
    """Exponential backoff with jitter for coordinator reconnections."""

    def __init__(
        self,
        base: float = 5.0,
        maximum: float = 120.0,
        multiplier: float = 2.0,
        jitter: float = 0.2,
    ) -> None:
        self._base = base
        self._max = maximum
        self._multiplier = multiplier
        self._jitter = jitter
        self._attempt = 0

    def reset(self) -> None:
        self._attempt = 0

    def next_delay(self) -> float:
        delay = min(self._base * (self._multiplier ** self._attempt), self._max)
        jitter_amount = delay * self._jitter * (random.random() * 2 - 1)
        self._attempt += 1
        return max(0.5, delay + jitter_amount)


# ── Job loop ──────────────────────────────────────────────────────────────────

async def job_loop(
    client: CoordinatorClient,
    runner: JobRunner,
    state: WorkerState,
    config: WorkerConfig,
) -> None:
    """
    Main job loop: poll for jobs, run them, repeat until stopped.

    Transient errors (network failures, coordinator temporarily unreachable)
    are retried with backoff. The loop only exits when state.stop_event is set.
    """
    backoff = ReconnectStrategy(
        base=config.reconnect_base_delay_seconds,
        maximum=config.reconnect_max_delay_seconds,
    )

    while not state.is_stopping():
        # ── Worker paused (draining) ───────────────────────────────────────────
        if state.run_status == WorkerRunStatus.DRAINING:
            await asyncio.sleep(config.job_poll_interval_seconds)
            continue

        try:
            # ── Claim next job ─────────────────────────────────────────────────
            job = await client.claim_next_job(state.worker_id)

            if job is None:
                # Queue is empty — wait and retry
                backoff.reset()
                state.run_status = WorkerRunStatus.IDLE
                await asyncio.sleep(config.job_poll_interval_seconds)
                continue

            backoff.reset()
            logger.info(
                "Job claimed",
                extra={"job_id": str(job.job_id), "path": job.input_path},
            )

            # ── Run the job ────────────────────────────────────────────────────
            await runner.run_job(job)

        except asyncio.CancelledError:
            break

        except Exception as exc:
            if state.is_stopping():
                break

            delay = backoff.next_delay()
            logger.warning(
                "Job loop error; retrying in %.1fs: %s",
                delay, exc,
                extra={"error_type": type(exc).__name__},
            )
            await asyncio.sleep(delay)

    logger.info("Job loop exited")


# ── Registration with retry ───────────────────────────────────────────────────

async def register_worker(
    client: CoordinatorClient,
    stable_id: uuid.UUID,
    state: WorkerState,
    config: WorkerConfig,
    pipeline: TranscriptionPipeline | None = None,
    current_job_id: uuid.UUID | None = None,
    current_job_status: str | None = None,
) -> None:
    """Register with the coordinator; retry indefinitely on failure."""
    hw = get_hardware_info()
    backoff = ReconnectStrategy(base=5.0, maximum=60.0)

    while True:
        try:
            resp = await client.register(
                stable_worker_id=stable_id,
                mac_address=hw.get("mac_address", "00:00:00:00:00:00"),
                hostname=hw.get("hostname", socket.gethostname()),
                ip_address=hw.get("ip_address", "127.0.0.1"),
                cpu_model=hw.get("cpu_model"),
                cpu_cores=hw.get("cpu_cores"),
                memory_total_gb=hw.get("memory_total_gb"),
                gpu_model=hw.get("gpu_model"),
                current_job_id=current_job_id,
                current_job_status=current_job_status,
            )

            state.worker_id = resp.worker_id
            state.coordinator_url = client.base_url

            logger.info(
                "Registered with coordinator",
                extra={
                    "worker_id": str(resp.worker_id),
                    "recovery_grace": resp.recovery_grace_active,
                    "cancel_job": resp.cancel_current_job,
                },
            )

            if resp.cancel_current_job:
                logger.warning(
                    "Coordinator rejected in-flight job; cancelling local work",
                    extra={"job_id": str(state.current_job_id) if state.current_job_id else None},
                )
                if state.current_job_id is not None:
                    cleanup_job(config.temp_dir, state.current_job_id)
                if pipeline is not None:
                    await pipeline.cancel_inflight()
                state.set_idle()

            if resp.recovery_grace_active:
                logger.info(
                    "Coordinator is in recovery grace period; will wait before requesting jobs"
                )
                # The job loop will wait naturally (poll interval)

            return resp

        except Exception as exc:
            delay = backoff.next_delay()
            logger.warning(
                "Registration failed; retrying in %.1fs: %s", delay, exc
            )
            await asyncio.sleep(delay)


# ── Graceful shutdown ─────────────────────────────────────────────────────────

def setup_signal_handlers(state: WorkerState, loop: asyncio.AbstractEventLoop) -> None:
    """Install SIGTERM and SIGINT handlers for graceful shutdown."""
    def handle_shutdown(signum: int, _: object) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("Received %s — initiating graceful shutdown", sig_name)
        loop.call_soon_threadsafe(state.request_stop)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)


# ── Main entry point ──────────────────────────────────────────────────────────

async def async_main() -> None:
    config = WorkerConfig()
    setup_logging(log_level=config.log_level, json_logs=config.json_logs)

    logger.info(
        "Worker agent starting",
        extra={"version": WORKER_VERSION, "python": sys.version.split()[0]},
    )

    # ── Validate local model bundle (offline — no HuggingFace download) ───────
    from .model_store import ModelBundleError, validate_model_bundle

    try:
        bundle = validate_model_bundle(config.model_path, strict_manifest=False)
    except ModelBundleError as exc:
        logger.error(
            "Model bundle invalid: %s. Run install-worker.sh to install the model.",
            exc,
        )
        sys.exit(1)
    config.model_path = bundle.resolved_path

    # ── State ─────────────────────────────────────────────────────────────────
    state = WorkerState()
    loop = asyncio.get_running_loop()
    setup_signal_handlers(state, loop)

    # ── Stable ID ─────────────────────────────────────────────────────────────
    stable_id = get_or_create_stable_worker_id(config)
    logger.info("Stable worker ID: %s", stable_id)

    # ── Startup cleanup ───────────────────────────────────────────────────────
    removed = cleanup_on_startup(config.temp_dir)
    if removed:
        logger.info("Cleaned up %d leftover temp directories from crashed run", removed)

    # ── Discover coordinator ──────────────────────────────────────────────────
    coordinator_url = None
    discovery_backoff = ReconnectStrategy(base=10.0, maximum=120.0)
    while coordinator_url is None and not state.is_stopping():
        try:
            coordinator_url = await discover_coordinator(config)
        except CoordinatorNotFoundError as exc:
            delay = discovery_backoff.next_delay()
            logger.warning("%s. Retrying in %.0fs...", exc, delay)
            await asyncio.sleep(delay)

    if state.is_stopping():
        return

    # ── Create HTTP client ────────────────────────────────────────────────────
    client = CoordinatorClient(coordinator_url, WORKER_VERSION)
    hw = get_hardware_info()
    hostname = hw.get("hostname", socket.gethostname())

    # Initialize to None — finally block must be safe even if startup fails
    heartbeat_svc: HeartbeatService | None = None
    ws_client: WorkerWebSocketClient | None = None
    pipeline: TranscriptionPipeline | None = None

    try:
        # ── Transcription pipeline (model loads ONCE here) ────────────────────
        # This is the key optimization: the Whisper model is loaded into Apple
        # Silicon unified memory once and stays warm for the entire worker lifetime.
        # Subsequent jobs skip the 15-30s model load cost entirely.
        logger.info("Loading transcription model (this may take up to 30s)...")
        pipeline = TranscriptionPipeline(
            model_path=config.model_path,
            language="tr",
            term_timeout=config.subprocess_term_timeout_seconds,
        )
        try:
            await pipeline.start()
        except PipelineStartupError as exc:
            logger.error("Failed to start transcription pipeline: %s", exc)
            sys.exit(1)

        # ── Registration ──────────────────────────────────────────────────────
        reg_resp = await register_worker(
            client, stable_id, state, config, pipeline=pipeline
        )
        state.run_status = WorkerRunStatus.IDLE

        # ── Background: heartbeat ─────────────────────────────────────────────
        heartbeat_svc = HeartbeatService(
            client=client,
            worker_id=state.worker_id,
            state=state,
            interval_seconds=reg_resp.heartbeat_interval_seconds,
        )
        await heartbeat_svc.start()

        # ── Background: WebSocket client ──────────────────────────────────────
        ws_base = _make_ws_base_url(reg_resp.websocket_url)
        ws_client = WorkerWebSocketClient(
            base_ws_url=ws_base,
            worker_id=state.worker_id,
            state=state,
            config=config,
        )
        await ws_client.start()

        # ── Job runner ────────────────────────────────────────────────────────
        runner = JobRunner(
            client=client,
            config=config,
            state=state,
            worker_id=state.worker_id,
            hostname=hostname,
            pipeline=pipeline,  # Shared; model stays warm across jobs
        )

        # ── Main job loop ─────────────────────────────────────────────────────
        logger.info(
            "Worker ready — entering job loop",
            extra={"backend": pipeline.backend},
        )
        await job_loop(client, runner, state, config)

    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down...")
        state.request_stop()

        if pipeline is not None and state.current_job_id is not None:
            logger.info(
                "Cancelling in-flight job before shutdown",
                extra={"job_id": str(state.current_job_id)},
            )
            try:
                await state.command_queue.put({
                    "type": "CANCEL_JOB",
                    "job_id": str(state.current_job_id),
                })
                await pipeline.cancel_inflight()
            except Exception:
                logger.exception("Failed to cancel in-flight job during shutdown")

        if heartbeat_svc is not None:
            await heartbeat_svc.stop()
        if ws_client is not None:
            await ws_client.stop()
        if pipeline is not None:
            await pipeline.stop()
        await client.aclose()
        logger.info("Worker agent stopped cleanly")


def main() -> None:
    """Synchronous entry point (called by the `transcription-worker` command)."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
