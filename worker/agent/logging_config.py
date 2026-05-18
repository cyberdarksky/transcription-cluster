from __future__ import annotations

import logging
import sys
from typing import Any

from pythonjsonlogger import jsonlogger


class _WorkerJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["service"] = "transcription-worker"
        log_record["logger"] = record.name
        log_record.pop("taskName", None)


def setup_logging(log_level: str = "INFO", json_logs: bool = True) -> None:
    """Configure root logger. Call once at application entry."""
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    if json_logs:
        formatter: logging.Formatter = _WorkerJsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.handlers.clear()
    root.addHandler(handler)

    for noisy in ("zeroconf", "websockets.client", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
