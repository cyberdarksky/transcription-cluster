"""
WebSocket client for real-time coordinator commands.

The coordinator sends commands (PAUSE_JOB, RESUME_JOB, CANCEL_JOB) via WebSocket.
The worker sends acknowledgements and pong messages back.

Design:
- Runs as an asyncio background task.
- On disconnect: reconnects with exponential backoff.
- Commands are placed into state.command_queue for the job runner to process.
- If WebSocket is unavailable, coordinator falls back to pending_commands
  in the heartbeat response — so this channel is reliable but not critical.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import websockets
from websockets.exceptions import ConnectionClosed

from .background import log_task_result
from .config import WorkerConfig
from .state import WorkerState, WorkerRunStatus

logger = logging.getLogger(__name__)
UTC = timezone.utc

_RECONNECT_DELAYS = [5, 10, 20, 40, 60, 120]  # seconds


class WorkerWebSocketClient:
    """
    Maintains the WebSocket connection to the coordinator.
    Receives commands and dispatches them to state.command_queue.
    """

    def __init__(
        self,
        base_ws_url: str,
        worker_id: uuid.UUID,
        state: WorkerState,
        config: WorkerConfig,
    ) -> None:
        self._base_url = base_ws_url.rstrip("/")
        self._worker_id = worker_id
        self._state = state
        self._config = config
        self._ws = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the WebSocket client as a background task."""
        self._task = asyncio.create_task(self._loop(), name="ws-client")
        self._task.add_done_callback(
            lambda t: log_task_result(t, "ws-client")
        )

    async def stop(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def send_ack(self, ack_type: str) -> None:
        """Send an acknowledgement back to the coordinator."""
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": ack_type}))
            except Exception:
                pass  # Non-critical; coordinator can detect via heartbeat

    # ── Internal loop ─────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        attempt = 0
        while not self._state.is_stopping():
            ws_url = (
                f"{self._base_url}/ws/worker?worker_id={self._worker_id}"
            )
            try:
                await self._connect_and_run(ws_url)
                attempt = 0  # Reset backoff on successful connection
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if self._state.is_stopping():
                    break
                delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
                logger.warning(
                    "WebSocket disconnected; reconnecting in %ds: %s",
                    delay, exc,
                    extra={"attempt": attempt + 1},
                )
                attempt += 1
                await asyncio.sleep(delay)

    async def _connect_and_run(self, ws_url: str) -> None:
        async with websockets.connect(
            ws_url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
            max_size=1_048_576,
        ) as ws:
            self._ws = ws
            logger.info("WebSocket connected to coordinator")

            async for raw in ws:
                if self._state.is_stopping():
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle_message(msg, ws)

        self._ws = None

    async def _handle_message(self, msg: dict, ws) -> None:
        msg_type = msg.get("type", "")

        if msg_type == "PING":
            await ws.send(json.dumps({"type": "PONG"}))
            return

        if msg_type in ("PAUSE_JOB", "RESUME_JOB", "CANCEL_JOB"):
            await self._state.command_queue.put(msg)
            ack = msg_type.replace("_JOB", "_ACK")
            await ws.send(json.dumps({"type": ack}))
            logger.info(
                "Received and acknowledged command",
                extra={"command": msg_type, "job_id": msg.get("job_id")},
            )
            return

        if msg_type == "connected":
            logger.debug("WebSocket handshake confirmed", extra={"data": msg})
            return

        logger.debug("Unknown WebSocket message type: %s", msg_type)
