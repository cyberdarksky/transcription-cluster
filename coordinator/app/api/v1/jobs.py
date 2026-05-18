from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ...core.dependencies import DbSession, WsManager
from ...core.exceptions import http_invalid_transition, http_job_not_found
from ...models.enums import JobStatus
from ...models.job import Job
from ...models.worker import Worker
from ...models.job_event import JobEvent
from ...schemas.common import PaginatedResponse
from ...schemas.job import (
    JobEventRead,
    JobPauseResponse,
    JobRead,
    JobReadDetail,
    JobRetryResponse,
)
from ...queue.distributed_queue import distributed_queue as _svc
from ..read_models import build_job_reads

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_job_or_404(job_id: uuid.UUID, db: DbSession) -> Job:
    """Fetch a job by PK or raise 404."""
    job = await db.get(Job, job_id)
    if job is None:
        raise http_job_not_found(job_id)
    return job


async def _worker_hostname(db: DbSession, worker_id: uuid.UUID | None) -> str | None:
    if worker_id is None:
        return None
    worker = await db.get(Worker, worker_id)
    return worker.hostname if worker else None


async def _handle_transition(
    job: Job | None,
    job_id: uuid.UUID,
    target_status: str,
    db: DbSession,
) -> Job:
    """
    Handle the common pattern: service returned None → check if job exists →
    raise 404 or 409.  Eliminates the 4× repeated error handling block.
    """
    if job is not None:
        return job
    existing = await db.get(Job, job_id)
    if existing is None:
        raise http_job_not_found(job_id)
    raise http_invalid_transition(existing.status, target_status)


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("", response_model=PaginatedResponse[JobRead])
async def list_jobs(
    db: DbSession,
    status: Annotated[list[JobStatus] | None, Query()] = None,
    worker_id: uuid.UUID | None = None,
    folder: str | None = None,
    filename: str | None = None,
    sort: str = "created_at_desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PaginatedResponse[JobRead]:
    stmt = (
        select(Job, Worker.hostname)
        .outerjoin(Worker, Job.worker_id == Worker.id)
    )

    if status:
        stmt = stmt.where(Job.status.in_(status))
    if worker_id:
        stmt = stmt.where(Job.worker_id == worker_id)
    if folder:
        stmt = stmt.where(Job.relative_folder.ilike(f"%{folder}%"))
    if filename:
        stmt = stmt.where(Job.original_filename.ilike(f"%{filename}%"))

    sort_map = {
        "created_at_desc": Job.created_at.desc(),
        "created_at_asc": Job.created_at.asc(),
        "priority_desc": Job.priority.desc(),
        "completed_at_desc": Job.completed_at.desc().nullslast(),
    }
    stmt = stmt.order_by(sort_map.get(sort, Job.created_at.desc()))

    count_stmt = select(func.count()).select_from(Job)
    if status:
        count_stmt = count_stmt.where(Job.status.in_(status))
    if worker_id:
        count_stmt = count_stmt.where(Job.worker_id == worker_id)
    if folder:
        count_stmt = count_stmt.where(Job.relative_folder.ilike(f"%{folder}%"))
    if filename:
        count_stmt = count_stmt.where(Job.original_filename.ilike(f"%{filename}%"))
    total = await db.scalar(count_stmt) or 0
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).all()

    return PaginatedResponse(
        items=await build_job_reads(list(rows)),
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, -(-total // page_size)),
    )


@router.get("/{job_id}", response_model=JobReadDetail)
async def get_job(job_id: uuid.UUID, db: DbSession) -> JobReadDetail:
    job = await _get_job_or_404(job_id, db)

    events = (
        await db.execute(
            select(JobEvent)
            .where(
                JobEvent.job_id == job_id,
                JobEvent.event_type != "progress",  # Guard: should never be stored
            )
            .order_by(JobEvent.created_at.asc())
        )
    ).scalars().all()

    hostname = None
    if job.worker_id:
        worker = await db.get(Worker, job.worker_id)
        hostname = worker.hostname if worker else None

    reads = await build_job_reads([(job, hostname)])
    detail = JobReadDetail.model_validate(reads[0].model_dump())
    detail.events = [JobEventRead.model_validate(e) for e in events]
    return detail


@router.post("/{job_id}/pause", response_model=JobPauseResponse)
async def pause_job(job_id: uuid.UUID, db: DbSession, ws: WsManager) -> JobPauseResponse:
    updated = await _svc.pause_job(db, job_id)
    job = await _handle_transition(updated, job_id, "paused", db)

    worker_id_str = str(job.worker_id) if job.worker_id else None
    delivered = False
    if worker_id_str:
        delivered = await ws.send_to_worker(
            worker_id_str, {"type": "PAUSE_JOB", "job_id": str(job_id)}
        )

    await ws.emit_job_status_changed(
        job_id=job.id,
        previous_status=JobStatus.PROCESSING,
        new_status=JobStatus.PAUSED,
        worker_id=job.worker_id,
        worker_hostname=await _worker_hostname(db, job.worker_id),
    )
    return JobPauseResponse(
        id=job.id,
        status=job.status,
        command_delivered=delivered,
        message="Duraklat komutu gönderildi" if delivered else "Komut kuyruklandı (kalp atışında teslim)",
    )


@router.post("/{job_id}/resume", response_model=JobPauseResponse)
async def resume_job(job_id: uuid.UUID, db: DbSession, ws: WsManager) -> JobPauseResponse:
    updated = await _svc.resume_job(db, job_id)
    job = await _handle_transition(updated, job_id, "processing", db)

    worker_id_str = str(job.worker_id) if job.worker_id else None
    delivered = False
    if worker_id_str:
        delivered = await ws.send_to_worker(
            worker_id_str, {"type": "RESUME_JOB", "job_id": str(job_id)}
        )

    await ws.emit_job_status_changed(
        job_id=job.id,
        previous_status=JobStatus.PAUSED,
        new_status=JobStatus.PROCESSING,
        worker_id=job.worker_id,
        worker_hostname=await _worker_hostname(db, job.worker_id),
    )
    return JobPauseResponse(
        id=job.id,
        status=job.status,
        command_delivered=delivered,
        message="Devam komutu gönderildi" if delivered else "Komut kuyruklandı",
    )


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: uuid.UUID, db: DbSession, ws: WsManager) -> dict:
    updated = await _svc.cancel_job(db, job_id)
    job = await _handle_transition(updated, job_id, "cancelled", db)

    worker_id_str = str(job.worker_id) if job.worker_id else None
    if worker_id_str:
        await ws.send_to_worker(worker_id_str, {"type": "CANCEL_JOB", "job_id": str(job_id)})

    await ws.emit_job_status_changed(
        job_id=job.id,
        previous_status="active",
        new_status=JobStatus.CANCELLED,
    )
    return {"id": str(job.id), "status": job.status}


@router.post("/{job_id}/retry", response_model=JobRetryResponse)
async def retry_job(job_id: uuid.UUID, db: DbSession) -> JobRetryResponse:
    updated = await _svc.retry_job(db, job_id)
    job = await _handle_transition(updated, job_id, "pending", db)
    return JobRetryResponse(id=job.id, status=job.status, retry_count=job.retry_count)
