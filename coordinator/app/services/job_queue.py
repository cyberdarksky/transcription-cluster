from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from ..config import settings
from ..core.time_utils import utc_naive
from ..models.enums import ErrorCategory, JobStatus, WorkerStatus
from ..models.job import Job
from ..models.worker import Worker
from ..schemas.job import WhisperSettings

logger = logging.getLogger(__name__)
UTC = timezone.utc


# ── Shared singleton (no mutable state — safe to share across modules) ─────────
class JobQueueService:
    """
    All state transitions and queue operations for jobs.

    Design rules:
    - Every public method is an explicit transaction unit.
    - DB mutations are always followed by db.flush() before returning.
    - Blocking I/O (file writes) is always dispatched to an executor.
    - Status strings always use WorkerStatus/JobStatus enums.
    """

    # ── Job claiming ──────────────────────────────────────────────────────────

    async def claim_next_job(
        self, db: AsyncSession, worker_id: uuid.UUID
    ) -> Job | None:
        """
        Atomically claim the highest-priority pending job.
        FOR UPDATE SKIP LOCKED prevents concurrent workers from blocking each other.

        NOTE: idx_jobs_queue partial index (WHERE status='pending') is used.
        next_retry_after is filtered at runtime — volatile NOW() cannot appear
        in a partial index predicate (PostgreSQL requires IMMUTABLE predicates).
        """
        stmt = (
            select(Job)
            .where(
                Job.status == JobStatus.QUEUED,
                or_(
                    Job.next_retry_after.is_(None),
                    Job.next_retry_after <= func.now(),
                ),
            )
            .order_by(Job.priority.desc(), Job.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        result = await db.execute(stmt)
        job = result.scalar_one_or_none()
        if job is None:
            return None

        now = utc_naive()
        job.status = JobStatus.ASSIGNED
        job.worker_id = worker_id
        job.assigned_at = now
        job.updated_at = now
        await db.flush()

        logger.info("Job claimed", extra={"job_id": str(job.id), "worker_id": str(worker_id)})
        return job

    # ── Status transitions ────────────────────────────────────────────────────

    async def mark_started(
        self, db: AsyncSession, job_id: uuid.UUID, worker_id: uuid.UUID
    ) -> Job | None:
        """ASSIGNED → PROCESSING. Returns None on wrong state."""
        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.worker_id == worker_id,
                Job.status == JobStatus.ASSIGNED,
            )
            .values(status=JobStatus.PROCESSING, started_at=func.now(), updated_at=func.now())
            .returning(Job)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def update_progress(
        self, db: AsyncSession, job_id: uuid.UUID, worker_id: uuid.UUID, percent: Decimal
    ) -> bool:
        """
        Update progress_percent only.
        Does NOT insert a job_event row — progress events are transient and
        not persisted (avoids ~720 rows per 2-hour audio file).
        """
        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.worker_id == worker_id,
                Job.status == JobStatus.PROCESSING,
            )
            .values(progress_percent=percent, updated_at=func.now())
        )
        return (await db.execute(stmt)).rowcount > 0

    async def pause_job(self, db: AsyncSession, job_id: uuid.UUID) -> Job | None:
        """PROCESSING → PAUSED. Returns None on wrong state (caller raises 409)."""
        stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.PROCESSING)
            .values(status=JobStatus.PAUSED, paused_at=func.now(), updated_at=func.now())
            .returning(Job)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def resume_job(self, db: AsyncSession, job_id: uuid.UUID) -> Job | None:
        """PAUSED → PROCESSING. Returns None on wrong state (caller raises 409)."""
        stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.PAUSED)
            .values(
                status=JobStatus.PROCESSING,
                paused_at=None,
                updated_at=func.now(),
            )
            .returning(Job)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def cancel_job(self, db: AsyncSession, job_id: uuid.UUID) -> Job | None:
        """Cancel any non-terminal job. Also clears the owning worker's current_job_id."""
        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.status.in_([
                    JobStatus.QUEUED, JobStatus.ASSIGNED,
                    JobStatus.PROCESSING, JobStatus.PAUSED,
                ]),
            )
            .values(
                status=JobStatus.CANCELLED,
                worker_id=None,
                progress_percent=None,
                updated_at=func.now(),
            )
            .returning(Job)
        )
        result = await db.execute(stmt)
        job = result.scalar_one_or_none()
        if job is None:
            return None

        # Clear the worker's current_job_id to maintain consistency
        if job.worker_id is not None:
            await db.execute(
                update(Worker)
                .where(Worker.id == job.worker_id, Worker.current_job_id == job_id)
                .values(current_job_id=None, updated_at=func.now())
            )
        return job

    async def retry_job(self, db: AsyncSession, job_id: uuid.UUID) -> Job | None:
        """Reset FAILED or CANCELLED back to QUEUED with a clean slate."""
        stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status.in_([JobStatus.FAILED, JobStatus.CANCELLED]))
            .values(
                status=JobStatus.QUEUED,
                retry_count=0,
                error_category=None,
                last_error=None,
                next_retry_after=None,
                worker_id=None,
                assigned_at=None,
                started_at=None,
                completed_at=None,
                progress_percent=None,
                updated_at=func.now(),
            )
            .returning(Job)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    # ── Complete ──────────────────────────────────────────────────────────────

    async def complete_job(
        self,
        db: AsyncSession,
        job_id: uuid.UUID,
        worker_id: uuid.UUID,
        srt_content: bytes,
        json_content: bytes,
        metadata: dict[str, Any],
    ) -> Job:
        """
        Atomically verify ownership, write output files, and mark complete.

        Ownership is checked under SELECT FOR UPDATE so no other request can
        steal the job between the check and the update.

        File writes use tmp → rename for atomicity (crash-safe on same filesystem).
        File I/O runs in an executor to avoid blocking the event loop.

        Raises:
            PermissionError: job no longer belongs to this worker.
            ValueError: job not found, or in an unexpected status.
        """
        # SELECT FOR UPDATE: prevents concurrent ownership theft
        result = await db.execute(
            select(Job).where(Job.id == job_id).with_for_update()
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        if job.worker_id != worker_id:
            raise PermissionError(
                f"Job {job_id} is not owned by worker {worker_id}. "
                f"Current owner: {job.worker_id}"
            )
        if job.status not in (JobStatus.PROCESSING, JobStatus.PAUSED):
            raise ValueError(f"Job {job_id} has unexpected status: {job.status}")

        # Dispatch blocking file I/O to thread pool
        loop = asyncio.get_running_loop()
        srt_rel, srt_hash = await loop.run_in_executor(
            None, self._write_output_file,
            srt_content, job.relative_folder, job.original_filename, ".srt"
        )
        json_rel, json_hash = await loop.run_in_executor(
            None, self._write_output_file,
            json_content, job.relative_folder, job.original_filename, ".json"
        )

        audio_dur = Decimal(str(metadata.get("audio_duration_seconds", 0)))
        proc_time = Decimal(str(metadata.get("processing_time_seconds", 0)))
        rtf = (proc_time / audio_dur).quantize(Decimal("0.0001")) if audio_dur > 0 else None

        now = utc_naive()
        job.status = JobStatus.COMPLETED
        job.completed_at = now
        job.updated_at = now
        job.progress_percent = Decimal("100")
        job.output_srt_path = srt_rel
        job.output_json_path = json_rel
        job.output_srt_hash = srt_hash
        job.output_json_hash = json_hash
        job.audio_duration_seconds = audio_dur
        job.processing_time_seconds = proc_time
        job.rtf = rtf

        await self._release_worker(db, worker_id, increment_completed=True,
                                   audio_seconds=audio_dur, processing_seconds=proc_time)
        await db.flush()
        logger.info(
            "Job completed",
            extra={
                "job_id": str(job_id), "worker_id": str(worker_id),
                "rtf": float(rtf) if rtf else None,
                "audio_duration_seconds": float(audio_dur),
            },
        )
        return job

    # ── Fail ──────────────────────────────────────────────────────────────────

    async def fail_job(
        self,
        db: AsyncSession,
        job_id: uuid.UUID,
        worker_id: uuid.UUID,
        error_message: str,
        error_category: ErrorCategory,
        retry: bool,
    ) -> Job:
        result = await db.execute(
            select(Job).where(Job.id == job_id).with_for_update()
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise ValueError(f"Job {job_id} not found")

        now = utc_naive()
        job.last_error = error_message
        job.error_category = error_category
        job.updated_at = now

        will_retry = (
            retry
            and error_category != ErrorCategory.DETERMINISTIC
            and job.retry_count < job.max_retries
        )

        if will_retry:
            delay = self._retry_delay(job.retry_count)
            job.status = JobStatus.RETRY_WAIT
            job.worker_id = None
            job.assigned_at = None
            job.started_at = None
            job.progress_percent = None
            job.retry_count += 1
            # next_retry_after=None means immediately claimable; non-zero delay sets a future time
            job.next_retry_after = (
                None
                if delay == 0
                else datetime.fromtimestamp(now.timestamp() + delay, tz=UTC)
            )
        else:
            job.status = JobStatus.FAILED
            job.worker_id = None

        await self._release_worker(db, worker_id, increment_failed=True)
        await db.flush()
        logger.warning(
            "Job failed",
            extra={
                "job_id": str(job_id), "category": error_category,
                "will_retry": will_retry, "retry_count": job.retry_count,
            },
        )
        return job

    # ── Job creation ──────────────────────────────────────────────────────────

    async def create_job(
        self,
        db: AsyncSession,
        input_path: str,
        original_filename: str,
        relative_folder: str,
        file_size_bytes: int | None,
        file_hash: str | None,
        priority: int = 0,
    ) -> Job | None:
        """
        Create a new job. Returns None if a non-terminal job already exists for
        this input_path (duplicate detection: path-first, then hash).

        Uses INSERT + IntegrityError catch to handle the rare TOCTOU race where
        two concurrent calls both pass the SELECT check.
        """
        # Fast path: check by path first
        existing = await db.scalar(
            select(Job.id).where(
                Job.input_path == input_path,
                Job.status.not_in([JobStatus.FAILED, JobStatus.CANCELLED]),
            )
        )
        if existing is not None:
            return None

        # Hash-based warning for same content at different paths
        if file_hash is not None:
            existing_hash = await db.scalar(
                select(Job.id).where(
                    Job.file_hash == file_hash,
                    Job.status.not_in([JobStatus.FAILED, JobStatus.CANCELLED]),
                )
            )
            if existing_hash is not None:
                logger.warning(
                    "Duplicate content hash at different path",
                    extra={"hash": file_hash, "new_path": input_path},
                )

        job = Job(
            input_path=input_path,
            original_filename=original_filename,
            relative_folder=relative_folder,
            file_size_bytes=file_size_bytes,
            file_hash=file_hash,
            priority=priority,
        )
        db.add(job)
        try:
            await db.flush()
        except IntegrityError:
            # TOCTOU: another request created the same job between our SELECT and INSERT
            await db.rollback()
            logger.debug("Concurrent job creation detected, skipping", extra={"path": input_path})
            return None

        logger.info("Job created", extra={"job_id": str(job.id), "path": input_path})
        return job

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _write_output_file(
        content: bytes,
        relative_folder: str,
        original_filename: str,
        extension: str,
    ) -> tuple[str, str]:
        """
        Synchronous: Write content atomically (tmp → rename) and return
        (relative_output_path, md5_hex).
        MUST be called via run_in_executor — never directly from async code.
        """
        basename = Path(original_filename).stem + extension
        folder = settings.output_base_dir / relative_folder
        folder.mkdir(parents=True, exist_ok=True)

        final_path = folder / basename
        tmp_path = final_path.with_suffix(extension + ".tmp")
        tmp_path.write_bytes(content)
        tmp_path.rename(final_path)  # Atomic on same filesystem

        md5 = hashlib.md5(content).hexdigest()
        relative = str(Path(relative_folder) / basename) if relative_folder else basename
        return relative, md5

    async def _release_worker(
        self,
        db: AsyncSession,
        worker_id: uuid.UUID,
        increment_completed: bool = False,
        increment_failed: bool = False,
        audio_seconds: Decimal = Decimal("0"),
        processing_seconds: Decimal = Decimal("0"),
    ) -> None:
        """
        Clear the worker's current job and update lifetime statistics.
        Extracted to avoid code duplication between complete_job and fail_job.
        """
        updates: dict[str, Any] = {
            "current_job_id": None,
            "status": WorkerStatus.IDLE,
            "updated_at": func.now(),
        }
        if increment_completed:
            updates["jobs_completed"] = Worker.jobs_completed + 1
            updates["total_audio_seconds"] = Worker.total_audio_seconds + audio_seconds
            updates["total_processing_seconds"] = Worker.total_processing_seconds + processing_seconds
        if increment_failed:
            updates["jobs_failed"] = Worker.jobs_failed + 1

        await db.execute(
            update(Worker).where(Worker.id == worker_id).values(**updates)
        )

    @staticmethod
    def _retry_delay(retry_count: int) -> int:
        delays = settings.retry_delays_seconds
        idx = min(retry_count, len(delays) - 1)
        return delays[idx]

    @staticmethod
    def build_whisper_settings() -> WhisperSettings:
        return WhisperSettings(
            model=settings.whisper_model_path,
            language="tr",
            word_timestamps=True,
        )


# Module-level singleton — no mutable state, safe to share
job_queue_service = JobQueueService()
