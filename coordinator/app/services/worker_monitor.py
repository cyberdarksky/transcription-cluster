from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db_context
from ..models.enums import JobStatus, WorkerStatus
from ..models.job import Job
from ..models.worker import Worker
from ..websocket.manager import WebSocketManager

logger = logging.getLogger(__name__)

UTC = timezone.utc
_CHECK_INTERVAL = 15  # seconds between monitor sweeps


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

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                await self._run_sweep()
            except Exception:
                logger.exception("Worker monitor sweep failed")
            await asyncio.sleep(_CHECK_INTERVAL)

    async def _run_sweep(self) -> None:
        """
        One full sweep:
        1. Run all DB mutations in a single transaction.
        2. Collect events describing what changed.
        3. Transaction commits on context exit.
        4. Broadcast collected events to dashboard AFTER commit.
        """
        worker_events: list[_WorkerExpiredEvent] = []
        job_events: list[_JobRecoveredEvent] = []
        recovered_count = 0

        async with get_db_context() as db:
            worker_events = await self._expire_stale_workers(db)
            job_events, recovered_count = await self._recover_stale_jobs(db)

        # ── Broadcast AFTER transaction commits ───────────────────────────────
        now_iso = datetime.now(UTC).isoformat()

        for ev in worker_events:
            await self._ws.broadcast_to_dashboard({
                "type": "worker_status_changed",
                "data": {
                    "worker_id": ev.worker_id,
                    "hostname": ev.hostname,
                    "previous_status": ev.previous_status,
                    "new_status": WorkerStatus.OFFLINE,
                    "timestamp": now_iso,
                },
            })
            await self._ws.emit_system_alert(
                severity="warning",
                code="WORKER_OFFLINE",
                message=f"{ev.hostname} bağlantısı kesildi (kalp atışı zaman aşımı)",
            )

        for ev in job_events:
            await self._ws.emit_job_status_changed(
                job_id=uuid.UUID(ev.job_id),
                previous_status=ev.previous_status,
                new_status=ev.new_status,
            )

        if recovered_count > 0:
            logger.info("Recovered %d stale jobs", recovered_count)

    # ── DB mutations (inside transaction) ─────────────────────────────────────

    async def _expire_stale_workers(
        self, db: AsyncSession
    ) -> list[_WorkerExpiredEvent]:
        """
        Batch-expire all workers whose heartbeat window has lapsed.
        Single SELECT to find them, single UPDATE to mark them offline.
        Returns event data for post-commit broadcasting.
        """
        timeout = settings.worker_heartbeat_timeout_seconds

        # Use MAKE_INTERVAL for safe integer binding — avoids f-string SQL
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

        # Single batch UPDATE — one round-trip regardless of worker count
        await db.execute(
            update(Worker)
            .where(Worker.id.in_(stale_ids))
            .values(status=WorkerStatus.OFFLINE, updated_at=func.now())
        )
        await db.flush()

        for row in rows:
            logger.warning(
                "Worker heartbeat expired; marked offline",
                extra={"worker_id": str(row.id), "hostname": row.hostname,
                       "timeout_seconds": timeout},
            )

        return events

    async def _recover_stale_jobs(
        self, db: AsyncSession
    ) -> tuple[list[_JobRecoveredEvent], int]:
        """
        Re-queue or fail jobs that belong to offline workers.
        Single SELECT + ORM mutations + single flush.
        Returns (broadcast events, re-queued count).
        """
        stale_jobs_result = await db.execute(
            select(Job)
            .join(Worker, Job.worker_id == Worker.id)
            .where(
                Worker.status == WorkerStatus.OFFLINE,
                Job.status.in_([
                    JobStatus.ASSIGNED,
                    JobStatus.PROCESSING,
                    JobStatus.PAUSED,
                ]),
            )
        )
        jobs = stale_jobs_result.scalars().all()
        if not jobs:
            return [], 0

        events: list[_JobRecoveredEvent] = []
        recovered = 0

        for job in jobs:
            prev = job.status
            if job.retry_count < job.max_retries:
                job.status = JobStatus.PENDING
                job.worker_id = None
                job.assigned_at = None
                job.started_at = None
                job.paused_at = None
                job.progress_percent = None
                job.retry_count += 1
                job.last_error = "İşçi bağlantı kesilmesi — iş yeniden kuyruğa alındı"
                new_status = JobStatus.PENDING
                recovered += 1
            else:
                job.status = JobStatus.FAILED
                job.worker_id = None
                job.last_error = (
                    "İşçi bağlantı kesilmesi nedeniyle maksimum yeniden deneme sayısına ulaşıldı"
                )
                new_status = JobStatus.FAILED

            events.append(_JobRecoveredEvent(
                job_id=str(job.id),
                previous_status=prev,
                new_status=new_status,
            ))

        # Single flush for all job mutations — not per-job
        await db.flush()
        return events, recovered
