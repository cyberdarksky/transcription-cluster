"""
Transcription metrics collected during and after inference.

Metrics are attached to every TranscriptionResult and logged for
performance analysis and benchmarking.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TranscriptionMetrics:
    """Complete timing and quality metrics for one transcription job."""

    backend: str = ""
    model_path: str = ""

    # ── Timing (seconds) ──────────────────────────────────────────────────────
    audio_duration_seconds: float = 0.0
    inference_seconds: float = 0.0        # Excludes time spent paused (SIGSTOP)
    total_wall_seconds: float = 0.0       # Wall clock including pauses
    paused_seconds: float = 0.0           # Total time frozen (SIGSTOP)
    model_load_seconds: float = 0.0       # First-job cost; 0 for subsequent jobs

    # ── Quality ───────────────────────────────────────────────────────────────
    segment_count: int = 0
    word_count: int = 0
    language_detected: str = ""
    language_probability: float = 0.0

    # ── Performance ───────────────────────────────────────────────────────────
    @property
    def rtf(self) -> float | None:
        """Real-Time Factor = inference_seconds / audio_duration_seconds.
        <1.0 means faster than real time."""
        if self.audio_duration_seconds > 0:
            return round(self.inference_seconds / self.audio_duration_seconds, 4)
        return None

    @property
    def speedup(self) -> float | None:
        """How many times faster than real time (1/RTF)."""
        r = self.rtf
        return round(1.0 / r, 2) if r and r > 0 else None

    @property
    def segments_per_second(self) -> float:
        if self.inference_seconds > 0:
            return round(self.segment_count / self.inference_seconds, 2)
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "audio_duration_seconds": round(self.audio_duration_seconds, 3),
            "inference_seconds": round(self.inference_seconds, 3),
            "total_wall_seconds": round(self.total_wall_seconds, 3),
            "paused_seconds": round(self.paused_seconds, 3),
            "model_load_seconds": round(self.model_load_seconds, 3),
            "rtf": self.rtf,
            "speedup_factor": self.speedup,
            "segment_count": self.segment_count,
            "word_count": self.word_count,
            "language_detected": self.language_detected,
            "segments_per_second": self.segments_per_second,
        }

    def log_summary(self, logger: Any, job_id: str = "") -> None:
        logger.info(
            "Transcription metrics",
            extra={
                "job_id": job_id,
                **self.to_dict(),
            },
        )


@dataclass
class InferenceTimer:
    """Context manager / manual timer for measuring inference phases."""

    _start: float = field(default_factory=time.monotonic, init=False)
    _paused_since: float | None = field(default=None, init=False)
    _total_paused: float = field(default=0.0, init=False)

    def pause(self) -> None:
        if self._paused_since is None:
            self._paused_since = time.monotonic()

    def resume(self) -> None:
        if self._paused_since is not None:
            self._total_paused += time.monotonic() - self._paused_since
            self._paused_since = None

    @property
    def elapsed_wall(self) -> float:
        return time.monotonic() - self._start

    @property
    def elapsed_effective(self) -> float:
        """Wall time minus paused time."""
        extra = (time.monotonic() - self._paused_since) if self._paused_since else 0.0
        return self.elapsed_wall - self._total_paused - extra

    @property
    def paused_seconds(self) -> float:
        extra = (time.monotonic() - self._paused_since) if self._paused_since else 0.0
        return self._total_paused + extra
