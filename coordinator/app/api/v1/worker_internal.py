from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from starlette.requests import Request
from sqlalchemy import select

from ...config import settings
from ...core.dependencies import DbSession, WsManager
from ...core.time_utils import utc_naive
from ...core.exceptions import http_worker_not_found
from ...models.enums import JobStatus, WorkerStatus
from ...models.job import Job
from ...models.worker import Worker
from ...models.worker_metric import WorkerMetric
from ...queue.distributed_queue import distributed_queue as _queue
from ...queue.lease_manager import lease_manager as _leases
from ...queue.states import LEASEABLE_STATUSES, REQUIRED_PREVIOUS_STATE, InvalidTransitionError
from ...schemas.job import (
    JobAssignment,
    JobCompleteResponse,
    JobFailRequest,
    JobFailResponse,
    JobProgressRequest,
    JobProgressResponse,
    JobStartRequest,
)
from ...schemas.worker import (
    PendingCommand,
    WorkerHeartbeatRequest,
    WorkerHeartbeatResponse,
    WorkerRegisterRequest,
    WorkerRegisterResponse,
)
router = APIRouter(prefix="/worker", tags=["worker-internal"])
logger = logging.getLogger(__name__)
UTC = timezone.utc

# Worker-controlled statuses are a subset of all statuses.
# OFFLINE and ERROR are coordinator-only and must not be accepted from worker heartbeats.
_WORKER_SETTABLE_STATUSES = frozenset({
    WorkerStatus.IDLE,
    WorkerStatus.BUSY,
    WorkerStatus.PAUSED,
})


# ── Registration ──────────────────────────────────────────────────────────────


@router.post("/register", response_model=WorkerRegisterResponse)
async def register_worker(
    payload: WorkerRegisterRequest,
    http_request: Request,
    db: DbSession,
    ws: WsManager,
) -> WorkerRegisterResponse:
    """
    Register or re-register a worker. Idempotent: updates existing record
    matched by stable_worker_id (preferred) or mac_address (fallback).

    Reconnect protocol: if payload.current_job_id is supplied and the coordinator
    is past its grace period, the job's ownership is verified — if it still belongs
    to this worker the job continues; otherwise the worker is told to cancel it.
    """
    recovery_grace_active: bool = getattr(http_request.app.state, "recovery_grace_active", False)
    cancel_current_job = False

    # ── Find or create worker record ──────────────────────────────────────────
    worker: Worker | None = None
    if payload.stable_worker_id:
        worker = await db.scalar(
            select(Worker).where(Worker.stable_worker_id == payload.stable_worker_id)
        )
    if worker is None:
        worker = await db.scalar(
            select(Worker).where(Worker.mac_address == payload.mac_address)
        )

    if worker is None:
        worker = Worker(
            stable_worker_id=payload.stable_worker_id,
            hostname=payload.hostname,
            mac_address=payload.mac_address,
            ip_address=payload.ip_address,
            api_port=payload.api_port,
            cpu_model=payload.cpu_model,
            cpu_cores=payload.cpu_cores,
            memory_total_gb=payload.memory_total_gb,
            gpu_model=payload.gpu_model,
            whisper_backend=payload.whisper_backend,
            worker_version=payload.worker_version,
        )
        db.add(worker)
        await db.flush()
    else:
        # Update mutable fields; never overwrite with None
        worker.hostname = payload.hostname
        worker.ip_address = payload.ip_address
        worker.api_port = payload.api_port
        if payload.cpu_model:
            worker.cpu_model = payload.cpu_model
        if payload.cpu_cores:
            worker.cpu_cores = payload.cpu_cores
        if payload.memory_total_gb:
            worker.memory_total_gb = payload.memory_total_gb
        if payload.gpu_model:
            worker.gpu_model = payload.gpu_model
        if payload.worker_version:
            worker.worker_version = payload.worker_version
        if payload.stable_worker_id and not worker.stable_worker_id:
            worker.stable_worker_id = payload.stable_worker_id

    # ── Reconnect with in-progress job ────────────────────────────────────────
    if payload.current_job_id:
        job = await db.get(Job, payload.current_job_id)
        if (
            job is not None
            and job.worker_id == worker.id
            and job.status.is_active
        ):
            worker.status = WorkerStatus.BUSY
            worker.current_job_id = job.id
            logger.info(
                "Worker reconnected with active job",
                extra={
                    "worker_id": str(worker.id),
                    "job_id": str(job.id),
                    "recovery_grace": recovery_grace_active,
                },
            )
        else:
            worker.status = WorkerStatus.IDLE
            worker.current_job_id = None
            cancel_current_job = True
    else:
        worker.status = WorkerStatus.IDLE
        worker.current_job_id = None

    worker.last_heartbeat = utc_naive()
    await db.flush()

    await ws.emit_worker_status_changed(
        worker_id=worker.id,
        hostname=worker.hostname,
        previous_status=WorkerStatus.OFFLINE,
        new_status=worker.status,
    )
    logger.info("Worker registered", extra={"worker_id": str(worker.id), "hostname": worker.hostname})

    return WorkerRegisterResponse(
        worker_id=worker.id,
        heartbeat_interval_seconds=worker.heartbeat_interval_seconds,
        coordinator_version=settings.coordinator_version,
        websocket_url=f"ws://{http_request.base_url.hostname}:{settings.coordinator_port}/ws/worker",
        recovery_grace_active=recovery_grace_active,
        cancel_current_job=cancel_current_job,
        settings={
            "whisper_model": settings.whisper_model_path,
            "whisper_language": "tr",
            "whisper_word_timestamps": True,
            "job_timeout_multiplier": settings.job_timeout_multiplier,
        },
    )


