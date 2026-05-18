from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .base import Base
from .enums import ErrorCategory, JobStatus

if TYPE_CHECKING:
    from .job_event import JobEvent
    from .worker import Worker


class Job(Base):
    __tablename__ = "jobs"

    # ── Identity ──────────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )

    # ── File information ──────────────────────────────────────────────────────
    # Relative path from the input base directory e.g. "ProjectA/meeting.mp3"
    input_path: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    # Relative folder for output hierarchy preservation e.g. "ProjectA"
    relative_folder: Mapped[str] = mapped_column(Text, nullable=False, default="")
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    # MD5 hex digest; 32 chars. Used for duplicate detection.
    file_hash: Mapped[Optional[str]] = mapped_column(String(32), index=True)

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[JobStatus] = mapped_column(
        String(20),
        nullable=False,
        default=JobStatus.PENDING,
        index=True,
    )

    # ── Assignment ────────────────────────────────────────────────────────────
    worker_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Priority (higher = processed first) ───────────────────────────────────
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)

    # ── Retry logic ───────────────────────────────────────────────────────────
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    # 'transient'     → retry applies (OOM, network, worker crash)
    # 'deterministic' → fail immediately (corrupt MP3, unsupported format)
    error_category: Mapped[Optional[ErrorCategory]] = mapped_column(
        String(15), nullable=True
    )
    # Delayed retry: job is pending but not claimable until this time passes.
    next_retry_after: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # ── Job timeout (infinite-loop protection) ────────────────────────────────
    # None → use audio_duration * job_timeout_multiplier from system_settings.
    max_job_duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)

    # ── Progress (live-updated during processing) ─────────────────────────────
    progress_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )
    assigned_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    paused_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # ── Results (populated on completion) ────────────────────────────────────
    # Relative to output base directory.
    output_srt_path: Mapped[Optional[str]] = mapped_column(Text)
    output_json_path: Mapped[Optional[str]] = mapped_column(Text)
    # MD5 of each output file for integrity verification.
    output_srt_hash: Mapped[Optional[str]] = mapped_column(String(32))
    output_json_hash: Mapped[Optional[str]] = mapped_column(String(32))
    audio_duration_seconds: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    processing_time_seconds: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    # Real-Time Factor: processing_time / audio_duration; <1.0 = faster than real-time
    rtf: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4))

    # ── Relationships ─────────────────────────────────────────────────────────
    worker: Mapped[Optional[Worker]] = relationship(
        "Worker",
        foreign_keys=[worker_id],
        back_populates="jobs",
        lazy="noload",
    )
    events: Mapped[list[JobEvent]] = relationship(
        "JobEvent",
        back_populates="job",
        order_by="JobEvent.created_at",
        lazy="noload",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Job id={self.id} path={self.input_path!r} status={self.status}>"
