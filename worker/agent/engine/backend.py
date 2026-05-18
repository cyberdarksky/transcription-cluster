"""
Backend detection and capability reporting.

Priority:
  1. mlx-whisper   — Native Apple Silicon (MLX framework, Metal GPU)
  2. faster-whisper — CTranslate2 with CoreML / CPU fallback
  3. openai-whisper — Original, CPU-only, slowest

Detection is cached at import time so repeated calls are free.
"""
from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

BackendName = Literal["mlx", "faster_whisper", "openai"]


@dataclass(frozen=True)
class BackendCapability:
    name: BackendName
    version: str
    is_apple_silicon: bool
    has_metal: bool
    notes: list[str] = field(default_factory=list)


def detect_backend(preferred: BackendName = "mlx") -> BackendCapability:
    """
    Detect which transcription backend is available.
    Returns the best available backend, respecting `preferred` order.

    This is called once at worker startup; the result is stored on the
    pipeline and passed into every subprocess invocation.
    """
    is_apple_silicon = platform.machine() == "arm64" and platform.system() == "Darwin"
    has_metal = False

    if is_apple_silicon:
        try:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=3,
            )
            has_metal = "Metal" in result.stdout or "Apple" in result.stdout
        except Exception:
            has_metal = True  # Assume Metal on Apple Silicon if check fails

    order: list[BackendName] = _build_order(preferred)

    for name in order:
        cap = _try_backend(name, is_apple_silicon, has_metal)
        if cap:
            if cap.name != preferred:
                logger.warning(
                    "Preferred backend %r unavailable; using %r",
                    preferred, cap.name,
                )
            else:
                logger.info("Backend: %s %s", cap.name, cap.version)
            return cap

    raise RuntimeError(
        "No transcription backend available. "
        "Install mlx-whisper, faster-whisper, or openai-whisper."
    )


def _build_order(preferred: BackendName) -> list[BackendName]:
    default: list[BackendName] = ["mlx", "faster_whisper", "openai"]
    if preferred in default:
        default.remove(preferred)
        return [preferred] + default
    return default


def _try_backend(
    name: BackendName,
    is_apple_silicon: bool,
    has_metal: bool,
) -> BackendCapability | None:
    notes: list[str] = []

    if name == "mlx":
        try:
            import mlx_whisper  # type: ignore[import]
            import mlx  # type: ignore[import]
            version = getattr(mlx, "__version__", "unknown")
            if not is_apple_silicon:
                notes.append("WARNING: mlx-whisper on non-Apple-Silicon will be slow")
            return BackendCapability(
                name="mlx",
                version=version,
                is_apple_silicon=is_apple_silicon,
                has_metal=has_metal,
                notes=notes,
            )
        except ImportError:
            return None

    if name == "faster_whisper":
        try:
            import faster_whisper  # type: ignore[import]
            version = getattr(faster_whisper, "__version__", "unknown")
            if is_apple_silicon:
                notes.append(
                    "faster-whisper on Apple Silicon: CoreML unavailable; using CPU. "
                    "Consider mlx-whisper for ~3x speedup."
                )
            return BackendCapability(
                name="faster_whisper",
                version=version,
                is_apple_silicon=is_apple_silicon,
                has_metal=False,
                notes=notes,
            )
        except ImportError:
            return None

    if name == "openai":
        try:
            import whisper  # type: ignore[import]
            version = getattr(whisper, "__version__", "unknown")
            notes.append("openai-whisper: CPU-only, ~3-5x slower than mlx-whisper")
            return BackendCapability(
                name="openai",
                version=version,
                is_apple_silicon=is_apple_silicon,
                has_metal=False,
                notes=notes,
            )
        except ImportError:
            return None

    return None