# ── Heartbeat ─────────────────────────────────────────────────────────────────


@router.post("/heartbeat", response_model=WorkerHeartbeatResponse)
async def heartbeat(
    payload: WorkerHeartbeatRequest, db: DbSession, ws: WsManager
) -> WorkerHeartbeatResponse:
    worker = await db.get(Worker, payload.worker_id)
    if worker is None:
        raise http_worker_not_found(payload.worker_id)

    # Validate that worker doesn't report coordinator-only statuses.
    # Silently coerce to IDLE to avoid data corruption.
    accepted_status = (
        payload.status if payload.status in _WORKER_SETTABLE_STATUSES
        else WorkerStatus.IDLE
    )
    if accepted_status != payload.status:
        logger.warning(
            "Worker sent invalid status; coercing to IDLE",
            extra={"worker_id": str(payload.worker_id), "reported_status": payload.status},
        )

    # Record heartbeat timestamp FIRST — before any other work — so false
    # timeout detection is not triggered by processing latency.
    now = utc_naive()
    worker.last_heartbeat = now
    worker.status = accepted_status
    worker.current_job_id = payload.current_job_id
    await db.flush()

    # Renew lease on the worker's current job (if any).
    lease_valid: bool | None = None
    if payload.current_job_id:
        job = await db.get(Job, payload.current_job_id)
        lease_seconds = settings.job_lease_duration_seconds
        if job and job.max_job_duration_seconds:
            lease_seconds = max(lease_seconds, min(int(job.max_job_duration_seconds), 7200))

        lease_valid = await _queue.renew_lease(
            db, payload.current_job_id, payload.worker_id, lease_seconds,
        )
        # Re-grant when the worker still owns an active job (renew can fail if the
        # lease just expired between sweeps — do not force-cancel a live worker).
        if (
            not lease_valid
            and job is not None
            and job.worker_id == payload.worker_id
            and job.status in LEASEABLE_STATUSES
        ):
            _leases.grant(job, payload.worker_id, lease_seconds)
            await db.flush()
            lease_valid = True
            logger.info(
                "Heartbeat re-granted lease on active job",
                extra={
                    "worker_id": str(payload.worker_id),
                    "job_id": str(payload.current_job_id),
                },
            )
        elif not lease_valid:
            logger.warning(
                "Heartbeat lease renewal failed — job may have been recovered",
                extra={
                    "worker_id": str(payload.worker_id),
                    "job_id": str(payload.current_job_id),
                },
            )

        if job and payload.job_progress_percent is not None:
            await _queue.update_progress(
                db,
                payload.current_job_id,
                payload.worker_id,
                payload.job_progress_percent,
            )
            await ws.emit_job_progress(
                job_id=payload.current_job_id,
                progress_percent=float(payload.job_progress_percent),
                elapsed_seconds=None,
                worker_id=payload.worker_id,
            )

    # Persist metrics snapshot
    m = payload.metrics
    db.add(WorkerMetric(
        worker_id=worker.id,
        cpu_percent=m.cpu_percent,
        memory_used_gb=m.memory_used_gb,
        memory_total_gb=m.memory_total_gb,
        memory_percent=m.memory_percent,
        gpu_percent=m.gpu_percent,
        gpu_memory_used_gb=m.gpu_memory_used_gb,
        current_job_id=payload.current_job_id,
        job_progress_percent=payload.job_progress_percent,
    ))
    await db.flush()

    # Broadcast metrics to dashboard (non-critical — fire and don't await errors)
    await ws.emit_worker_metrics(
        worker_id=worker.id,
        hostname=worker.hostname,
        cpu_percent=float(m.cpu_percent) if m.cpu_percent is not None else None,
        memory_percent=float(m.memory_percent) if m.memory_percent is not None else None,
        gpu_percent=float(m.gpu_percent) if m.gpu_percent is not None else None,
        job_progress=float(payload.job_progress_percent) if payload.job_progress_percent is not None else None,
    )

    pending_raw = ws.pop_pending_commands(str(payload.worker_id))
    return WorkerHeartbeatResponse(
        received_at=now,
        pending_commands=[PendingCommand(**cmd) for cmd in pending_raw],
        lease_valid=lease_valid,
    )


