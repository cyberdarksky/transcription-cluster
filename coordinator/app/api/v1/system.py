from __future__ import annotations

import shutil
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from ...config import settings
from ...core.dependencies import DbSession, WsManager
from ...database import check_db_connection
from ...models.enums import JobStatus, WorkerStatus
from ...models.input_directory import InputDirectory
from ...models.job import Job
from ...models.worker import Worker
from ...schemas.system import (
    BulkJobCreateRequest,
    BulkJobCreateResponse,
    CoordinatorInfo,
    InputDirectoryCreate,
    InputDirectoryRead,
    JobStats,
    ScanRequest,
    ScanResponse,
    SystemSettingsResponse,
    SystemSettingsUpdate,
    SystemStatsResponse,
    ThroughputStats,
    WorkerStats,
)

router = APIRouter(prefix="/system", tags=["system"])

_START_TIME = time.monotonic()


@router.get("/stats", response_model=SystemStatsResponse)
async def get_system_stats(db: DbSession) -> SystemStatsResponse:
    UTC = timezone.utc

    # Job counts per status
    job_rows = await db.execute(
        select(Job.status, func.count().label("cnt")).group_by(Job.status)
    )
    job_counts: dict[str, int] = {row.status: row.cnt for row in job_rows}

    # Worker counts per status
    worker_rows = await db.execute(
        select(Worker.status, func.count().label("cnt")).group_by(Worker.status)
    )
    worker_counts: dict[str, int] = {row.status: row.cnt for row in worker_rows}

    # Throughput
    now = datetime.now(UTC)
    from sqlalchemy import text as _text

    completed_1h = await db.scalar(
        select(func.count()).where(
            Job.status == JobStatus.COMPLETED,
            Job.completed_at >= _text("NOW() - INTERVAL '1 hour'"),
        )
    ) or 0
    completed_24h = await db.scalar(
        select(func.count()).where(
            Job.status == JobStatus.COMPLETED,
            Job.completed_at >= _text("NOW() - INTERVAL '24 hours'"),
        )
    ) or 0

    audio_24h_row = await db.scalar(
        select(func.coalesce(func.sum(Job.audio_duration_seconds), 0)).where(
            Job.status == JobStatus.COMPLETED,
            Job.completed_at >= _text("NOW() - INTERVAL '24 hours'"),
        )
    )
    avg_rtf_row = await db.scalar(
        select(func.avg(Job.rtf)).where(
            Job.status == JobStatus.COMPLETED,
            Job.completed_at >= _text("NOW() - INTERVAL '24 hours'"),
            Job.rtf.is_not(None),
        )
    )

    # Disk usage
    storage_used = storage_avail = None
    try:
        disk = shutil.disk_usage(str(settings.output_base_dir))
        storage_used = round(disk.used / (1024**3), 2)
        storage_avail = round(disk.free / (1024**3), 2)
    except OSError:
        pass

    active_dirs = await db.scalar(
        select(func.count()).where(InputDirectory.is_active.is_(True))
    ) or 0

    return SystemStatsResponse(
        jobs=JobStats(
            total=sum(job_counts.values()),
            pending=job_counts.get(JobStatus.QUEUED, 0)
            + job_counts.get(JobStatus.RETRY_WAIT, 0),
            assigned=job_counts.get(JobStatus.ASSIGNED, 0),
            downloading=job_counts.get(JobStatus.DOWNLOADING, 0),
            processing=job_counts.get(JobStatus.PROCESSING, 0),
            uploading=job_counts.get(JobStatus.UPLOADING, 0),
            paused=job_counts.get(JobStatus.PAUSED, 0),
            completed=job_counts.get(JobStatus.COMPLETED, 0),
            failed=job_counts.get(JobStatus.FAILED, 0),
            cancelled=job_counts.get(JobStatus.CANCELLED, 0),
        ),
        workers=WorkerStats(
            total=sum(worker_counts.values()),
            online=sum(
                worker_counts.get(s, 0)
                for s in [WorkerStatus.IDLE, WorkerStatus.BUSY, WorkerStatus.PAUSED]
            ),
            offline=worker_counts.get(WorkerStatus.OFFLINE, 0),
            busy=worker_counts.get(WorkerStatus.BUSY, 0),
            idle=worker_counts.get(WorkerStatus.IDLE, 0),
        ),
        throughput=ThroughputStats(
            jobs_completed_last_1h=completed_1h,
            jobs_completed_last_24h=completed_24h,
            audio_hours_last_24h=round(float(audio_24h_row or 0) / 3600, 2),
            avg_rtf_last_24h=round(float(avg_rtf_row), 4) if avg_rtf_row else None,
        ),
        coordinator=CoordinatorInfo(
            version=settings.coordinator_version,
            uptime_seconds=time.monotonic() - _START_TIME,
            db_connected=await check_db_connection(),
            input_dirs_active=active_dirs,
            storage_used_gb=storage_used,
            storage_available_gb=storage_avail,
        ),
    )


