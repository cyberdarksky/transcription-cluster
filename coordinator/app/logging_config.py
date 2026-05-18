from __future__ import annotations

import logging
import logging.config
import sys
from typing import Any

from pythonjsonlogger import jsonlogger


class _CoordinatorJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter with standard fields for production log aggregation."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["service"] = "transcription-coordinator"
        log_record["logger"] = record.name
        # Remove noisy fields duplicated by JSON formatter
        log_record.pop("taskName", None)


def setup_logging(log_level: str = "INFO", json_logs: bool = True) -> None:
    """
    Configure the root logger and all library loggers.
    Call once at application startup before any other imports log.
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    if json_logs:
        formatter: logging.Formatter = _CoordinatorJsonFormatter(
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

    # Quiet noisy third-party loggers
    for noisy in (
        "uvicorn.access",
        "watchdog.observers.inotify_buffer",
        "zeroconf",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # SQLAlchemy engine logs only in DEBUG mode
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.DEBUG if log_level == "DEBUG" else logging.WARNING
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
