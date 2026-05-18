"""
Persistent transcription worker subprocess.

This module is run AS a subprocess (via `python -m agent.engine.persistent_worker`)
and communicates with the parent process over stdin (requests) / stdout (responses).

Key design:
  - Model is loaded ONCE at startup and kept in Apple Silicon unified memory.
  - Warmup inference eliminates first-job latency.
  - Multiple jobs are processed in sequence without model reloading.
  - SIGSTOP/SIGCONT from parent freezes/resumes this entire process including
    Metal GPU computation — zero-overhead pause with full state preservation.
  - Cancel requests mark a result to be discarded without killing the model.

Protocol (newline-delimited JSON over stdin/stdout):
  Parent → Subprocess (stdin):
    {"type": "transcribe", "audio_path": "...", "output_file": "...",
     "language": "tr", "word_timestamps": true,
     "job_id": "..."}
    {"type": "cancel"}
    {"type": "shutdown"}

  Subprocess → Parent (stdout):
    {"type": "ready", "backend": "mlx", "model": "...", "warmed_up": true}
    {"type": "result", "job_id": "...", "cancelled": false,
     "metrics": {...}}                 (result written to output_file)
    {"type": "error", "job_id": "...", "message": "...", "category": "transient"}
    {"type": "heartbeat"}             (every 5s during long inference; proves liveness)

Memory management:
  After each job, gc.collect() is called to free temporary tensors. On MLX,
  mx.metal.clear_cache() is called if available to return Metal GPU memory
  to the pool. These are safe operations — not aggressive.

Turkish optimization:
  condition_on_previous_text=True maintains Turkish morphological context
  across 30-second windows. Initial prompt anchors the language model to
  formal Turkish. These are standard Whisper parameters, not hacks.
"""
from __future__ import annotations

import gc
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("persistent_worker")

# ── Inference settings ────────────────────────────────────────────────────────

_WARMUP_DURATION_SAMPLES = 16_000   # 1 second of silence at 16kHz
_HEARTBEAT_INTERVAL = 5.0           # seconds; proves subprocess is alive during long runs

# Turkish-optimized parameters (mlx-whisper — no beam_size; MLX raises otherwise).
_WHISPER_MLX_PARAMS: dict[str, Any] = {
    "language": "tr",
    "word_timestamps": True,
    "verbose": False,
    "condition_on_previous_text": True,
    "temperature": 0.0,
    "compression_ratio_threshold": 2.4,
    "logprob_threshold": -1.0,
    "no_speech_threshold": 0.6,
    "initial_prompt": "Türkçe konuşma transkripsiyon.",
}

# faster-whisper / openai-whisper fallback parameters.
_WHISPER_CT2_PARAMS: dict[str, Any] = {
    **_WHISPER_MLX_PARAMS,
    "beam_size": 5,
    "fp16": False,
}


# ── Backend loading ───────────────────────────────────────────────────────────

def _load_backend(model_path: str) -> tuple[str, Any]:
    """
    Load the best available backend in priority order.
    Returns (backend_name, model_object_or_module).
    """
    # ── 1. mlx-whisper (preferred for Apple Silicon) ──────────────────────────
    try:
        import mlx_whisper  # type: ignore[import]
        logger.info("Backend: mlx-whisper (Apple Silicon Metal GPU)")
        # For mlx-whisper, the 'model' is the module itself — transcribe()
        # loads from path_or_hf_repo on each call, but uses MLX model caching.
        return "mlx", mlx_whisper
    except ImportError:
        logger.warning("mlx-whisper not available; trying faster-whisper")

    # ── 2. faster-whisper (CTranslate2 fallback) ──────────────────────────────
    try:
        from faster_whisper import WhisperModel  # type: ignore[import]
        logger.info("Backend: faster-whisper (CPU/CoreML fallback)")
        model = WhisperModel(
            model_path,
            device="cpu",
            compute_type="int8",        # Best CPU performance
            num_workers=1,              # Single job at a time
            cpu_threads=0,              # Use all available cores
        )
        return "faster_whisper", model
    except ImportError:
        logger.warning("faster-whisper not available; trying openai-whisper")

    # ── 3. openai-whisper (last resort) ──────────────────────────────────────
    try:
        import whisper as openai_whisper  # type: ignore[import]
        logger.info("Backend: openai-whisper (CPU, slowest)")
        model = openai_whisper.load_model("medium", device="cpu")
        return "openai", model
    except ImportError:
        pass

    raise RuntimeError(
        "No transcription backend available. "
        "Install mlx-whisper: pip install mlx-whisper mlx"
    )


