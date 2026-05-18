"""
Temporary file management.

Each job gets its own directory: <temp_dir>/<job_id>/
  input.mp3   — downloaded MP3
  output.srt  — generated SRT
  output.json — generated JSON

Cleanup is called:
  1. After successful upload (always — even if upload fails, we clean up to
     avoid filling disk).
  2. On job failure or cancellation.
  3. On worker startup (cleans up any leftover directories from a crash).
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def job_dir(temp_dir: Path, job_id: object) -> Path:
    return temp_dir / str(job_id)


def mp3_path(temp_dir: Path, job_id: object) -> Path:
    return job_dir(temp_dir, job_id) / "input.mp3"


def srt_path(temp_dir: Path, job_id: object) -> Path:
    return job_dir(temp_dir, job_id) / "output.srt"


def json_path(temp_dir: Path, job_id: object) -> Path:
    return job_dir(temp_dir, job_id) / "output.json"


def prepare_job_dir(temp_dir: Path, job_id: object) -> Path:
    """Create a clean job directory (removes leftovers from a crashed prior attempt)."""
    d = job_dir(temp_dir, job_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


def drain_command_queue(queue: object) -> int:
    """Drop stale coordinator commands so they cannot affect the next job."""
    drained = 0
    get_nowait = getattr(queue, "get_nowait", None)
    if get_nowait is None:
        return 0
    while True:
        try:
            get_nowait()
            drained += 1
        except asyncio.QueueEmpty:
            break
    return drained


def cleanup_job(temp_dir: Path, job_id: object) -> None:
    """Remove the job's temp directory. Silent on error."""
    d = job_dir(temp_dir, job_id)
    try:
        shutil.rmtree(d, ignore_errors=True)
        logger.debug("Cleaned up job temp dir", extra={"dir": str(d)})
    except Exception as exc:
        logger.warning("Failed to clean up job temp dir: %s", exc, extra={"dir": str(d)})


def cleanup_on_startup(temp_dir: Path) -> int:
    """
    Remove all leftover job directories from a previous crashed run.
    Returns the number of directories removed.
    """
    if not temp_dir.exists():
        temp_dir.mkdir(parents=True, exist_ok=True)
        return 0

    removed = 0
    for child in temp_dir.iterdir():
        if child.is_dir():
            try:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
            except Exception:
                pass

    if removed:
        logger.info("Startup cleanup: removed %d leftover job directories", removed)
    return removed
