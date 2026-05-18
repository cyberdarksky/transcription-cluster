from __future__ import annotations

import asyncio
import logging

from sqlalchemy import delete
from sqlalchemy.sql import func

from ..config import settings
from ..database import get_db_context
from ..models.job_event import JobEvent
from ..background import log_task_result
from ..models.worker_metric import WorkerMetric

logger = logging.getLogger(__name__)

_DAILY_INTERVAL = 24 * 3600  # seconds


class MaintenanceService:
    """
    Runs cleanup tasks once per day as an asyncio background task.
    No external cron required — the coordinator manages its own housekeeping.

    Uses ORM-level DELETE statements with parameterized binds (no f-string SQL).

    Tasks:
    1. Delete worker_metrics older than worker_metrics_retention_days.
    2. Delete non-'completed' job_events older than job_events_retention_days.
       Completed events are retained permanently for auditability.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._daily_loop(), name="maintenance")
        self._task.add_done_callback(
            lambda t: log_task_result(t, "maintenance")
        )
        logger.info("Maintenance service started (runs daily after 1h initial delay)")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Maintenance service stopped")

    async def run_now(self) -> dict[str, int]:
        """Execute maintenance immediately (exposed via /api/v1/system/maintenance)."""
        return await self._run_cleanup()

    async def _daily_loop(self) -> None:
        # Initial delay gives the system time to stabilise after startup.
        await asyncio.sleep(3600)
        while self._running:
            try:
                stats = await self._run_cleanup()
                logger.info("Daily maintenance completed", extra=stats)
            except Exception:
                logger.exception("Daily maintenance failed")
            await asyncio.sleep(_DAILY_INTERVAL)

    async def _run_cleanup(self) -> dict[str, int]:
        metrics_days = settings.worker_metrics_retention_days
        events_days = settings.job_events_retention_days

        deleted: dict[str, int] = {}

        async with get_db_context() as db:
            # ── worker_metrics: retain last N days ─────────────────────────────
            # Uses MAKE_INTERVAL for safe integer binding (no f-string SQL).
            cutoff_metrics = func.now() - func.make_interval(0, 0, 0, metrics_days)
            result = await db.execute(
                delete(WorkerMetric).where(WorkerMetric.recorded_at < cutoff_metrics)
            )
            deleted["worker_metrics_deleted"] = result.rowcount

            # ── job_events: retain completed events forever; clean old others ───
            cutoff_events = func.now() - func.make_interval(0, 0, 0, events_days)
            result = await db.execute(
                delete(JobEvent).where(
                    JobEvent.created_at < cutoff_events,
                    JobEvent.event_type != "completed",
                )
            )
            deleted["job_events_deleted"] = result.rowcount

        return deleted
