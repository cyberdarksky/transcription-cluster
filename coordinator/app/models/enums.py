from __future__ import annotations

import enum


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    PROCESSING = "processing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_active(self) -> bool:
        return self in (
            JobStatus.ASSIGNED,
            JobStatus.PROCESSING,
            JobStatus.PAUSED,
        )

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def can_pause(self) -> bool:
        return self == JobStatus.PROCESSING

    @property
    def can_resume(self) -> bool:
        return self == JobStatus.PAUSED

    @property
    def can_cancel(self) -> bool:
        return self in (
            JobStatus.PENDING,
            JobStatus.ASSIGNED,
            JobStatus.PROCESSING,
            JobStatus.PAUSED,
        )

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
