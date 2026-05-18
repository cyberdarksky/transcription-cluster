"""Build dashboard-facing API models with joined metrics and job context."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.job import Job
from ..models.worker import Worker
from ..schemas.job import JobRead
from ..schemas.worker import WorkerRead


@dataclass(frozen=True)
class _LatestMetric:
    cpu_percent: float | None
    memory_percent: float | None
    gpu_percent: float | None


async def _latest_metrics_by_worker(
    db: AsyncSession, worker_ids: list[uuid.UUID]
) -> dict[uuid.UUID, _LatestMetric]:
    if not worker_ids:
        return {}

    stmt = text("""
        SELECT DISTINCT ON (worker_id)
            worker_id,
            cpu_percent,
            memory_percent,
            gpu_percent
        FROM worker_metrics
        WHERE worker_id IN :ids
        ORDER BY worker_id, recorded_at DESC
    """).bindparams(bindparam("ids", expanding=True))
    rows = await db.execute(stmt, {"ids": worker_ids})

    out: dict[uuid.UUID, _LatestMetric] = {}
    for row in rows:
        out[row.worker_id] = _LatestMetric(
            cpu_percent=float(row.cpu_percent) if row.cpu_percent is not None else None,
            memory_percent=float(row.memory_percent) if row.memory_percent is not None else None,
            gpu_percent=float(row.gpu_percent) if row.gpu_percent is not None else None,
        )
    return out


def _hours_from_seconds(seconds: Decimal) -> float:
    return round(float(seconds) / 3600.0, 2)


async def build_worker_reads(db: AsyncSession, workers: list[Worker]) -> list[WorkerRead]:
    if not workers:
        return []

    job_ids = [w.current_job_id for w in workers if w.current_job_id]
    jobs_by_id: dict[uuid.UUID, Job] = {}
    if job_ids:
        result = await db.execute(select(Job).where(Job.id.in_(job_ids)))
        jobs_by_id = {j.id: j for j in result.scalars().all()}

    metrics = await _latest_metrics_by_worker(db, [w.id for w in workers])

    reads: list[WorkerRead] = []
    for worker in workers:
        base = WorkerRead.model_validate(worker)
        job = jobs_by_id.get(worker.current_job_id) if worker.current_job_id else None
        m = metrics.get(worker.id)
        progress = None
        if job is not None and job.progress_percent is not None:
            progress = float(job.progress_percent)

        reads.append(
            base.model_copy(
                update={
                    "current_job_path": job.input_path if job else None,
                    "current_job_progress": progress,
                    "total_audio_hours": _hours_from_seconds(worker.total_audio_seconds),
                    "last_cpu_percent": m.cpu_percent if m else None,
                    "last_memory_percent": m.memory_percent if m else None,
                    "last_gpu_percent": m.gpu_percent if m else None,
                }
            )
        )
    return reads


async def build_worker_read(db: AsyncSession, worker: Worker) -> WorkerRead:
    return (await build_worker_reads(db, [worker]))[0]


async def build_job_reads(rows: list[tuple[Job, str | None]]) -> list[JobRead]:
    reads: list[JobRead] = []
    for job, hostname in rows:
        base = JobRead.model_validate(job)
        reads.append(base.model_copy(update={"worker_hostname": hostname}))
    return reads
