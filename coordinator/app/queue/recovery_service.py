"""
LeaseRecoveryService — detects expired job leases and recovers them.

Relationship with WorkerMonitor:
    - WorkerMonitor  → marks workers OFFLINE when heartbeat window lapses.
    - LeaseRecovery  → re-queues jobs when lease_expires_at < NOW(), regardless
                       of whether the worker is online or offline.

This separation means:
    1. A worker can have a live heartbeat but its lease expires (job is taking
       too long — the "job timeout" safety net).
    2. A worker can go offline (no heartbeat) and its jobs are recovered here.

Recovery semantics:
    - retry_count < max_retries AND error_category != 'deterministic'
        → status = RETRY_WAIT, retry_count++, next_retry_after set
    - otherwise
        → status = FAILED

Processing rate:
    Processes at most BATCH_SIZE expired leases per sweep to keep transactions
    short and avoid locking many rows at once.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from datetime import timedelta

from ..config import settings
from ..database import get_db_context
from ..models.enums import ErrorCategory, JobStatus, WorkerStatus
from ..models.job import Job
from ..models.worker import Worker
from ..background import log_task_result
from ..websocket.manager import WebSocketManager
from .states import RECOVERABLE_STATUSES

logger = logging.getLogger(__name__)
UTC = timezone.utc
BATCH_SIZE = 50  # max expired leases per sweep


@dataclass
class _RecoveryEvent:
    job_id: str
    previous_status: str
    new_status: str
    worker_id: str | None
    hostname: str | None


class LeaseRecoveryService:
    """
    Asyncio background task that scans for expired job leases.
    Runs every lease_recovery_interval_seconds (default 30 s).
    """

    def __init__(self, ws_manager: WebSocketManager) -> None:
        self._ws = ws_manager
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="lease-recovery")
        self._task.add_done_callback(
            lambda t: log_task_result(t, "lease-recovery")
        )
        logger.info(
            "Lease recovery service started (interval=%ds)",
            settings.lease_recovery_interval_seconds,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Lease recovery service stopped")

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._sweep()
            except Exception:
                logger.exception("Lease recovery sweep failed")
            await asyncio.sleep(settings.lease_recovery_interval_seconds)

    async def _sweep(self) -> None:
        """
        One recovery sweep:
        1. Find expired-lease jobs inside a transaction.
        2. Mutate their status (RETRY_WAIT or FAILED).
        3. Collect broadcast events.
        4. Commit.
        5. Broadcast AFTER commit.
        """
        events: list[_RecoveryEvent] = []
        recovered = 0

        async with get_db_context() as db:
            events, recovered = await self._recover_expired(db)

        # Broadcast AFTER the transaction commits
        for ev in events:
            await self._ws.emit_job_status_changed(
                job_id=uuid.UUID(ev.job_id),
                previous_status=ev.previous_status,
                new_status=ev.new_status,
            )
            if ev.new_status == JobStatus.FAILED:
                await self._ws.emit_system_alert(
                    severity="warning",
                    code="JOB_LEASE_EXPIRED_FAILED",
                    message=(
                        f"İş {ev.job_id[:8]}… maksimum yeniden deneme sayısına ulaştı "
                        f"(kira sona erdi). İşçi: {ev.hostname or 'bilinmiyor'}"
                    ),
                )

        if recovered > 0:
            logger.info("Lease recovery: %d jobs re-queued", recovered)

    # ── DB mutations (inside transaction) ─────────────────────────────────────

    async def _recover_expired(
        self, db: AsyncSession
    ) -> tuple[list[_RecoveryEvent], int]:
        """
        Find and recover at most BATCH_SIZE jobs with expired leases.

        FOR UPDATE SKIP LOCKED prevents race with a concurrent recovery instance
        (e.g., if the coordinator is restarted twice in quick succession).
        """
        # Find expired leases — SKIP LOCKED avoids blocking concurrent processes
        stmt = (
            select(Job)
            .where(
                Job.status.in_(RECOVERABLE_STATUSES),
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at < func.now(),
            )
            .with_for_update(skip_locked=True)
            .limit(BATCH_SIZE)
        )
        result = await db.execute(stmt)
        jobs = result.scalars().all()
        if not jobs:
            return [], 0

        events: list[_RecoveryEvent] = []
        recovered = 0

        # Fetch hostname for alert messages (batch lookup)
        worker_ids = {job.worker_id for job in jobs if job.worker_id}
        hostnames: dict[uuid.UUID, str] = {}
        if worker_ids:
            w_result = await db.execute(
                select(Worker.id, Worker.hostname).where(Worker.id.in_(worker_ids))
            )
            hostnames = {row.id: row.hostname for row in w_result}

        for job in jobs:
            prev_status = job.status

            # BUG-FIX #1: Capture worker_id BEFORE mutating it to None.
            # The original code set job.worker_id = None then read job.worker_id
            # in the _RecoveryEvent, yielding None every time. This made the
            # batch worker UPDATE below a no-op (empty affected_worker_ids list)
            # and left worker.current_job_id permanently stale after lease expiry.
            old_worker_id: uuid.UUID | None = job.worker_id

            # Decide: retry or fail?
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
                job.progress_percent = None
                job.retry_count += 1
                job.error_category = ErrorCategory.TRANSIENT
                job.last_error = "Kiralama süresi doldu — iş yeniden kuyruğa alındı"
                # BUG-FIX #2: Compute next_retry_after in Python, not via SQL func.
                # Assigning a SQLAlchemy func expression to an ORM attribute stores
                # the expression object, not a datetime. Any code reading
                # job.next_retry_after before session.refresh() gets gibberish.
                job.next_retry_after = (
                    None
                    if delay == 0
                    else datetime.now(UTC) + timedelta(seconds=delay)
                )
                new_status = JobStatus.RETRY_WAIT
                recovered += 1
            else:
                job.status = JobStatus.FAILED
                job.worker_id = None
                job.lease_expires_at = None
                job.last_error = (
                    "Kiralama süresi doldu — maksimum yeniden deneme sayısına ulaşıldı"
                )
                new_status = JobStatus.FAILED

            events.append(_RecoveryEvent(
                job_id=str(job.id),
                previous_status=prev_status,
                new_status=new_status,
                # Use old_worker_id captured before mutation (not job.worker_id which is now None)
                worker_id=str(old_worker_id) if old_worker_id else None,
                hostname=hostnames.get(old_worker_id) if old_worker_id else None,
            ))

        # Single flush for all mutations
        await db.flush()

        # Clear current_job_id on affected workers (batch)
        affected_worker_ids = [
            uuid.UUID(ev.worker_id)
            for ev in events
            if ev.worker_id
        ]
        if affected_worker_ids:
            await db.execute(
                update(Worker)
                .where(Worker.id.in_(affected_worker_ids))
                .values(current_job_id=None, updated_at=func.now())
            )

        logger.info(
            "Lease recovery sweep: %d re-queued, %d failed",
            recovered,
            len(jobs) - recovered,
            extra={"expired_count": len(jobs)},
        )
        return events, recovered