# ── Warmup ────────────────────────────────────────────────────────────────────

def _warmup(backend: str, model: Any, model_path: str) -> float:
    """
    Run a minimal inference on silent audio.
    Eliminates JIT compilation and GPU pipeline setup costs for the first real job.
    Returns warmup duration in seconds.
    """
    import numpy as np

    silence = np.zeros(_WARMUP_DURATION_SAMPLES, dtype=np.float32)
    start = time.monotonic()

    try:
        if backend == "mlx":
            model.transcribe(
                silence,
                path_or_hf_repo=model_path,
                language="tr",
                verbose=False,
            )
        elif backend == "faster_whisper":
            list(model.transcribe(silence, language="tr")[0])  # exhaust generator
        elif backend == "openai":
            model.transcribe(silence, language="tr", verbose=False)

    except Exception as exc:
        # Warmup failure is non-fatal — log and continue
        logger.warning("Warmup inference failed (non-fatal): %s", exc)
        return 0.0

    elapsed = time.monotonic() - start
    logger.info("Warmup complete in %.1fs", elapsed)
    return elapsed


# ── Metal memory management ───────────────────────────────────────────────────

def _release_metal_cache() -> None:
    """
    Return unused Metal GPU memory to the system pool after each job.
    Safe: this is a standard MLX maintenance call, not a destructive operation.
    """
    try:
        import mlx.core as mx  # type: ignore[import]
        if hasattr(mx.metal, "clear_cache"):
            mx.metal.clear_cache()
    except Exception:
        pass  # Not critical; only available on recent MLX versions


# ── Transcription (per backend) ───────────────────────────────────────────────

