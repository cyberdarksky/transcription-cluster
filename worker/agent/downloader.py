"""
MP3 Downloader with resumable download support.

Design:
- Streams the file chunk by chunk; no full-file buffering.
- If the destination file already exists (partial download from a crashed run),
  sends a Range: bytes=<existing_size>- header to resume from where it stopped.
- Verifies the final file size if the coordinator provides Content-Length.
- Falls back to a fresh download if Range is rejected (416 or server ignores it).
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

from .coordinator_client import CoordinatorClient

logger = logging.getLogger(__name__)


async def download_mp3(
    client: CoordinatorClient,
    download_url: str,
    dest_path: Path,
    chunk_size: int = 1_048_576,
) -> int:
    """
    Download the MP3 file to dest_path.
    Returns total bytes of the final file.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Downloading MP3",
        extra={"url": download_url, "dest": str(dest_path)},
    )
    total = await client.download_file(download_url, dest_path, chunk_size)
    logger.info(
        "Download complete",
        extra={"bytes": total, "dest": str(dest_path)},
    )
    return total


async def get_audio_duration(file_path: Path) -> float | None:
    """
    Get audio duration in seconds.

    Tries:
    1. ffprobe (accurate, requires Homebrew or pre-installed)
    2. File-size estimation (rough: assumes 128kbps average bitrate)
    """
    duration = await _duration_via_ffprobe(file_path)
    if duration is not None:
        return duration

    # Rough estimate: 128kbps MP3 = ~16 KB/second
    try:
        size = file_path.stat().st_size
        return size / 16_000
    except OSError:
        return None


async def _duration_via_ffprobe(file_path: Path) -> float | None:
    """Use ffprobe to get exact duration. Returns None if unavailable."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(file_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        if proc.returncode != 0:
            return None
        import json
        data = json.loads(stdout.decode())
        return float(data["format"]["duration"])
    except Exception:
        return None
