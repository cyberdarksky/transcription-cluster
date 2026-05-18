"""
Heartbeat background task.

Sends a heartbeat to the coordinator every heartbeat_interval_seconds.
The coordinator uses the heartbeat to:
  1. Track worker liveness (90s timeout → worker marked offline).
  2. Renew the lease on the current job (implicit via current_job_id field).
  3. Deliver pending commands (PAUSE/RESUME/CANCEL) as fallback if WebSocket
     is unavailable.

Backoff behavior:
  If the heartbeat fails (network error), the task waits RETRY_INTERVAL seconds
  before retrying — it does NOT stop. This is intentional: the coordinator's
  heartbeat timeout is 90 seconds; transient failures of <90s are recovered
  automatically. The worker keeps running, processes files, and the coordinator
  re-syncs on the next successful heartbeat.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from .background import log_task_result
from .coordinator_client import CoordinatorClient
from .metrics import collect_metrics
from .state import WorkerRunStatus, WorkerState

logger = logging.getLogger(__name__)

_RETRY_INTERVAL = 5  # seconds between retries after failure


class HeartbeatService:
    """
    Runs as an asyncio background task.
    Sends periodic heartbeats and delivers pending coordinator commands
    to state.command_queue.
    """

    def __init__(
        self,
        client: CoordinatorClient,
        worker_id: uuid.UUID,
        state: WorkerState,
        interval_seconds: int = 30,
    ) -> None:
        self._client = client
        self._worker_id = worker_id
        self._state = state
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="heartbeat")
        self._task.add_done_callback(
            lambda t: log_task_result(t, "heartbeat")
        )
        logger.info(
            "Heartbeat service started (interval=%ds)", self._interval
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while not self._state.is_stopping():
            try:
                await self._send_heartbeat()
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Heartbeat failed: %s", exc)
                await asyncio.sleep(_RETRY_INTERVAL)

    async def _send_heartbeat(self) -> None:
        status = _run_status_to_worker_status(self._state.run_status)
        metrics = await collect_metrics()

        pending_commands, lease_valid = await self._client.heartbeat(
            worker_id=self._worker_id,
            status=status,
            current_job_id=self._state.current_job_id,
            job_progress_percent=(
                self._state.job_progress_percent
                if self._state.current_job_id
                else None
            ),
            metrics=metrics.to_dict(),
        )

        if (
            self._state.current_job_id is not None
            and lease_valid is False
        ):
            logger.warning(
                "Lease lost on job %s — requesting cancel",
                self._state.current_job_id,
            )
            await self._state.command_queue.put({
                "type": "CANCEL_JOB",
                "job_id": str(self._state.current_job_id),
            })

        # Deliver any commands the coordinator queued for us
        # (fallback when WebSocket is not connected)
        if pending_commands:
            # BUG-FIX: log was inside the for loop, so it fired once per
            # command instead of once per batch, and "if pending_commands"
            # was always True inside the loop body. Now logged once with count.
            logger.debug(
                "Received %d pending command(s) via heartbeat fallback",
                len(pending_commands),
            )
            for cmd in pending_commands:
                await self._state.command_queue.put(cmd)


def _run_status_to_worker_status(run_status: WorkerRunStatus) -> str:
    mapping = {
        WorkerRunStatus.STARTING:  "online",
        WorkerRunStatus.IDLE:      "idle",
        WorkerRunStatus.BUSY:      "busy",
        WorkerRunStatus.PAUSED:    "paused",
        WorkerRunStatus.DRAINING:  "idle",
        WorkerRunStatus.STOPPING:  "idle",
    }
    return mapping.get(run_status, "idle")