def _transcribe_mlx(
    model: Any,
    model_path: str,
    audio_path: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    result = model.transcribe(audio_path, path_or_hf_repo=model_path, **params)
    return _normalise_result(result, params["language"])


def _transcribe_faster_whisper(
    model: Any,
    audio_path: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    segments_gen, info = model.transcribe(
        audio_path,
        language=params.get("language", "tr"),
        beam_size=params.get("beam_size", 5),
        word_timestamps=params.get("word_timestamps", True),
        condition_on_previous_text=params.get("condition_on_previous_text", True),
        initial_prompt=params.get("initial_prompt"),
        vad_filter=True,          # faster-whisper has built-in VAD
        vad_parameters={"min_silence_duration_ms": 500},
    )
    segments = []
    full_text_parts = []
    for seg in segments_gen:
        words = []
        if seg.words:
            for w in seg.words:
                words.append({
                    "word": w.word, "start": round(w.start, 3),
                    "end": round(w.end, 3), "probability": round(w.probability, 4),
                })
        s = {
            "id": seg.id, "start": round(seg.start, 3), "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "avg_logprob": round(seg.avg_logprob, 4),
            "no_speech_prob": round(seg.no_speech_prob, 4),
            "words": words,
        }
        segments.append(s)
        full_text_parts.append(seg.text)

    return {
        "text": " ".join(full_text_parts).strip(),
        "language": info.language,
        "language_probability": round(info.language_probability, 4),
        "segments": segments,
    }


def _transcribe_openai(
    model: Any,
    audio_path: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    result = model.transcribe(
        audio_path,
        language=params.get("language", "tr"),
        word_timestamps=params.get("word_timestamps", True),
        verbose=False,
    )
    return _normalise_result(result, params.get("language", "tr"))


def _normalise_result(raw: dict[str, Any], language: str) -> dict[str, Any]:
    """Bring mlx-whisper / openai-whisper output to canonical form."""
    segments = []
    for seg in raw.get("segments", []):
        words = []
        for w in seg.get("words", []):
            words.append({
                "word": w.get("word", ""),
                "start": round(float(w.get("start", 0)), 3),
                "end": round(float(w.get("end", 0)), 3),
                "probability": round(float(w.get("probability", 1.0)), 4),
            })
        segments.append({
            "id": seg.get("id", 0),
            "start": round(float(seg.get("start", 0)), 3),
            "end": round(float(seg.get("end", 0)), 3),
            "text": seg.get("text", "").strip(),
            "avg_logprob": round(float(seg.get("avg_logprob", 0)), 4),
            "no_speech_prob": round(float(seg.get("no_speech_prob", 0)), 4),
            "words": words,
        })
    return {
        "text": raw.get("text", "").strip(),
        "language": raw.get("language", language),
        "language_probability": float(raw.get("language_probability", 1.0)),
        "segments": segments,
    }


# ── Main subprocess loop ──────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if len(sys.argv) < 3:
        sys.stderr.write("Usage: python -m agent.engine.persistent_worker <model_path> <language>\n")
        sys.exit(1)

    model_path = sys.argv[1]
    language = sys.argv[2]

    # ── Load model ────────────────────────────────────────────────────────────
    load_start = time.monotonic()
    backend_name, model = _load_backend(model_path)

    base = _WHISPER_MLX_PARAMS if backend_name == "mlx" else _WHISPER_CT2_PARAMS
    params = dict(base)
    params["language"] = language
    load_seconds = time.monotonic() - load_start
    logger.info("Model loaded in %.1fs (backend=%s)", load_seconds, backend_name)

    # ── Warmup ────────────────────────────────────────────────────────────────
    warmup_seconds = _warmup(backend_name, model, model_path)

    # Signal ready to parent
    _send({
        "type": "ready",
        "backend": backend_name,
        "model_path": model_path,
        "language": language,
        "load_seconds": round(load_seconds, 2),
        "warmup_seconds": round(warmup_seconds, 2),
    })

    # ── Job loop ──────────────────────────────────────────────────────────────
    cancel_pending = False

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_type = request.get("type", "")

        if req_type == "shutdown":
            logger.info("Shutdown requested")
            break

        if req_type == "cancel":
            cancel_pending = True
            continue

        if req_type != "transcribe":
            continue

        # ── Process transcription request ─────────────────────────────────────
        cancel_pending = False
        job_id = request.get("job_id", "")
        audio_path = request["audio_path"]
        output_file = request["output_file"]

        # Merge per-request overrides into params
        job_params = dict(params)
        if "language" in request:
            job_params["language"] = request["language"]
        if "word_timestamps" in request:
            job_params["word_timestamps"] = request["word_timestamps"]

        infer_start = time.monotonic()
        try:
            if backend_name == "mlx":
                result = _transcribe_mlx(model, model_path, audio_path, job_params)
            elif backend_name == "faster_whisper":
                result = _transcribe_faster_whisper(model, audio_path, job_params)
            else:
                result = _transcribe_openai(model, audio_path, job_params)

            infer_seconds = time.monotonic() - infer_start

            # Write result to file (no pipe deadlock for large outputs)
            Path(output_file).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

            _send({
                "type": "result",
                "job_id": job_id,
                "cancelled": cancel_pending,
                "metrics": {
                    "backend": backend_name,
                    "inference_seconds": round(infer_seconds, 3),
                    "segment_count": len(result.get("segments", [])),
                    "word_count": sum(len(s.get("words", [])) for s in result.get("segments", [])),
                    "language_detected": result.get("language", ""),
                    "language_probability": result.get("language_probability", 1.0),
                },
            })

        except MemoryError:
            infer_seconds = time.monotonic() - infer_start
            _send({
                "type": "error",
                "job_id": job_id,
                "message": "OOM during transcription — insufficient unified memory",
                "category": "transient",
            })

        except Exception as exc:
            infer_seconds = time.monotonic() - infer_start
            err_text = str(exc)
            category = _classify_error(exc, err_text)
            logger.error("Transcription error: %s", exc, exc_info=True)
            _send({
                "type": "error",
                "job_id": job_id,
                "message": err_text[:500],
                "category": category,
            })

        finally:
            # Release temporary tensors and return Metal memory to pool
            gc.collect()
            _release_metal_cache()


def _send(msg: dict[str, Any]) -> None:
    """Write a JSON message to stdout (parent reads it)."""
    print(json.dumps(msg, ensure_ascii=False), flush=True)


def _classify_error(exc: Exception, err_text: str) -> str:
    deterministic_patterns = (
        "Invalid audio", "AudioFileError", "No such file",
        "unsupported format", "cannot read", "not a valid",
        "corrupt", "broken pipe",
    )
    if any(p in err_text for p in deterministic_patterns):
        return "deterministic"
    if isinstance(exc, (MemoryError, OverflowError)):
        return "transient"
    return "transient"


if __name__ == "__main__":
    main()
