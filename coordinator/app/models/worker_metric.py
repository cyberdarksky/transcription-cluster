from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .base import Base

if TYPE_CHECKING:
    from .worker import Worker


class WorkerMetric(Base):
    """
    Time-series worker metrics sampled on every heartbeat.
    Retention: configurable (default 7 days); cleaned up by daily maintenance task.
    """

    __tablename__ = "worker_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    worker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workers.id", ondelete="CASCADE"),
        nullable=False,
    )
    recorded_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    # ── System metrics ────────────────────────────────────────────────────────
    cpu_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    memory_used_gb: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    memory_total_gb: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    memory_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))

    # ── GPU metrics (Apple Silicon via ioreg — no sudo required) ─────────────
    gpu_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    gpu_memory_used_gb: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))

    # ── Job context ───────────────────────────────────────────────────────────
    current_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    job_progress_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))

    # ── Relationships ─────────────────────────────────────────────────────────
    worker: Mapped[Worker] = relationship("Worker", back_populates="metrics", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<WorkerMetric worker={self.worker_id} "
            f"cpu={self.cpu_percent}% at={self.recorded_at}>"
        )
