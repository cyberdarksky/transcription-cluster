"""
RetryScheduler — promotes RETRY_WAIT jobs to QUEUED when their delay has passed.

BUG-FIX: Original query required next_retry_after IS NOT NULL AND <= NOW(),
which permanently excluded delay=0 retries (next_retry_after = NULL means
"immediately claimable"). Those jobs were stuck in RETRY_WAIT forever.

Fix: also promote RETRY_WAIT jobs where next_retry_after IS NULL.

Design:
    - Runs every retry_scheduler_interval_seconds (default 30 s).
    - Finds all RETRY_WAIT jobs where next_retry_after <= NOW().
    - Transitions them to QUEUED so workers can claim them.
    - Uses FOR UPDATE SKIP LOCKED to avoid conflicts with concurrent instances.

This is deliberately separate from LeaseRecoveryService:
    - Recovery creates RETRY_WAIT entries.
    - Scheduler promotes them when the delay passes.
    - Clean separation of concerns, testable in isolation.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.sql import func

from ..config import settings
from ..core.time_utils import utc_naive
from ..database import get_db_context
from ..models.enums import JobStatus
from ..models.job import Job
from ..background import log_task_result
from ..websocket.manager import WebSocketManager

logger = logging.getLogger(__name__)
UTC = timezone.utc
BATCH_SIZE = 100


@dataclass
class _PromotionEvent:
    job_id: str
    retry_count: int


class RetryScheduler:
    """
    Asyncio background task that promotes RETRY_WAIT → QUEUED.
    """

    def __init__(self, ws_manager: WebSocketManager) -> None:
        self._ws = ws_manager
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="retry-scheduler")
        self._task.add_done_callback(
            lambda t: log_task_result(t, "retry-scheduler")
        )
        logger.info(
            "Retry scheduler started (interval=%ds)",
            settings.retry_scheduler_interval_seconds,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Retry scheduler stopped")

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._sweep()
            except Exception:
                logger.exception("Retry scheduler sweep failed")
            await asyncio.sleep(settings.retry_scheduler_interval_seconds)

    async def _sweep(self) -> None:
        events: list[_PromotionEvent] = []

        async with get_db_context() as db:
            events = await self._promote_ready_jobs(db)

        # Broadcast AFTER commit
        for ev in events:
            await self._ws.emit_job_status_changed(
                job_id=uuid.UUID(ev.job_id),
                previous_status=JobStatus.RETRY_WAIT,
                new_status=JobStatus.QUEUED,
            )

        if events:
            logger.info("Retry scheduler promoted %d jobs to QUEUED", len(events))

    async def _promote_ready_jobs(
        self, db
    ) -> list[_PromotionEvent]:
        """
        Batch-promote all RETRY_WAIT jobs ready for retry.
        FOR UPDATE SKIP LOCKED avoids conflicts with concurrent scheduler instances.
        """
        # BUG-FIX: Also include next_retry_after IS NULL (delay=0 retries).
        # The original query only matched IS NOT NULL AND <= NOW(), leaving
        # delay=0 jobs permanently stuck in RETRY_WAIT.
        stmt = (
            select(Job)
            .where(
                Job.status == JobStatus.RETRY_WAIT,
                or_(
                    Job.next_retry_after.is_(None),       # Immediately claimable (delay=0)
                    Job.next_retry_after <= func.now(),   # Delay has passed
                ),
            )
            .with_for_update(skip_locked=True)
            .limit(BATCH_SIZE)
        )
        result = await db.execute(stmt)
        jobs = result.scalars().all()
        if not jobs:
            return []

        events: list[_PromotionEvent] = []
        for job in jobs:
            job.status = JobStatus.QUEUED
            job.next_retry_after = None
            job.updated_at = utc_naive()
            events.append(_PromotionEvent(job_id=str(job.id), retry_count=job.retry_count))

        await db.flush()
        logger.debug("Promoted %d RETRY_WAIT → QUEUED", len(events))
        return events
