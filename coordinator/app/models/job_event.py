from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .base import Base

if TYPE_CHECKING:
    from .job import Job
    from .worker import Worker


class JobEvent(Base):
    """
    Immutable audit log of job status transitions and lifecycle events.

    NOTE: 'progress' events are NOT stored here. Progress updates only
    update jobs.progress_percent (single-row UPDATE). This table records
    state transitions only, keeping write volume manageable.
    """

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    worker_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Event types: created | assigned | processing | paused | resumed
    #              completed | failed | cancelled | retried | timeout
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # Free-form event-specific payload.
    # Examples:
    #   assigned:   {"previous_status": "pending", "worker_hostname": "mac-studio-2"}
    #   failed:     {"error": "OOM", "exit_code": -9, "attempt": 2, "category": "transient"}
    #   completed:  {"audio_seconds": 1234.5, "processing_seconds": 456.7, "rtf": 0.37}
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    # ── Relationships ─────────────────────────────────────────────────────────
    job: Mapped[Job] = relationship("Job", back_populates="events", lazy="noload")
    worker: Mapped[Optional[Worker]] = relationship("Worker", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<JobEvent id={self.id} job_id={self.job_id} "
            f"type={self.event_type!r} at={self.created_at}>"
        )
