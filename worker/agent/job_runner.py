"""
JobRunner — orchestrates the full job lifecycle.

Job pipeline:
  ASSIGNED → DOWNLOADING → PROCESSING → UPLOADING → COMPLETED

The Transcriber has been replaced by TranscriptionPipeline from engine/.
The key difference: the model is loaded ONCE at worker startup and stays
resident in Apple Silicon unified memory. This eliminates the 15-30 second
model load cost on every job.

SIGSTOP / SIGCONT pause/resume is handled identically by the pipeline —
the persistent subprocess is frozen at the OS level, preserving Metal GPU state.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path

import httpx

from .cleanup import cleanup_job, mp3_path, prepare_job_dir, srt_path, json_path
from .config import WorkerConfig
from .coordinator_client import CoordinatorClient, JobAssignment
from .downloader import download_mp3, get_audio_duration
from .engine import (
    JobCancelledError,
    TranscriptionPipeline,
    TranscriptionPipelineError,
)
from .json_generator import generate_json
from .srt_generator import generate_srt
from .state import WorkerState
from .uploader import OwnershipLostError, upload_results

logger = logging.getLogger(__name__)


class JobRunner:
    """
    Executes a single job from claim through completion.
    Holds a reference to the shared TranscriptionPipeline (model loaded once).
    """

    def __init__(
        self,
        client: CoordinatorClient,
        config: WorkerConfig,
        state: WorkerState,
        worker_id: uuid.UUID,
        hostname: str,
        pipeline: TranscriptionPipeline,
    ) -> None:
        self._client = client
        self._config = config
        self._state = state
        self._worker_id = worker_id
        self._hostname = hostname
        self._pipeline = pipeline

    async def run_job(self, job: JobAssignment) -> None:
        """
        Execute a full job lifecycle. Reports all errors to the coordinator
        and cleans up temp files in all cases.
        """
        job_id = job.job_id
        temp_dir = self._config.temp_dir

        prepare_job_dir(temp_dir, job_id)
        input_path = mp3_path(temp_dir, job_id)
        out_srt = srt_path(temp_dir, job_id)
        out_json = json_path(temp_dir, job_id)
        # Pipeline writes its result JSON here; we read it after transcription.
        pipeline_output = temp_dir / str(job_id) / "_pipeline_output.json"

        self._state.set_busy(job_id, job.input_path)
        job_start_time = time.monotonic()

        try:
            # ── Phase 1: DOWNLOADING ───────────────────────────────────────────
            await self._client.advance_state(job_id, self._worker_id, "downloading")
            logger.info(
                "Job started: downloading",
                extra={"job_id": str(job_id), "path": job.input_path},
            )

            try:
                await download_mp3(
                    self._client,
                    job.download_url,
                    input_path,
                    chunk_size=self._config.download_chunk_size_bytes,
                )
            except Exception as exc:
                raise _TransientError(f"Download failed: {exc}") from exc

            audio_duration = await get_audio_duration(input_path)
            self._state.audio_duration_seconds = audio_duration

            # ── Phase 2: PROCESSING ────────────────────────────────────────────
            await self._client.advance_state(job_id, self._worker_id, "processing")
            logger.info(
                "Job phase: processing",
                extra={
                    "job_id": str(job_id),
                    "audio_duration": audio_duration,
                    "backend": self._pipeline.backend,
                },
            )

            async def on_progress(percent: float, elapsed: float) -> None:
                self._state.job_progress_percent = percent
                await self._client.report_progress(job_id, self._worker_id, percent, elapsed)

            result = await self._pipeline.transcribe(
                audio_path=input_path,
                output_file=pipeline_output,
                job_id=job_id,
                command_queue=self._state.command_queue,
                audio_duration=audio_duration,
                on_progress=on_progress,
            )

            processing_elapsed = time.monotonic() - job_start_time
            result.metrics.log_summary(logger, str(job_id))

            # ── Phase 3: Generate output files ────────────────────────────────
            logger.info(
                "Generating SRT and JSON output",
                extra={"job_id": str(job_id), "segments": result.segment_count},
            )
            generate_srt(result.segments, out_srt)
            generate_json(
                result_text=result.text,
                result_language=result.language,
                segments=result.segments,
                original_filename=job.original_filename,
                input_path=job.input_path,
                relative_folder=job.relative_folder,
                worker_id=self._worker_id,
                worker_hostname=self._hostname,
                whisper_model=str(self._pipeline._model_path),
                audio_duration_seconds=audio_duration or 0.0,
                processing_time_seconds=processing_elapsed,
                output_path=out_json,
            )

            # ── Phase 4: UPLOADING ─────────────────────────────────────────────
            await self._client.advance_state(job_id, self._worker_id, "uploading")
            logger.info("Job phase: uploading results", extra={"job_id": str(job_id)})

            try:
                await upload_results(
                    client=self._client,
                    job_id=job_id,
                    worker_id=self._worker_id,
                    srt_path=out_srt,
                    json_path=out_json,
                    audio_duration_seconds=audio_duration or 0.0,
                    processing_time_seconds=processing_elapsed,
                    segment_count=result.segment_count,
                    word_count=result.word_count,
                )
            except OwnershipLostError as exc:
                logger.warning(
                    "Job ownership lost during upload; discarding results",
                    extra={"job_id": str(job_id), "reason": str(exc)},
                )
                return  # cleanup in finally

            total_elapsed = time.monotonic() - job_start_time
            logger.info(
                "Job completed",
                extra={
                    "job_id": str(job_id),
                    "total_seconds": round(total_elapsed, 1),
                    "audio_seconds": round(audio_duration or 0, 1),
                    "rtf": result.metrics.rtf,
                    "speedup": result.metrics.speedup,
                    "backend": self._pipeline.backend,
                },
            )

        except JobCancelledError:
            logger.info("Job cancelled", extra={"job_id": str(job_id)})

        except _TransientError as exc:
            logger.warning(
                "Job failed (transient)",
                extra={"job_id": str(job_id), "error": str(exc)},
            )
            await self._safe_fail(job_id, str(exc), "transient", retry=True)

        except TranscriptionPipelineError as exc:
            logger.warning(
                "Job failed (pipeline error)",
                extra={
                    "job_id": str(job_id),
                    "category": exc.error_category,
                    "error": str(exc),
                },
            )
            await self._safe_fail(job_id, str(exc), exc.error_category, retry=True)

        except httpx.HTTPStatusError as exc:
            logger.error(
                "Job failed (coordinator HTTP error)",
                extra={"job_id": str(job_id), "status": exc.response.status_code},
            )
            await self._safe_fail(job_id, str(exc), "transient", retry=True)

        except Exception as exc:
            logger.exception("Job failed (unexpected)", extra={"job_id": str(job_id)})
            await self._safe_fail(job_id, f"{type(exc).__name__}: {exc}", "transient", retry=True)

        finally:
            self._state.set_idle()
            pipeline_output.unlink(missing_ok=True)
            cleanup_job(temp_dir, job_id)

    async def _safe_fail(
        self, job_id: uuid.UUID, message: str, category: str, retry: bool
    ) -> None:
        try:
            await self._client.fail_job(
                job_id=job_id,
                worker_id=self._worker_id,
                error_message=message,
                error_category=category,
                retry=retry,
            )
        except Exception as exc:
            logger.warning(
                "Could not report job failure to coordinator: %s", exc,
                extra={"job_id": str(job_id)},
            )


class _TransientError(RuntimeError):
    pass
