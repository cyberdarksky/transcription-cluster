from __future__ import annotations

import enum


class JobStatus(str, enum.Enum):
    """
    Full job lifecycle state machine.

    Linear progression (worker-driven):
        QUEUED → ASSIGNED → DOWNLOADING → PROCESSING → UPLOADING → COMPLETED

    Side states:
        PAUSED       — user suspended mid-PROCESSING; awaits RESUME command
        RETRY_WAIT   — transient failure; waiting for next_retry_after to pass
        FAILED       — terminal; all retries exhausted or deterministic error
        CANCELLED    — terminal; user-cancelled from dashboard

    Legacy alias (retained for backward compat; treated as QUEUED by all code):
        PENDING      — pre-v2 initial state; migrated to QUEUED on startup
    """

    QUEUED = "queued"
    ASSIGNED = "assigned"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    RETRY_WAIT = "retry_wait"
    CANCELLED = "cancelled"

    # ── Semantic groupings ────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """Job is currently held by a worker under a lease."""
        return self in (
            JobStatus.ASSIGNED,
            JobStatus.DOWNLOADING,
            JobStatus.PROCESSING,
            JobStatus.UPLOADING,
            JobStatus.PAUSED,
        )

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def is_leaseable(self) -> bool:
        """Worker must hold a valid lease while in this state."""
        return self in (
            JobStatus.ASSIGNED,
            JobStatus.DOWNLOADING,
            JobStatus.PROCESSING,
            JobStatus.UPLOADING,
        )

    @property
    def can_pause(self) -> bool:
        return self == JobStatus.PROCESSING

    @property
    def can_resume(self) -> bool:
        return self == JobStatus.PAUSED

    @property
    def can_cancel(self) -> bool:
        return not self.is_terminal

    @property
    def can_retry(self) -> bool:
        return self in (JobStatus.FAILED, JobStatus.CANCELLED)


class WorkerStatus(str, enum.Enum):
    ONLINE = "online"
    IDLE = "idle"
    BUSY = "busy"
    PAUSED = "paused"
    OFFLINE = "offline"
    ERROR = "error"

    @property
    def is_available(self) -> bool:
        return self == WorkerStatus.IDLE


class ErrorCategory(str, enum.Enum):
    TRANSIENT = "transient"
    DETERMINISTIC = "deterministic"
