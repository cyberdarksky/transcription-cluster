"""
JSON output generator.

Produces a rich JSON transcript that includes:
- Full text
- Per-segment timing and confidence
- Per-word timing (if word_timestamps was enabled)
- Processing metadata (RTF, worker ID, model)

The JSON is written with ensure_ascii=False so Turkish characters (ş, ğ, ü, ö, ı, ç)
are stored as native Unicode rather than escape sequences.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def generate_json(
    *,
    result_text: str,
    result_language: str,
    segments: list[dict[str, Any]],
    original_filename: str,
    input_path: str,
    relative_folder: str,
    worker_id: uuid.UUID,
    worker_hostname: str,
    whisper_model: str,
    audio_duration_seconds: float,
    processing_time_seconds: float,
    output_path: Path,
) -> None:
    """Write the JSON transcript to output_path."""
    rtf = (
        round(processing_time_seconds / audio_duration_seconds, 4)
        if audio_duration_seconds > 0
        else None
    )

    word_count = sum(len(s.get("words", [])) for s in segments)

    payload = {
        "version": "1.0",
        "file": {
            "name": original_filename,
            "path": input_path,
            "folder": relative_folder,
        },
        "transcription": {
            "language": result_language,
            "model": whisper_model,
            "text": result_text,
            "segment_count": len(segments),
            "word_count": word_count,
        },
        "segments": [
            {
                "id": seg.get("id", i),
                "start": seg.get("start", 0),
                "end": seg.get("end", 0),
                "duration": round(
                    float(seg.get("end", 0)) - float(seg.get("start", 0)), 3
                ),
                "text": seg.get("text", "").strip(),
                "avg_logprob": seg.get("avg_logprob"),
                "no_speech_prob": seg.get("no_speech_prob"),
                "words": seg.get("words", []),
            }
            for i, seg in enumerate(segments)
        ],
        "metadata": {
            "transcribed_at": datetime.now(timezone.utc).isoformat(),
            "worker_id": str(worker_id),
            "worker_hostname": worker_hostname,
            "audio_duration_seconds": round(audio_duration_seconds, 3),
            "processing_time_seconds": round(processing_time_seconds, 3),
            "real_time_factor": rtf,
        },
    }

    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