@router.get("/settings", response_model=SystemSettingsResponse)
async def get_settings() -> SystemSettingsResponse:
    return SystemSettingsResponse(
        worker_heartbeat_timeout_seconds=settings.worker_heartbeat_timeout_seconds,
        max_retries_default=settings.max_retries_default,
        retry_delay_seconds=settings.retry_delays_seconds,
        worker_metrics_retention_days=settings.worker_metrics_retention_days,
        job_events_retention_days=settings.job_events_retention_days,
        dashboard_refresh_interval_ms=5000,
        file_watcher_debounce_seconds=2,
        whisper_model=settings.whisper_model_path,
        whisper_language="tr",
        whisper_word_timestamps=True,
        job_timeout_multiplier=settings.job_timeout_multiplier,
        coordinator_recovery_grace_seconds=settings.recovery_grace_seconds,
    )


@router.put("/settings", response_model=SystemSettingsResponse)
async def update_settings(
    payload: SystemSettingsUpdate,
) -> SystemSettingsResponse:
    """
    Update runtime settings. Currently updates the in-process settings object.
    For persistent changes, update .env and restart the coordinator.
    """
    if payload.worker_heartbeat_timeout_seconds is not None:
        settings.worker_heartbeat_timeout_seconds = payload.worker_heartbeat_timeout_seconds
    if payload.max_retries_default is not None:
        settings.max_retries_default = payload.max_retries_default
    if payload.retry_delay_seconds is not None:
        settings.retry_delays_seconds = payload.retry_delay_seconds
    if payload.worker_metrics_retention_days is not None:
        settings.worker_metrics_retention_days = payload.worker_metrics_retention_days
    if payload.job_timeout_multiplier is not None:
        settings.job_timeout_multiplier = payload.job_timeout_multiplier

    return await get_settings()


# ── Input directories ─────────────────────────────────────────────────────────


@router.get("/input-directories", response_model=list[InputDirectoryRead])
async def list_input_directories(db: DbSession) -> list[InputDirectoryRead]:
    result = await db.execute(select(InputDirectory).order_by(InputDirectory.created_at))
    return [InputDirectoryRead.model_validate(d) for d in result.scalars()]


@router.post(
    "/input-directories",
    response_model=InputDirectoryRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_input_directory(
    payload: InputDirectoryCreate, db: DbSession
) -> InputDirectoryRead:
    d = InputDirectory(
        path=payload.path,
        output_path=payload.output_path,
        label=payload.label,
        watch_recursively=payload.watch_recursively,
        default_priority=payload.default_priority,
    )
    db.add(d)
    await db.flush()
    return InputDirectoryRead.model_validate(d)


@router.delete("/input-directories/{dir_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_input_directory(dir_id: uuid.UUID, db: DbSession) -> None:
    d = await db.get(InputDirectory, dir_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Input directory not found")
    await db.delete(d)


# ── Scan ──────────────────────────────────────────────────────────────────────


@router.post("/scan", response_model=ScanResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_scan(
    payload: ScanRequest, request: Request, db: DbSession
) -> ScanResponse:
    """
    Trigger a background directory scan to create jobs for unprocessed MP3 files.
    The actual scan runs as an asyncio task.
    """
    import asyncio

    file_watcher = getattr(request.app.state, "file_watcher", None)
    if file_watcher is None:
        raise HTTPException(status_code=503, detail="File watcher not running")

    if payload.input_directory_id:
        directory = await db.get(InputDirectory, payload.input_directory_id)
        if directory is None:
            raise HTTPException(status_code=404, detail="Input directory not found")
        directories = [directory]
    else:
        result = await db.execute(
            select(InputDirectory).where(InputDirectory.is_active.is_(True))
        )
        directories = list(result.scalars())

    scan_id = str(uuid.uuid4())

    async def _run_scan():
        for d in directories:
            await file_watcher.scan_directory(d, payload.force_reprocess)

    asyncio.create_task(_run_scan(), name=f"scan-{scan_id}")

    return ScanResponse(
        scan_id=scan_id,
        status="started",
        message=f"{len(directories)} dizin taranıyor",
    )
