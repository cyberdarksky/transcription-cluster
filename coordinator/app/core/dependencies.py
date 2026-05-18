from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..websocket.manager import WebSocketManager

# ── Database session dependency ───────────────────────────────────────────────

DbSession = Annotated[AsyncSession, Depends(get_db)]

# ── WebSocket manager singleton ────────────────────────────────────────────────
# Injected via app.state; accessed through this dependency.


def get_ws_manager(request: object) -> WebSocketManager:
    from fastapi import Request

    assert isinstance(request, Request)
    return request.app.state.ws_manager  # type: ignore[no-any-return]


WsManager = Annotated[WebSocketManager, Depends(get_ws_manager)]
