from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .base import Base
from .enums import WorkerStatus

if TYPE_CHECKING:
    from .job import Job
    from .job_event import JobEvent
    from .worker_metric import WorkerMetric


class Worker(Base):
    __tablename__ = "workers"

    # ── Identity ──────────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    # Stable identity generated once at install time and stored in
    # ~/.transcription-worker/worker-id. Does not change with VPN/Docker/hardware swap.
    stable_worker_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=True, index=True
    )
    hostname: Mapped[str] = mapped_column(Text, nullable=False)
    mac_address: Mapped[str] = mapped_column(String(17), nullable=False, unique=True)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    api_port: Mapped[int] = mapped_column(Integer, nullable=False, default=8081)

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[WorkerStatus] = mapped_column(
        String(20),
        nullable=False,
        default=WorkerStatus.OFFLINE,
    )

    # ── Hardware capabilities (filled on registration) ────────────────────────
    cpu_model: Mapped[Optional[str]] = mapped_column(Text)
    cpu_cores: Mapped[Optional[int]] = mapped_column(Integer)
    memory_total_gb: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    gpu_model: Mapped[Optional[str]] = mapped_column(Text)
    whisper_backend: Mapped[str] = mapped_column(
        String(50), nullable=False, default="mlx-whisper"
    )
    worker_version: Mapped[Optional[str]] = mapped_column(String(20))

    # ── Heartbeat ─────────────────────────────────────────────────────────────
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(
        "last_heartbeat", nullable=True
    )
    heartbeat_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    # CONSTRAINT: heartbeat_timeout_seconds >= 3 * heartbeat_interval_seconds.
    # This invariant is validated at the application layer via system_settings.

    # ── Current job (denormalized for query convenience) ──────────────────────
    # FK uses use_alter so it can be created after both tables exist.
    current_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL", use_alter=True, name="fk_workers_current_job"),
        nullable=True,
    )

    # ── Lifetime statistics ───────────────────────────────────────────────────
    jobs_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_audio_seconds: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0")
    )
    total_processing_seconds: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0")
    )
    # RTF = processing_time / audio_duration; <1.0 means faster than real-time
    average_rtf: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4))

    # ── Timestamps ────────────────────────────────────────────────────────────
    registered_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    current_job: Mapped[Optional[Job]] = relationship(
        "Job",
        foreign_keys=[current_job_id],
        lazy="noload",
    )
    jobs: Mapped[list[Job]] = relationship(
        "Job",
        foreign_keys="Job.worker_id",
        back_populates="worker",
        lazy="noload",
    )
    metrics: Mapped[list[WorkerMetric]] = relationship(
        "WorkerMetric",
        back_populates="worker",
        lazy="noload",
        cascade="all, delete-orphan",
    )
    events: Mapped[list[JobEvent]] = relationship(
        "JobEvent",
        foreign_keys="JobEvent.worker_id",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return f"<Worker id={self.id} hostname={self.hostname!r} status={self.status}>"
