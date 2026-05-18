"""Shared helpers for asyncio background tasks."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def log_task_result(task: asyncio.Task[object], name: str) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("%s background task crashed", name, exc_info=exc)
