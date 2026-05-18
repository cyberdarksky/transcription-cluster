"""
SRT file generator.

Converts the list of segments returned by mlx-whisper into a valid
SRT (SubRip Subtitle) file.

SRT format:
    <index>
    <HH:MM:SS,mmm> --> <HH:MM:SS,mmm>
    <text>
    <blank line>

Turkish characters (ş, ğ, ü, ö, ı, ç) require UTF-8 encoding — always used.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def generate_srt(segments: list[dict[str, Any]], output_path: Path) -> None:
    """
    Write a valid SRT file from a list of whisper segments.
    output_path is written atomically (tmp → rename) by the caller.
    """
    lines: list[str] = []

    for i, seg in enumerate(segments, start=1):
        start_ts = _format_timestamp(float(seg.get("start", 0)))
        end_ts = _format_timestamp(float(seg.get("end", 0)))
        text = seg.get("text", "").strip()

        if not text:
            continue

        lines.append(str(i))
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(text)
        lines.append("")  # Blank line separator

    output_path.write_text("\n".join(lines), encoding="utf-8")


def _format_timestamp(seconds: float) -> str:
    """
    Convert float seconds to SRT timestamp: HH:MM:SS,mmm

    Examples:
        0.0   → "00:00:00,000"
        65.25 → "00:01:05,250"
        3661.5 → "01:01:01,500"
    """
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