# ── Job claiming ──────────────────────────────────────────────────────────────


@router.get("/jobs/next")
async def claim_next_job(
    worker_id: uuid.UUID, db: DbSession, ws: WsManager
) -> JobAssignment:
    """
    Atomically claim the next pending job (FOR UPDATE SKIP LOCKED).
    Returns 204 No Content when the queue is empty.
    """
    from fastapi.responses import Response

    job = await _queue.claim_job(db, worker_id)
    if job is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)  # type: ignore[return-value]

    worker = await db.get(Worker, worker_id)
    await ws.emit_job_status_changed(
        job_id=job.id,
        previous_status=JobStatus.QUEUED,
        new_status=JobStatus.ASSIGNED,
        worker_id=worker_id,
        worker_hostname=worker.hostname if worker else None,
    )
    return JobAssignment(
        job_id=job.id,
        input_path=job.input_path,
        original_filename=job.original_filename,
        relative_folder=job.relative_folder,
        file_size_bytes=job.file_size_bytes,
        download_url=f"/api/v1/files/{job.id}/download",
        whisper_settings=_queue.build_whisper_settings(),
        max_job_duration_seconds=job.max_job_duration_seconds,
    )


# ── Job lifecycle ─────────────────────────────────────────────────────────────


@router.post("/jobs/{job_id}/start", summary="Legacy: ASSIGNED → PROCESSING (prefer /state)")
async def start_job(
    job_id: uuid.UUID, payload: JobStartRequest, db: DbSession, ws: WsManager
) -> dict:
    try:
        job = await _queue.advance_state(
            db, job_id, payload.worker_id, JobStatus.PROCESSING
        )
    except (InvalidTransitionError, KeyError, ValueError, PermissionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"detail": str(exc), "error_code": "INVALID_STATE_TRANSITION"},
        ) from exc

    await ws.emit_job_status_changed(
        job_id=job.id,
        previous_status=JobStatus.ASSIGNED,
        new_status=JobStatus.PROCESSING,
        worker_id=payload.worker_id,
    )
    return {"status": job.status, "started_at": job.started_at.isoformat() if job.started_at else None}


