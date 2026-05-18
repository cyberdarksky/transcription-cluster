from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db_context
from ..models.enums import ErrorCategory, JobStatus, WorkerStatus
from ..models.job import Job
from ..models.worker import Worker
from ..background import log_task_result
from ..websocket.manager import WebSocketManager

logger = logging.getLogger(__name__)

UTC = timezone.utc
_CHECK_INTERVAL = 15  # seconds between monitor sweeps

# Align with lease recovery — all states a worker can hold while offline.
_OFFLINE_RECOVERABLE_STATUSES = frozenset({
    JobStatus.ASSIGNED,
    JobStatus.DOWNLOADING,
    JobStatus.PROCESSING,
    JobStatus.UPLOADING,
    JobStatus.PAUSED,
})


@dataclass
class _WorkerExpiredEvent:
    worker_id: str
    hostname: str
    previous_status: str


@dataclass
class _JobRecoveredEvent:
    job_id: str
    previous_status: str
    new_status: str


class WorkerMonitor:
    """
    Background asyncio task that detects dead workers and recovers their jobs.

    Transaction safety rule:
    - All DB mutations happen INSIDE a single transaction (get_db_context).
    - All WebSocket broadcasts happen AFTER the transaction commits successfully.
    - The dashboard never receives events for changes that were rolled back.
    """

    def __init__(self, ws_manager: WebSocketManager) -> None:
        self._ws = ws_manager
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop(), name="worker-monitor")
        self._task.add_done_callback(
            lambda t: log_task_result(t, "worker-monitor")
        )
        logger.info("Worker monitor started (interval=%ds)", _CHECK_INTERVAL)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Worker monitor stopped")

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                await self._run_sweep()
            except Exception:
                logger.exception("Worker monitor sweep failed")
            await asyncio.sleep(_CHECK_INTERVAL)

    async def run_post_grace_recovery(
        self, coordinator_started_at: datetime
    ) -> int:
        """
        After coordinator restart grace period:
        mark workers that never checked in since startup offline, then re-queue jobs.
        """
        worker_events: list[_WorkerExpiredEvent] = []
        job_events: list[_JobRecoveredEvent] = []
        recovered = 0

        async with get_db_context() as db:
            worker_events = await self._mark_workers_offline_since(
                db, coordinator_started_at
            )
            job_events, recovered = await self._recover_stale_jobs(db)

        await self._broadcast_recovery_events(
            worker_events, job_events, recovered, grace_recovery=True
        )

        if worker_events:
            logger.info(
                "Grace recovery: %d worker(s) marked offline (no check-in since restart)",
                len(worker_events),
            )
        return recovered

    async def _run_sweep(self) -> None:
        worker_events: list[_WorkerExpiredEvent] = []
        job_events: list[_JobRecoveredEvent] = []
        recovered = 0

        async with get_db_context() as db:
            worker_events = await self._expire_stale_workers(db)
            job_events, recovered = await self._recover_stale_jobs(db)

        await self._broadcast_recovery_events(worker_events, job_events, recovered)

    async def _broadcast_recovery_events(
        self,
        worker_events: list[_WorkerExpiredEvent],
        job_events: list[_JobRecoveredEvent],
        recovered: int,
        *,
        grace_recovery: bool = False,
    ) -> None:
        for ev in worker_events:
            await self._ws.emit_worker_status_changed(
                worker_id=uuid.UUID(ev.worker_id),
                hostname=ev.hostname,
                previous_status=ev.previous_status,
                new_status=WorkerStatus.OFFLINE,
            )
            if grace_recovery:
                alert_code = "WORKER_OFFLINE_RESTART"
                alert_msg = (
                    f"{ev.hostname} koordinatör yeniden başlatmasından sonra "
                    "yeniden bağlanmadı"
                )
            else:
                alert_code = "WORKER_OFFLINE"
                alert_msg = (
                    f"{ev.hostname} bağlantısı kesildi (kalp atışı zaman aşımı)"
                )
            await self._ws.emit_system_alert(
                severity="warning",
                code=alert_code,
                message=alert_msg,
            )

        for ev in job_events:
            await self._ws.emit_job_status_changed(
                job_id=uuid.UUID(ev.job_id),
                previous_status=ev.previous_status,
                new_status=ev.new_status,
            )

        if recovered > 0:
            logger.info("Recovered %d stale jobs from offline workers", recovered)

    async def _mark_workers_offline_since(
        self, db: AsyncSession, since: datetime
    ) -> list[_WorkerExpiredEvent]:
        """
        Workers that did not heartbeat/register after coordinator startup are
        treated as disconnected (restart recovery path).
        """
        stale_result = await db.execute(
            select(Worker.id, Worker.hostname, Worker.status).where(
                Worker.status.not_in([WorkerStatus.OFFLINE, WorkerStatus.ERROR]),
                or_(
                    Worker.last_heartbeat.is_(None),
                    Worker.last_heartbeat < since,
                ),
            )
        )
        rows = stale_result.all()
        if not rows:
            return []

        stale_ids = [row.id for row in rows]
        events = [
            _WorkerExpiredEvent(
                worker_id=str(row.id),
                hostname=row.hostname,
                previous_status=row.status,
            )
            for row in rows
        ]

        await db.execute(
            update(Worker)
            .where(Worker.id.in_(stale_ids))
            .values(
                status=WorkerStatus.OFFLINE,
                current_job_id=None,
                updated_at=func.now(),
            )
        )
        await db.flush()

        for row in rows:
            logger.warning(
                "Worker did not check in after coordinator restart; marked offline",
                extra={"worker_id": str(row.id), "hostname": row.hostname},
            )

        return events

    async def _expire_stale_workers(
        self, db: AsyncSession
    ) -> list[_WorkerExpiredEvent]:
        timeout = settings.worker_heartbeat_timeout_seconds
        stale_cutoff = func.now() - func.make_interval(0, 0, 0, 0, 0, timeout)

        stale_result = await db.execute(
            select(Worker.id, Worker.hostname, Worker.status).where(
                Worker.status.not_in([WorkerStatus.OFFLINE, WorkerStatus.ERROR]),
                Worker.last_heartbeat.is_not(None),
                Worker.last_heartbeat < stale_cutoff,
            )
        )
        rows = stale_result.all()
        if not rows:
            return []

        stale_ids = [row.id for row in rows]
        events = [
            _WorkerExpiredEvent(
                worker_id=str(row.id),
                hostname=row.hostname,
                previous_status=row.status,
            )
            for row in rows
        ]

        await db.execute(
            update(Worker)
            .where(Worker.id.in_(stale_ids))
            .values(status=WorkerStatus.OFFLINE, updated_at=func.now())
        )
        await db.flush()

        for row in rows:
            logger.warning(
                "Worker heartbeat expired; marked offline",
                extra={
                    "worker_id": str(row.id),
                    "hostname": row.hostname,
                    "timeout_seconds": timeout,
                },
            )

        return events

    async def _recover_stale_jobs(
        self, db: AsyncSession
    ) -> tuple[list[_JobRecoveredEvent], int]:
        """
        Re-queue jobs held by offline workers.

        Uses the same RETRY_WAIT → QUEUED flow as LeaseRecoveryService so
        retry delays and RetryScheduler stay consistent.
        """
        stmt = (
            select(Job)
            .join(Worker, Job.worker_id == Worker.id)
            .where(
                Worker.status == WorkerStatus.OFFLINE,
                Job.status.in_(_OFFLINE_RECOVERABLE_STATUSES),
            )
            .with_for_update(skip_locked=True)
        )
        result = await db.execute(stmt)
        jobs = result.scalars().all()
        if not jobs:
            return [], 0

        events: list[_JobRecoveredEvent] = []
        recovered = 0
        now = datetime.now(UTC)

        for job in jobs:
            prev = job.status
            can_retry = (
                job.retry_count < job.max_retries
                and job.error_category != ErrorCategory.DETERMINISTIC
            )

            if can_retry:
                delay = settings.retry_delays_seconds[
                    min(job.retry_count, len(settings.retry_delays_seconds) - 1)
                ]
                job.status = JobStatus.RETRY_WAIT
                job.worker_id = None
                job.lease_expires_at = None
                job.assigned_at = None
                job.started_at = None
                job.paused_at = None
                job.progress_percent = None
                job.retry_count += 1
                job.error_category = ErrorCategory.TRANSIENT
                job.last_error = "İşçi bağlantısı kesildi — iş yeniden kuyruğa alındı"
                job.next_retry_after = (
                    None
                    if delay == 0
                    else now + timedelta(seconds=delay)
                )
                new_status = JobStatus.RETRY_WAIT
                recovered += 1
            else:
                job.status = JobStatus.FAILED
                job.worker_id = None
                job.lease_expires_at = None
                job.last_error = (
                    "İşçi bağlantısı kesildi — maksimum yeniden deneme sayısına ulaşıldı"
                )
                new_status = JobStatus.FAILED

            events.append(_JobRecoveredEvent(
                job_id=str(job.id),
                previous_status=prev,
                new_status=new_status,
            ))

        await db.flush()

        offline_worker_ids = await db.scalars(
            select(Worker.id).where(
                Worker.status == WorkerStatus.OFFLINE,
                Worker.current_job_id.is_not(None),
            )
        )
        worker_ids_to_clear = list(offline_worker_ids.all())
        if worker_ids_to_clear:
            await db.execute(
                update(Worker)
                .where(Worker.id.in_(worker_ids_to_clear))
                .values(current_job_id=None, updated_at=func.now())
            )

        return events, recovered
