"""
JobRunner — orchestrates the full job lifecycle.

Each call to run_job() executes one complete job:
  ASSIGNED → DOWNLOADING → PROCESSING → UPLOADING → COMPLETED

State transitions are reported to the coordinator at each phase change.
All files are written to a temporary directory that is cleaned up after
the job finishes (success or failure).

Resume safety:
  If the worker restarts mid-job and re-registers with current_job_id, the
  coordinator either:
  (a) Confirms the job still belongs to this worker → resume from where we
      left off. We re-start from the beginning of the phase we were in (no
      partial result checkpointing below the file level).
  (b) Tells us to cancel (job was reassigned) → cancel_current_job=True.

Pause/Resume:
  The Transcriber subprocess handles SIGSTOP/SIGCONT. The job_runner polls
  the command queue and delegates signals to the Transcriber context.

Error categorization:
  - Download failures: transient (retry)
  - Transcription OOM or SIGKILL: transient (retry)
  - Corrupt/unsupported audio: deterministic (no retry)
  - Upload ownership conflict: not retried (job was reassigned)
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
from .json_generator import generate_json
from .srt_generator import generate_srt
from .state import WorkerRunStatus, WorkerState
from .transcriber import JobCancelledError, Transcriber, TranscriberError
from .uploader import OwnershipLostError, upload_results

logger = logging.getLogger(__name__)


class JobRunner:
    """
    Executes a single job from claim through completion.
    Instantiate per job, or reuse across jobs (stateless methods).
    """

    def __init__(
        self,
        client: CoordinatorClient,
        config: WorkerConfig,
        state: WorkerState,
        worker_id: uuid.UUID,
        hostname: str,
    ) -> None:
        self._client = client
        self._config = config
        self._state = state
        self._worker_id = worker_id
        self._hostname = hostname

    async def run_job(self, job: JobAssignment) -> None:
        """
        Execute a full job lifecycle. Handles all phases and cleans up
        temp files regardless of outcome.

        Raises nothing — all errors are reported to the coordinator internally.
        """
        job_id = job.job_id
        temp_dir = self._config.temp_dir

        prepare_job_dir(temp_dir, job_id)
        input_path = mp3_path(temp_dir, job_id)
        out_srt = srt_path(temp_dir, job_id)
        out_json = json_path(temp_dir, job_id)

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
                "Job phase: processing (transcription)",
                extra={
                    "job_id": str(job_id),
                    "audio_duration": audio_duration,
                    "model": job.whisper_model,
                },
            )

            # BUG-FIX: config.__dict__.get("job_timeout_multiplier") always
            # returned the default (5) because Pydantic v2 stores fields in
            # __dict__ only after model creation, but the key "job_timeout_multiplier"
            # wasn't defined in WorkerConfig. Now it IS a proper field and is
            # accessed via the attribute directly.
            max_duration = job.max_job_duration_seconds or (
                int(audio_duration * self._config.job_timeout_multiplier)
                if audio_duration
                else None
            )

            async with Transcriber(
                audio_path=input_path,
                model_path=Path(job.whisper_model),
                language=job.whisper_language,
                word_timestamps=job.whisper_word_timestamps,
                audio_duration=audio_duration,
                max_duration_seconds=max_duration,
                # Pass term_timeout from config so Transcriber doesn't need
                # to reinstantiate WorkerConfig on every kill call.
                term_timeout=self._config.subprocess_term_timeout_seconds,
            ) as transcriber:
                self._state.transcription_pid = transcriber.pid

                async def on_progress(percent: float, elapsed: float) -> None:
                    self._state.job_progress_percent = percent
                    await self._client.report_progress(
                        job_id, self._worker_id, percent, elapsed
                    )

                result = await transcriber.run(
                    command_queue=self._state.command_queue,
                    on_progress=on_progress,
                )

            self._state.transcription_pid = None
            processing_elapsed = time.monotonic() - job_start_time

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
                whisper_model=job.whisper_model,
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
                # Job was reassigned while we were uploading.
                # Coordinator already handled it; we just clean up.
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
                    "rtf": round(total_elapsed / audio_duration, 3)
                           if audio_duration else None,
                },
            )

        except JobCancelledError:
            logger.info("Job cancelled", extra={"job_id": str(job_id)})
            # Coordinator already updated status; no fail_job needed

        except _TransientError as exc:
            logger.warning(
                "Job failed (transient)",
                extra={"job_id": str(job_id), "error": str(exc)},
            )
            await self._safe_fail(job_id, str(exc), "transient", retry=True)

        except TranscriberError as exc:
            logger.warning(
                "Job failed (transcription error)",
                extra={
                    "job_id": str(job_id),
                    "category": exc.error_category,
                    "error": str(exc),
                },
            )
            await self._safe_fail(
                job_id, str(exc), exc.error_category, retry=True
            )

        except httpx.HTTPStatusError as exc:
            logger.error(
                "Job failed (coordinator HTTP error)",
                extra={"job_id": str(job_id), "status": exc.response.status_code},
            )
            await self._safe_fail(job_id, str(exc), "transient", retry=True)

        except Exception as exc:
            logger.exception(
                "Job failed (unexpected error)",
                extra={"job_id": str(job_id)},
            )
            await self._safe_fail(job_id, f"{type(exc).__name__}: {exc}", "transient", retry=True)

        finally:
            self._state.transcription_pid = None
            self._state.set_idle()
            cleanup_job(temp_dir, job_id)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _safe_fail(
        self, job_id: uuid.UUID, message: str, category: str, retry: bool
    ) -> None:
        """Report job failure, ignoring any error in the fail call itself."""
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
    """Wrapper for transient errors during download or other non-transcription steps."""
