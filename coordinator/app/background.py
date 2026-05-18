"""Shared helpers for asyncio background tasks."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def log_task_result(task: asyncio.Task[object], name: str) -> None:
    """Log unexpected task failures; ignore normal cancellation."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("%s background task crashed", name, exc_info=exc)


async def stop_with_timeout(
    coro,
    name: str,
    *,
    timeout: float = 10.0,
) -> None:
    """Await a service stop coroutine with a bounded wait."""
    try:
        await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("%s did not stop within %.0fs", name, timeout)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("%s stop failed", name)
