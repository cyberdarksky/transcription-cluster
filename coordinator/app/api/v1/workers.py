from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from ...core.dependencies import DbSession, WsManager
from ...core.exceptions import http_worker_not_found
from ...models.enums import WorkerStatus
from ...models.worker import Worker
from ...models.worker_metric import WorkerMetric
from ...schemas.common import PaginatedResponse
from ...schemas.worker import WorkerMetricSnapshot, WorkerRead, WorkerReadDetail

router = APIRouter(prefix="/workers", tags=["workers"])


@router.get("", response_model=PaginatedResponse[WorkerRead])
async def list_workers(
    db: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PaginatedResponse[WorkerRead]:
    from sqlalchemy import func

    stmt = (
        select(Worker)
        .order_by(
            Worker.status.asc(),  # online first
            Worker.hostname.asc(),
        )
    )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    workers = result.scalars().all()

    return PaginatedResponse(
        items=[WorkerRead.model_validate(w) for w in workers],
        total=total or 0,
        page=page,
        page_size=page_size,
        pages=max(1, -(-( total or 0) // page_size)),
    )


@router.get("/{worker_id}", response_model=WorkerReadDetail)
async def get_worker(
    worker_id: uuid.UUID,
    db: DbSession,
    metrics_limit: Annotated[int, Query(ge=1, le=1000)] = 120,
) -> WorkerReadDetail:
    worker = await db.get(Worker, worker_id)
    if worker is None:
        raise http_worker_not_found(worker_id)

    metrics_result = await db.execute(
        select(WorkerMetric)
        .where(WorkerMetric.worker_id == worker_id)
        .order_by(WorkerMetric.recorded_at.desc())
        .limit(metrics_limit)
    )
    metrics = metrics_result.scalars().all()

    detail = WorkerReadDetail.model_validate(worker)
    detail.metrics_history = [WorkerMetricSnapshot.model_validate(m) for m in reversed(metrics)]
    return detail


@router.get("/{worker_id}/metrics")
async def get_worker_metrics(
    worker_id: uuid.UUID,
    db: DbSession,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
) -> dict:
    worker = await db.get(Worker, worker_id)
    if worker is None:
        raise http_worker_not_found(worker_id)

    UTC = timezone.utc
    if to is None:
        to = datetime.now(UTC)
    if from_ is None:
        from_ = datetime(to.year, to.month, to.day, to.hour - 1,
                         to.minute, to.second, tzinfo=UTC)

    stmt = (
        select(WorkerMetric)
        .where(
            WorkerMetric.worker_id == worker_id,
            WorkerMetric.recorded_at >= from_,
            WorkerMetric.recorded_at <= to,
        )
        .order_by(WorkerMetric.recorded_at.asc())
    )
    result = await db.execute(stmt)
    metrics = result.scalars().all()

    return {
        "worker_id": str(worker_id),
        "from": from_.isoformat(),
        "to": to.isoformat(),
        "metrics": [
            {
                "time": m.recorded_at.isoformat(),
                "cpu_percent": float(m.cpu_percent) if m.cpu_percent else None,
                "memory_percent": float(m.memory_percent) if m.memory_percent else None,
                "gpu_percent": float(m.gpu_percent) if m.gpu_percent else None,
                "job_progress": float(m.job_progress_percent) if m.job_progress_percent else None,
            }
            for m in metrics
        ],
    }


@router.post("/{worker_id}/pause")
async def pause_worker(worker_id: uuid.UUID, db: DbSession, ws: WsManager) -> dict:
    """Pause a worker: it completes its current job but accepts no new ones."""
    worker = await db.get(Worker, worker_id)
    if worker is None:
        raise http_worker_not_found(worker_id)

    prev = worker.status
    worker.status = WorkerStatus.PAUSED
    await db.flush()

    await ws.broadcast_to_dashboard({
        "type": "worker_status_changed",
        "data": {
            "worker_id": str(worker_id),
            "hostname": worker.hostname,
            "previous_status": prev,
            "new_status": WorkerStatus.PAUSED,
        },
    })
    return {"worker_id": str(worker_id), "status": WorkerStatus.PAUSED}


@router.post("/{worker_id}/resume")
async def resume_worker(worker_id: uuid.UUID, db: DbSession, ws: WsManager) -> dict:
    """Resume a paused worker."""
    worker = await db.get(Worker, worker_id)
    if worker is None:
        raise http_worker_not_found(worker_id)

    prev = worker.status
    worker.status = WorkerStatus.IDLE
    await db.flush()

    await ws.broadcast_to_dashboard({
        "type": "worker_status_changed",
        "data": {
            "worker_id": str(worker_id),
            "hostname": worker.hostname,
            "previous_status": prev,
            "new_status": WorkerStatus.IDLE,
        },
    })
    return {"worker_id": str(worker_id), "status": WorkerStatus.IDLE}
