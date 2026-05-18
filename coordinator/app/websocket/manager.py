from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

from ..models.enums import JobStatus, WorkerStatus
from fastapi.websockets import WebSocketState

logger = logging.getLogger(__name__)


def _status_value(status: str | JobStatus | WorkerStatus) -> str:
    if isinstance(status, (JobStatus, WorkerStatus)):
        return status.value
    return str(status)


class WebSocketManager:
    """
    Central WebSocket connection registry for the coordinator.

    Design notes:
    - Single asyncio event loop (single Uvicorn worker) — no locking required.
    - Dashboard connections: fan-out broadcast to all connected clients.
    - Worker connections: point-to-point by worker_id.
    - Pending commands: fallback queue delivered on next heartbeat
      when the worker WebSocket is not connected.
    """

    def __init__(self) -> None:
        self._dashboard_connections: set[WebSocket] = set()
        self._worker_connections: dict[str, WebSocket] = {}
        # Fallback command queue for workers without an active WebSocket.
        self._pending_commands: dict[str, list[dict[str, Any]]] = {}

    # ── Dashboard connections ─────────────────────────────────────────────────

    async def connect_dashboard(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._dashboard_connections.add(websocket)
        logger.info(
            "Dashboard client connected",
            extra={"client": str(websocket.client), "total": len(self._dashboard_connections)},
        )

    def disconnect_dashboard(self, websocket: WebSocket) -> None:
        self._dashboard_connections.discard(websocket)
        logger.info(
            "Dashboard client disconnected",
            extra={"total": len(self._dashboard_connections)},
        )

    async def broadcast_to_dashboard(self, message: dict[str, Any]) -> None:
        """
        Broadcast a message to all connected dashboard clients.
        Silently removes clients that have disconnected.
        """
        if not self._dashboard_connections:
            return

        stale: set[WebSocket] = set()
        for ws in self._dashboard_connections:
            if ws.client_state != WebSocketState.CONNECTED:
                stale.add(ws)
                continue
            try:
                await ws.send_json(message)
            except Exception:
                stale.add(ws)

        self._dashboard_connections -= stale
        if stale:
            logger.debug("Removed %d stale dashboard connections", len(stale))

    @property
    def dashboard_connection_count(self) -> int:
        return len(self._dashboard_connections)

    # ── Worker connections ────────────────────────────────────────────────────

    async def connect_worker(self, websocket: WebSocket, worker_id: str) -> None:
        await websocket.accept()
        self._worker_connections[worker_id] = websocket
        logger.info(
            "Worker WebSocket connected",
            extra={"worker_id": worker_id, "total": len(self._worker_connections)},
        )

    def disconnect_worker(self, worker_id: str) -> None:
        self._worker_connections.pop(worker_id, None)
        logger.info(
            "Worker WebSocket disconnected",
            extra={"worker_id": worker_id, "total": len(self._worker_connections)},
        )

    async def send_to_worker(self, worker_id: str, command: dict[str, Any]) -> bool:
        """
        Send a command to a specific worker.
        Returns True if delivered via WebSocket, False if queued as pending.
        """
        ws = self._worker_connections.get(worker_id)
        if ws is not None and ws.client_state == WebSocketState.CONNECTED:
            try:
                await ws.send_json(command)
                return True
            except Exception:
                self._worker_connections.pop(worker_id, None)
                logger.warning("Worker WebSocket send failed; falling back to pending queue",
                               extra={"worker_id": worker_id})

        # Fallback: add to pending commands delivered on next heartbeat
        self._pending_commands.setdefault(worker_id, []).append(command)
        return False

    def is_worker_connected(self, worker_id: str) -> bool:
        ws = self._worker_connections.get(worker_id)
        return ws is not None and ws.client_state == WebSocketState.CONNECTED

    @property
    def worker_connection_count(self) -> int:
        return len(self._worker_connections)

    # ── Pending commands ──────────────────────────────────────────────────────

    def pop_pending_commands(self, worker_id: str) -> list[dict[str, Any]]:
        """Return and clear all queued commands for a worker (delivered via heartbeat)."""
        return self._pending_commands.pop(worker_id, [])

    # ── Convenience broadcast helpers ─────────────────────────────────────────

    async def emit_job_progress(
        self,
        job_id: uuid.UUID,
        progress_percent: float,
        elapsed_seconds: float | None,
        worker_id: uuid.UUID | None,
    ) -> None:
        await self.broadcast_to_dashboard(
            {
                "type": "job_progress",
                "data": {
                    "job_id": str(job_id),
                    "progress_percent": progress_percent,
                    "elapsed_seconds": elapsed_seconds,
                    "worker_id": str(worker_id) if worker_id else None,
                },
            }
        )

    async def emit_job_status_changed(
        self,
        job_id: uuid.UUID,
        previous_status: str | JobStatus,
        new_status: str | JobStatus,
        worker_id: uuid.UUID | None = None,
        worker_hostname: str | None = None,
    ) -> None:
        await self.broadcast_to_dashboard(
            {
                "type": "job_status_changed",
                "data": {
                    "job_id": str(job_id),
                    "previous_status": _status_value(previous_status),
                    "new_status": _status_value(new_status),
                    "worker_id": str(worker_id) if worker_id else None,
                    "worker_hostname": worker_hostname,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }
        )

    async def emit_job_created(
        self,
        job_id: uuid.UUID,
        input_path: str,
        status: JobStatus = JobStatus.QUEUED,
    ) -> None:
        now = datetime.now(timezone.utc)
        await self.broadcast_to_dashboard(
            {
                "type": "job_created",
                "data": {
                    "job_id": str(job_id),
                    "input_path": input_path,
                    "status": _status_value(status),
                    "created_at": now.isoformat(),
                    "timestamp": now.isoformat(),
                },
            }
        )

    async def emit_worker_status_changed(
        self,
        worker_id: uuid.UUID,
        hostname: str,
        new_status: str | WorkerStatus,
        previous_status: str | WorkerStatus | None = None,
    ) -> None:
        await self.broadcast_to_dashboard(
            {
                "type": "worker_status_changed",
                "data": {
                    "worker_id": str(worker_id),
                    "hostname": hostname,
                    "previous_status": _status_value(previous_status) if previous_status else None,
                    "new_status": _status_value(new_status),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }
        )

    async def emit_worker_metrics(
        self,
        worker_id: uuid.UUID,
        hostname: str,
        cpu_percent: float | None,
        memory_percent: float | None,
        gpu_percent: float | None,
        job_progress: float | None,
    ) -> None:
        await self.broadcast_to_dashboard(
            {
                "type": "worker_metrics",
                "data": {
                    "worker_id": str(worker_id),
                    "hostname": hostname,
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory_percent,
                    "gpu_percent": gpu_percent,
                    "current_job_progress": job_progress,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }
        )

    async def emit_system_alert(
        self,
        severity: str,
        code: str,
        message: str,
    ) -> None:
        await self.broadcast_to_dashboard(
            {
                "type": "system_alert",
                "data": {
                    "severity": severity,
                    "code": code,
                    "message": message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }
        )
