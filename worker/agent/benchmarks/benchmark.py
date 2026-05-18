"""
Benchmarking utilities for the transcription engine.

Usage:
    python -m agent.benchmarks.benchmark --audio /path/to/test.mp3 \\
        --model /opt/transcription-models/whisper-medium-mlx \\
        --runs 3 --output benchmark_results.json

Measures:
  - Model load time (amortized — happens once per worker lifetime with new design)
  - Warmup time
  - Per-file inference time (median, p95)
  - RTF (Real-Time Factor)
  - Memory before/after
  - Backend comparison (if multiple backends available)

All measurements exclude paused time to reflect actual compute cost.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import socket
import statistics
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psutil

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    run_index: int
    audio_path: str
    audio_duration_seconds: float
    backend: str
    model_path: str
    load_seconds: float        # 0 for runs 2+ (model already loaded)
    warmup_seconds: float      # 0 for runs 2+
    inference_seconds: float
    rtf: float
    segment_count: int
    word_count: int
    memory_before_mb: float
    memory_after_mb: float
    memory_delta_mb: float
    success: bool
    error: str = ""


@dataclass
class BenchmarkReport:
    timestamp: str
    hostname: str
    platform: str
    cpu_model: str
    cpu_cores: int
    memory_total_gb: float
    is_apple_silicon: bool
    model_path: str
    audio_files: list[str]
    runs_per_file: int
    results: list[RunResult] = field(default_factory=list)

    # ── Aggregates (computed on finalise()) ───────────────────────────────────
    median_rtf: float | None = None
    p95_rtf: float | None = None
    min_rtf: float | None = None
    max_rtf: float | None = None
    median_inference_seconds: float | None = None
    total_audio_hours: float | None = None
    total_inference_hours: float | None = None
    first_run_load_seconds: float | None = None
    warmup_seconds: float | None = None
    backend: str | None = None

    def finalise(self) -> None:
        successful = [r for r in self.results if r.success]
        if not successful:
            return

        rtfs = [r.rtf for r in successful]
        infer = [r.inference_seconds for r in successful]

        self.backend = successful[0].backend
        self.median_rtf = round(statistics.median(rtfs), 4)
        self.p95_rtf = round(sorted(rtfs)[int(len(rtfs) * 0.95)], 4)
        self.min_rtf = round(min(rtfs), 4)
        self.max_rtf = round(max(rtfs), 4)
        self.median_inference_seconds = round(statistics.median(infer), 2)
        self.total_audio_hours = round(
            sum(r.audio_duration_seconds for r in successful) / 3600, 3
        )
        self.total_inference_hours = round(
            sum(r.inference_seconds for r in successful) / 3600, 3
        )
        # Load/warmup from first run (only happens once per pipeline start)
        first = self.results[0] if self.results else None
        if first:
            self.first_run_load_seconds = first.load_seconds
            self.warmup_seconds = first.warmup_seconds

    def print_summary(self) -> None:
        self.finalise()
        print("\n" + "═" * 60)
        print(f"  Benchmark Report — {self.timestamp}")
        print("═" * 60)
        print(f"  Platform   : {self.platform}")
        print(f"  CPU        : {self.cpu_model}")
        print(f"  Memory     : {self.memory_total_gb:.1f} GB")
        print(f"  Backend    : {self.backend}")
        print(f"  Model      : {Path(self.model_path).name}")
        print()
        if self.first_run_load_seconds is not None:
            print(f"  Model load : {self.first_run_load_seconds:.1f}s (once per worker)")
        if self.warmup_seconds is not None:
            print(f"  Warmup     : {self.warmup_seconds:.1f}s (once per worker)")
        print()
        print(f"  Median RTF : {self.median_rtf:.4f}  ({1/self.median_rtf:.1f}x real-time)")
        print(f"  p95    RTF : {self.p95_rtf:.4f}")
        print(f"  Min/Max RTF: {self.min_rtf:.4f} / {self.max_rtf:.4f}")
        print(f"  Median infer: {self.median_inference_seconds:.1f}s")
        print()
        print(f"  Runs       : {len([r for r in self.results if r.success])}"
              f"/{len(self.results)} successful")
        if self.total_audio_hours:
            throughput = self.total_audio_hours / (self.total_inference_hours or 1)
            print(f"  Throughput : {throughput:.1f}x real-time aggregate")
        print("═" * 60 + "\n")


class BenchmarkRunner:
    """
    Run structured benchmarks against the transcription pipeline.
    """

    def __init__(
        self,
        model_path: Path,
        language: str = "tr",
        runs_per_file: int = 3,
        warm_up_pipeline: bool = True,
    ) -> None:
        self._model_path = model_path
        self._language = language
        self._runs = runs_per_file
        self._warm_up = warm_up_pipeline

    async def run(self, audio_files: list[Path]) -> BenchmarkReport:
        """
        Execute benchmarks across all audio files and return a full report.
        The pipeline is started once; all runs share the loaded model.
        """
        from ..engine.pipeline import TranscriptionPipeline, JobCancelledError

        report = BenchmarkReport(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            hostname=socket.gethostname(),
            platform=f"macOS {platform.mac_ver()[0]} {platform.machine()}",
            cpu_model=self._get_cpu_model(),
            cpu_cores=psutil.cpu_count(logical=False) or psutil.cpu_count() or 0,
            memory_total_gb=round(psutil.virtual_memory().total / (1024 ** 3), 1),
            is_apple_silicon=platform.machine() == "arm64",
            model_path=str(self._model_path),
            audio_files=[str(f) for f in audio_files],
            runs_per_file=self._runs,
        )

        pipeline = TranscriptionPipeline(
            model_path=self._model_path,
            language=self._language,
        )

        try:
            logger.info("Starting pipeline for benchmarks...")
            await pipeline.start()

            # Retrieve load/warmup times from first start
            load_seconds = pipeline._load_seconds
            warmup_seconds = pipeline._warmup_seconds

            run_index = 0
            for audio_path in audio_files:
                audio_duration = await self._get_duration(audio_path)
                if audio_duration is None:
                    logger.warning("Could not determine duration: %s; skipping", audio_path)
                    continue

                for run_i in range(self._runs):
                    run_index += 1
                    output_file = audio_path.parent / f"_bench_output_{run_index}.json"

                    mem_before = psutil.Process(os.getpid()).memory_info().rss / (1024 ** 2)

                    infer_start = time.monotonic()
                    error_msg = ""
                    success = False
                    seg_count = word_count = 0

                    try:
                        result = await pipeline.transcribe(
                            audio_path=audio_path,
                            output_file=output_file,
                            job_id=uuid.uuid4(),
                            command_queue=asyncio.Queue(),
                            audio_duration=audio_duration,
                        )
                        infer_seconds = time.monotonic() - infer_start
                        seg_count = result.segment_count
                        word_count = result.word_count
                        success = True
                    except Exception as exc:
                        infer_seconds = time.monotonic() - infer_start
                        error_msg = str(exc)
                        logger.error("Benchmark run %d failed: %s", run_index, exc)
                    finally:
                        output_file.unlink(missing_ok=True)

                    mem_after = psutil.Process(os.getpid()).memory_info().rss / (1024 ** 2)

                    rtf = (infer_seconds / audio_duration) if audio_duration > 0 else 0

                    report.results.append(RunResult(
                        run_index=run_index,
                        audio_path=str(audio_path),
                        audio_duration_seconds=audio_duration,
                        backend=pipeline.backend,
                        model_path=str(self._model_path),
                        # Load/warmup only counted for first run (once per worker)
                        load_seconds=load_seconds if run_index == 1 else 0.0,
                        warmup_seconds=warmup_seconds if run_index == 1 else 0.0,
                        inference_seconds=round(infer_seconds, 3),
                        rtf=round(rtf, 4),
                        segment_count=seg_count,
                        word_count=word_count,
                        memory_before_mb=round(mem_before, 1),
                        memory_after_mb=round(mem_after, 1),
                        memory_delta_mb=round(mem_after - mem_before, 1),
                        success=success,
                        error=error_msg,
                    ))

                    logger.info(
                        "Run %d/%d complete: RTF=%.3f, duration=%.1fs, infer=%.1fs",
                        run_index,
                        len(audio_files) * self._runs,
                        rtf,
                        audio_duration,
                        infer_seconds,
                    )

        finally:
            await pipeline.stop()

        report.finalise()
        return report

    async def _get_duration(self, audio_path: Path) -> float | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", str(audio_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            data = json.loads(stdout.decode())
            return float(data["format"]["duration"])
        except Exception:
            # Rough estimate from file size (128kbps MP3 ≈ 16 KB/s)
            try:
                return audio_path.stat().st_size / 16_000
            except Exception:
                return None

    @staticmethod
    def _get_cpu_model() -> str:
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip() or platform.processor()
        except Exception:
            return platform.processor()


# ── CLI entry point ───────────────────────────────────────────────────────────

async def _async_main(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    audio_files = [Path(p) for p in args.audio]
    for f in audio_files:
        if not f.exists():
            raise FileNotFoundError(f"Audio file not found: {f}")

    model_path = Path(args.model)
    if not model_path.exists():
        raise FileNotFoundError(f"Model not found: {model_path}")

    runner = BenchmarkRunner(
        model_path=model_path,
        language=args.language,
        runs_per_file=args.runs,
    )

    report = await runner.run(audio_files)
    report.print_summary()

    if args.output:
        out = Path(args.output)
        out.write_text(
            json.dumps(
                {
                    "timestamp": report.timestamp,
                    "hostname": report.hostname,
                    "platform": report.platform,
                    "cpu_model": report.cpu_model,
                    "memory_total_gb": report.memory_total_gb,
                    "backend": report.backend,
                    "model_path": report.model_path,
                    "summary": {
                        "median_rtf": report.median_rtf,
                        "p95_rtf": report.p95_rtf,
                        "min_rtf": report.min_rtf,
                        "max_rtf": report.max_rtf,
                        "median_inference_seconds": report.median_inference_seconds,
                        "model_load_seconds": report.first_run_load_seconds,
                        "warmup_seconds": report.warmup_seconds,
                    },
                    "runs": [asdict(r) for r in report.results],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"Results written to: {out}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark the transcription engine on Apple Silicon"
    )
    parser.add_argument(
        "--audio", nargs="+", required=True,
        help="Audio file(s) to use for benchmarking (MP3 format)",
    )
    parser.add_argument(
        "--model",
        default="/opt/transcription-models/whisper-medium-mlx",
        help="Path to the Whisper model directory",
    )
    parser.add_argument(
        "--language", default="tr",
        help="Language code (default: tr for Turkish)",
    )
    parser.add_argument(
        "--runs", type=int, default=3,
        help="Number of inference runs per audio file (default: 3)",
    )
    parser.add_argument(
        "--output",
        help="Save JSON report to this path",
    )
    args = parser.parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