@router.post("/jobs/{job_id}/state", summary="Advance job to next state in pipeline")
async def advance_job_state(
    job_id: uuid.UUID,
    worker_id: uuid.UUID,
    new_state: JobStatus,
    db: DbSession,
    ws: WsManager,
) -> dict:
    """
    Advance the job along the processing pipeline.

    Valid progressions (in order):
        ASSIGNED → DOWNLOADING  (worker started download)
        DOWNLOADING → PROCESSING  (download complete, transcription starting)
        PROCESSING → UPLOADING  (transcription complete, uploading results)

    Atomically validated at the DB level — no race between concurrent requests.
    """
    try:
        job = await _queue.advance_state(db, job_id, worker_id, new_state)
    except (InvalidTransitionError, KeyError, ValueError, PermissionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"detail": str(exc), "error_code": "INVALID_STATE_TRANSITION"},
        ) from exc

    prev = REQUIRED_PREVIOUS_STATE[new_state]
    worker = await db.get(Worker, worker_id)
    await ws.emit_job_status_changed(
        job_id=job.id,
        previous_status=prev,
        new_status=new_state,
        worker_id=worker_id,
        worker_hostname=worker.hostname if worker else None,
    )
    return {"job_id": str(job_id), "status": job.status}


@router.post("/jobs/{job_id}/progress", response_model=JobProgressResponse)
async def report_progress(
    job_id: uuid.UUID, payload: JobProgressRequest, db: DbSession, ws: WsManager
) -> JobProgressResponse:
    await _queue.update_progress(db, job_id, payload.worker_id, payload.percent)
    await ws.emit_job_progress(
        job_id=job_id,
        progress_percent=float(payload.percent),
        elapsed_seconds=payload.elapsed_seconds,
        worker_id=payload.worker_id,
    )
    return JobProgressResponse(received=True, command=None)


@router.post("/jobs/{job_id}/complete", response_model=JobCompleteResponse)
async def complete_job(
    job_id: uuid.UUID,
    db: DbSession,
    ws: WsManager,
    metadata: str = Form(..., description="JSON string with completion metadata"),
    srt_file: UploadFile = File(..., description=".srt output file"),
    json_file: UploadFile = File(..., description=".json output file"),
) -> JobCompleteResponse:
    """
    Worker uploads SRT and JSON output along with completion metadata.
    Validates worker ownership under SELECT FOR UPDATE before accepting
    (protects against stale-worker race when job was reassigned at timeout).
    """
    meta = json.loads(metadata)
    worker_id = uuid.UUID(str(meta["worker_id"]))

    srt_content = await srt_file.read()
    json_content = await json_file.read()

    try:
        job = await _queue.complete_job(
            db=db,
            job_id=job_id,
            worker_id=worker_id,
            srt_content=srt_content,
            json_content=json_content,
            metadata=meta,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"detail": str(exc), "error_code": "JOB_OWNERSHIP_CONFLICT"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    worker = await db.get(Worker, worker_id)
    await ws.emit_job_status_changed(
        job_id=job.id,
        previous_status=JobStatus.PROCESSING,
        new_status=JobStatus.COMPLETED,
        worker_id=worker_id,
        worker_hostname=worker.hostname if worker else None,
    )
    return JobCompleteResponse(
        status=job.status,
        output_srt_path=job.output_srt_path or "",
        output_json_path=job.output_json_path or "",
    )


@router.post("/jobs/{job_id}/fail", response_model=JobFailResponse)
async def fail_job(
    job_id: uuid.UUID, payload: JobFailRequest, db: DbSession, ws: WsManager
) -> JobFailResponse:
    try:
        job = await _queue.fail_job(
            db=db,
            job_id=job_id,
            worker_id=payload.worker_id,
            error_message=payload.error_message,
            error_category=payload.error_category,
            retry=payload.retry,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"detail": str(exc), "error_code": "JOB_OWNERSHIP_CONFLICT"},
        ) from exc

    worker = await db.get(Worker, payload.worker_id)
    await ws.emit_job_status_changed(
        job_id=job.id,
        previous_status=JobStatus.PROCESSING,
        new_status=job.status,
        worker_id=payload.worker_id,
        worker_hostname=worker.hostname if worker else None,
    )
    return JobFailResponse(
        status=job.status,
        retry_count=job.retry_count,
        will_retry=job.status == JobStatus.RETRY_WAIT,
        retry_after=job.next_retry_after,
    )
