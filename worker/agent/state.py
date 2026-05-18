"""
WorkerState — the single source of mutable worker runtime state.

All asyncio tasks share this object. Mutations are safe because asyncio
is single-threaded (cooperative scheduling). The transcription PID is the
one field used from a different OS context (signal handlers), but signal
handlers in Python are always deferred to the main thread loop, so no
race is possible.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum


class WorkerRunStatus(str, Enum):
    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"        # Processing a job
    PAUSED = "paused"    # Current job is paused (user command)
    DRAINING = "draining"  # Finishing current job then stopping (worker paused)
    STOPPING = "stopping"  # Graceful shutdown in progress


@dataclass
class WorkerState:
    """Single shared state object for all worker tasks."""

    # ── Identity ──────────────────────────────────────────────────────────────
    worker_id: uuid.UUID | None = None
    coordinator_url: str | None = None

    # ── Runtime status ────────────────────────────────────────────────────────
    run_status: WorkerRunStatus = WorkerRunStatus.STARTING
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)

    # ── Current job ───────────────────────────────────────────────────────────
    current_job_id: uuid.UUID | None = None
    current_job_path: str | None = None
    job_progress_percent: float = 0.0
    audio_duration_seconds: float | None = None

    # ── Subprocess (transcription subprocess PID) ─────────────────────────────
    # Set by Transcriber; read by signal handlers and command processor.
    transcription_pid: int | None = None

    # ── Command queue (WebSocket → job runner) ────────────────────────────────
    # Coordinator pushes commands (PAUSE_JOB, RESUME_JOB, CANCEL_JOB) here.
    command_queue: asyncio.Queue[dict] = field(default_factory=asyncio.Queue)

    # ── Cancel / pause signals ────────────────────────────────────────────────
    cancel_requested: bool = False  # Set when CANCEL_JOB received
    pause_requested: bool = False   # Set when PAUSE_JOB received; cleared on RESUME

    def request_stop(self) -> None:
        """Signal all tasks to stop cleanly (called on SIGTERM)."""
        self.run_status = WorkerRunStatus.STOPPING
        self.stop_event.set()

    def is_stopping(self) -> bool:
        return self.stop_event.is_set()

    def set_idle(self) -> None:
        self.run_status = WorkerRunStatus.IDLE
        self.current_job_id = None
        self.current_job_path = None
        self.job_progress_percent = 0.0
        self.audio_duration_seconds = None
        self.transcription_pid = None
        self.cancel_requested = False
        self.pause_requested = False

    def set_busy(self, job_id: uuid.UUID, job_path: str) -> None:
        self.run_status = WorkerRunStatus.BUSY
        self.current_job_id = job_id
        self.current_job_path = job_path
        self.job_progress_percent = 0.0
        self.cancel_requested = False
        self.pause_requested = False
