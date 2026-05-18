"""
DistributedQueue — production-grade PostgreSQL-backed job queue.

All public methods follow one invariant:
    The DB mutation (status transition, worker assignment, lease operation)
    is encoded as a single UPDATE with a WHERE clause that atomically validates
    the pre-condition. There is no SELECT-then-UPDATE anti-pattern.

Race-condition guarantees:
    - claim_job: FOR UPDATE SKIP LOCKED — concurrent workers never block each other
      or receive the same job.
    - advance_state: WHERE status=<expected_prev> — transition rejected at DB level
      if another process already changed the state.
    - complete_job: non-locking read for metadata → async file IO → atomic
      UPDATE WHERE worker_id+status. No row lock held during IO. If ownership
      is lost between read and UPDATE, files are cleaned up and PermissionError raised.
    - fail_job: SELECT FOR UPDATE is appropriate here because the mutation is
      a pure in-memory computation (no IO), keeping the lock window tiny.
    - renew_lease: WHERE lease_expires_at >= NOW() — stale worker cannot renew
      an already-expired (and potentially recovered) lease.
    - cancel_job: SELECT FOR UPDATE before UPDATE so old worker_id is captured
      correctly and worker.current_job_id is cleared.
    - create_job: SAVEPOINT (begin_nested) instead of full rollback on
      IntegrityError — other request-level DB operations are preserved.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

from ..core.time_utils import utc_naive
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from ..config import settings
from ..models.enums import ErrorCategory, JobStatus, WorkerStatus
from ..models.job import Job
from ..models.worker import Worker
from ..schemas.job import WhisperSettings
from .lease_manager import lease_manager
from .states import (
    ACTIVE_STATUSES,
    LEASEABLE_STATUSES,
    REQUIRED_PREVIOUS_STATE,
    InvalidTransitionError,
)

logger = logging.getLogger(__name__)
UTC = timezone.utc


class DistributedQueue:
    """
    Full lifecycle management for the distributed transcription queue.
    Instantiate once as a module-level singleton — all methods are stateless.
    """

    # ── Job claiming ──────────────────────────────────────────────────────────

    async def claim_job(
        self,
        db: AsyncSession,
        worker_id: uuid.UUID,
        lease_duration_seconds: int | None = None,
    ) -> Job | None:
        """
        Atomically claim the next available job with a worker lease.

        FOR UPDATE SKIP LOCKED prevents duplicate assignment under concurrent load.
        Lease is set immediately: if the worker crashes before renewing,
        LeaseRecoveryService will re-queue the job automatically.
        """
        duration = lease_duration_seconds or settings.job_lease_duration_seconds

        # Prevent one worker from holding two leases (poll race or reconnect bug).
        busy_job = await db.scalar(
            select(Worker.current_job_id).where(Worker.id == worker_id)
        )
        if busy_job is not None:
            logger.warning(
                "Worker already has an active job; claim skipped",
                extra={"worker_id": str(worker_id), "current_job_id": str(busy_job)},
            )
            return None

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
        job.assigned_at = now
        job.updated_at = now
        lease_manager.grant(job, worker_id, duration)

        await db.execute(
            update(Worker)
            .where(Worker.id == worker_id)
            .values(
                current_job_id=job.id,
                status=WorkerStatus.BUSY,
                updated_at=func.now(),
            )
        )

        await db.flush()
        logger.info(
            "Job claimed",
            extra={
                "job_id": str(job.id),
                "worker_id": str(worker_id),
                "lease_expires_at": job.lease_expires_at.isoformat() if job.lease_expires_at else None,
            },
        )
        return job

    # ── State progression ─────────────────────────────────────────────────────

    async def advance_state(
        self,
        db: AsyncSession,
        job_id: uuid.UUID,
        worker_id: uuid.UUID,
        new_state: JobStatus,
    ) -> Job:
        """
        Advance a job along the pipeline (ASSIGNED→DOWNLOADING→PROCESSING→UPLOADING).

        Validates the required previous state in the WHERE clause — no
        SELECT-first anti-pattern.

        Raises:
            KeyError: new_state is not a valid advance target.
            InvalidTransitionError: job is in the wrong state (concurrent race).
            PermissionError: job not owned by this worker.
            ValueError: job not found.
        """
        required_prev = REQUIRED_PREVIOUS_STATE.get(new_state)
        if required_prev is None:
            raise KeyError(
                f"{new_state!r} is not a valid advance target. "
                "Use complete_job() or fail_job() for terminal transitions."
            )

        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.worker_id == worker_id,
                Job.status == required_prev,
            )
            .values(status=new_state, updated_at=func.now())
            .returning(Job)
        )
        updated = (await db.execute(stmt)).scalar_one_or_none()
        if updated is not None:
            return updated

        # Nothing matched — diagnose why, but acknowledge this read can race
        existing = await db.get(Job, job_id)
        if existing is None:
            raise ValueError(f"Job {job_id} not found")
        if existing.worker_id != worker_id:
            raise PermissionError(f"Job {job_id} not owned by worker {worker_id}")
        raise InvalidTransitionError(existing.status, new_state)

    # ── Lease renewal ─────────────────────────────────────────────────────────

    async def update_progress(
        self,
        db: AsyncSession,
        job_id: uuid.UUID,
        worker_id: uuid.UUID,
        percent: Decimal,
    ) -> bool:
        """Update progress_percent for an in-flight job owned by this worker."""
        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.worker_id == worker_id,
                Job.status.in_([
                    JobStatus.DOWNLOADING,
                    JobStatus.PROCESSING,
                    JobStatus.UPLOADING,
                    JobStatus.PAUSED,
                ]),
            )
            .values(progress_percent=percent, updated_at=func.now())
        )
        return (await db.execute(stmt)).rowcount > 0

    async def renew_lease(
        self,
        db: AsyncSession,
        job_id: uuid.UUID,
        worker_id: uuid.UUID,
        duration_seconds: int | None = None,
    ) -> bool:
        """
        Extend the lease on the worker's current job.
        Called implicitly on every heartbeat.
        Returns False if the lease already expired or the job was reassigned.
        """
        duration = duration_seconds or settings.job_lease_duration_seconds
        renewed = await lease_manager.renew(db, job_id, worker_id, duration)
        if not renewed:
            logger.warning(
                "Lease renewal failed — job may have been recovered or reassigned",
                extra={"job_id": str(job_id), "worker_id": str(worker_id)},
            )
        return renewed

    # ── Completion ────────────────────────────────────────────────────────────

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
        Write output files and mark the job COMPLETED.

        BUG-FIX: Original design held a SELECT FOR UPDATE lock during async file IO
        (potentially seconds), blocking every other transaction touching this row
        (cancel, heartbeat, recovery).

        New design: 3-phase approach with NO lock held during IO:
          Phase 1 — Non-locking validation read (gets filename/folder for file paths).
          Phase 2 — Async file IO with no DB lock held.
          Phase 3 — Atomic UPDATE WHERE (worker_id + status) validates ownership.
                     If 0 rows updated → ownership was lost → clean up orphaned files.

        This eliminates the lock-during-IO problem entirely. Concurrent requests
        racing to complete the same job: the one whose UPDATE wins proceeds;
        the other gets a clean PermissionError and cleans up its files.

        Raises:
            PermissionError: job not owned by this worker (or ownership lost mid-IO).
            ValueError: job not found or in unexpected state.
        """
        # ── Phase 1: Non-locking read (no lock held) ─────────────────────────
        row = await db.scalar(select(Job).where(Job.id == job_id))
        if row is None:
            raise ValueError(f"Job {job_id} not found")
        if row.worker_id != worker_id:
            raise PermissionError(
                f"Job {job_id} ownership conflict: "
                f"owner={row.worker_id}, requester={worker_id}"
            )
        if row.status not in (JobStatus.UPLOADING, JobStatus.PROCESSING):
            raise ValueError(
                f"Job {job_id} in state {row.status!r}; expected UPLOADING or PROCESSING"
            )

        original_filename = row.original_filename
        relative_folder = row.relative_folder

        # ── Phase 2: File IO (NO DB lock held) ────────────────────────────────
        loop = asyncio.get_running_loop()
        srt_rel, srt_hash = await loop.run_in_executor(
            None, self._write_output_file,
            srt_content, relative_folder, original_filename, ".srt",
        )
        json_rel, json_hash = await loop.run_in_executor(
            None, self._write_output_file,
            json_content, relative_folder, original_filename, ".json",
        )

        audio_dur = Decimal(str(metadata.get("audio_duration_seconds", 0)))
        proc_time = Decimal(str(metadata.get("processing_time_seconds", 0)))
        rtf = (proc_time / audio_dur).quantize(Decimal("0.0001")) if audio_dur > 0 else None
        now = utc_naive()

        # ── Phase 3: Atomic ownership-validated UPDATE (no long lock) ─────────
        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.worker_id == worker_id,
                Job.status.in_([JobStatus.UPLOADING, JobStatus.PROCESSING]),
            )
            .values(
                status=JobStatus.COMPLETED,
                completed_at=now,
                updated_at=now,
                progress_percent=Decimal("100"),
                output_srt_path=srt_rel,
                output_json_path=json_rel,
                output_srt_hash=srt_hash,
                output_json_hash=json_hash,
                audio_duration_seconds=audio_dur,
                processing_time_seconds=proc_time,
                rtf=rtf,
                lease_expires_at=None,
            )
            .returning(Job)
        )
        updated = (await db.execute(stmt)).scalar_one_or_none()

        if updated is None:
            # Ownership was lost between Phase 1 and Phase 3 (lease expired,
            # recovery ran, another worker was assigned). Clean up orphaned files.
            await loop.run_in_executor(
                None, self._cleanup_orphaned_files, srt_rel, json_rel
            )
            raise PermissionError(
                f"Job {job_id} ownership lost during file upload. "
                "Orphaned output files cleaned up."
            )

        await self._release_worker(
            db, worker_id,
            increment_completed=True,
            audio_seconds=audio_dur,
            processing_seconds=proc_time,
        )
        await db.flush()
        logger.info(
            "Job completed",
            extra={
                "job_id": str(job_id),
                "worker_id": str(worker_id),
                "rtf": float(rtf) if rtf else None,
                "audio_duration_seconds": float(audio_dur),
            },
        )
        return updated

    # ── Failure ───────────────────────────────────────────────────────────────

    async def fail_job(
        self,
        db: AsyncSession,
        job_id: uuid.UUID,
        worker_id: uuid.UUID,
        error_message: str,
        error_category: ErrorCategory,
        retry: bool = True,
    ) -> Job:
        """
        Mark a job failed or schedule it for retry.

        SELECT FOR UPDATE is retained here because the mutation is a pure
        in-memory computation (no IO), keeping the lock window negligible.

        Retry logic:
            DETERMINISTIC errors  → FAILED immediately (no retry, ever).
            TRANSIENT errors, retry=True, retry_count < max_retries → RETRY_WAIT.
            TRANSIENT errors, retry_count >= max_retries → FAILED.
            retry=False → FAILED regardless of category.

        BUG-FIX: Log attempt number BEFORE incrementing retry_count so that
        the log message matches the attempt that actually failed (not the next).
        """
        result = await db.execute(select(Job).where(Job.id == job_id).with_for_update())
        job = result.scalar_one_or_none()
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        if job.worker_id != worker_id:
            raise PermissionError(
                f"Job {job_id} not owned by worker {worker_id} "
                f"(owner={job.worker_id})"
            )

        # Capture before mutation for accurate logging
        attempt_number = job.retry_count + 1

        now = utc_naive()
        job.last_error = error_message
        job.error_category = error_category
        job.updated_at = now
        job.lease_expires_at = None

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
            # Compute retry time in Python — do NOT assign a SQL func expression
            # to an ORM attribute; it serialises as a Python expression object.
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
                "job_id": str(job_id),
                "worker_id": str(worker_id),
                "attempt": attempt_number,
                "retry_count_new": job.retry_count,
                "max_retries": job.max_retries,
                "category": str(error_category),
                "will_retry": will_retry,
            },
        )
        return job

    # ── Pause / Resume ────────────────────────────────────────────────────────

    async def pause_job(
        self, db: AsyncSession, job_id: uuid.UUID
    ) -> Job | None:
        """
        PROCESSING → PAUSED.

        BUG-FIX: Also clears lease_expires_at. During PAUSED, heartbeats cannot
        renew the lease (PAUSED is not in LEASEABLE_STATUSES). Leaving a ticking
        lease would trigger recovery the moment it expires, undoing the pause.
        """
        stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.PROCESSING)
            .values(
                status=JobStatus.PAUSED,
                paused_at=func.now(),
                lease_expires_at=None,  # Prevent recovery from triggering on stale lease
                updated_at=func.now(),
            )
            .returning(Job)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def resume_job(
        self, db: AsyncSession, job_id: uuid.UUID
    ) -> Job | None:
        """
        PAUSED → PROCESSING.

        BUG-FIX: Grants a fresh lease on resume. The original lease expired
        during the pause period. Without a new lease the recovery service would
        immediately re-queue the job after the first recovery sweep.
        """
        new_expires = utc_naive() + timedelta(seconds=settings.job_lease_duration_seconds)
        stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.PAUSED)
            .values(
                status=JobStatus.PROCESSING,
                paused_at=None,
                lease_expires_at=new_expires,
                lease_renewed_count=0,
                updated_at=func.now(),
            )
            .returning(Job)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def cancel_job(
        self, db: AsyncSession, job_id: uuid.UUID
    ) -> Job | None:
        """
        Cancel any non-terminal job and clear the owning worker's current_job_id.

        BUG-FIX: The previous implementation read job.worker_id from RETURNING
        after the UPDATE had already set it to NULL. The worker_id was always None,
        so worker.current_job_id was never cleared.

        Fix: SELECT FOR UPDATE to capture the current worker_id *before* the UPDATE,
        then use it to clear the worker record.
        """
        # Capture old worker_id under a lock before cancelling
        row = await db.execute(
            select(Job.id, Job.worker_id, Job.status)
            .where(Job.id == job_id, Job.status.in_(ACTIVE_STATUSES))
            .with_for_update()
        )
        existing = row.one_or_none()
        if existing is None:
            return None

        old_worker_id = existing.worker_id

        # Now perform the cancel
        await db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.CANCELLED,
                worker_id=None,
                lease_expires_at=None,
                progress_percent=None,
                updated_at=func.now(),
            )
        )

        # Clear worker record using the captured old_worker_id
        if old_worker_id is not None:
            await db.execute(
                update(Worker)
                .where(Worker.id == old_worker_id, Worker.current_job_id == job_id)
                .values(current_job_id=None, updated_at=func.now())
            )

        await db.flush()
        # Return the updated job for callers that need it
        return await db.get(Job, job_id)

    async def retry_job(
        self, db: AsyncSession, job_id: uuid.UUID
    ) -> Job | None:
        """Manual retry: reset FAILED or CANCELLED back to QUEUED."""
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
                lease_expires_at=None,
                assigned_at=None,
                started_at=None,
                completed_at=None,
                progress_percent=None,
                updated_at=func.now(),
            )
            .returning(Job)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

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
        Create a new QUEUED job.

        BUG-FIX: Previous implementation called `await db.rollback()` on
        IntegrityError, which rolled back the ENTIRE session transaction,
        silently discarding any other DB work in the same request.

        Fix: Use a SAVEPOINT (db.begin_nested()) so only the failed INSERT
        is rolled back while the enclosing transaction continues unaffected.
        """
        existing = await db.scalar(
            select(Job.id).where(
                Job.input_path == input_path,
                Job.status.not_in([JobStatus.FAILED, JobStatus.CANCELLED]),
            )
        )
        if existing is not None:
            return None

        if file_hash is not None:
            dup_hash = await db.scalar(
                select(Job.id).where(
                    Job.file_hash == file_hash,
                    Job.status.not_in([JobStatus.FAILED, JobStatus.CANCELLED]),
                )
            )
            if dup_hash is not None:
                logger.warning(
                    "Duplicate content hash at different path",
                    extra={"hash": file_hash, "path": input_path},
                )

        job = Job(
            input_path=input_path,
            original_filename=original_filename,
            relative_folder=relative_folder,
            file_size_bytes=file_size_bytes,
            file_hash=file_hash,
            priority=priority,
            status=JobStatus.QUEUED,
        )
        db.add(job)
        try:
            # SAVEPOINT: only the INSERT is rolled back on IntegrityError;
            # the enclosing session transaction is preserved.
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            logger.debug(
                "Concurrent job creation for same path, skipping",
                extra={"path": input_path},
            )
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
        Synchronous. Writes atomically (tmp → rename).
        MUST be called via run_in_executor — never directly from async code.
        Returns (relative_output_path, md5_hex).
        """
        basename = Path(original_filename).stem + extension
        folder = settings.output_base_dir / relative_folder
        folder.mkdir(parents=True, exist_ok=True)

        final_path = folder / basename
        tmp_path = final_path.with_suffix(extension + ".tmp")
        tmp_path.write_bytes(content)
        tmp_path.rename(final_path)

        md5 = hashlib.md5(content).hexdigest()
        relative = str(Path(relative_folder) / basename) if relative_folder else basename
        return relative, md5

    @staticmethod
    def _cleanup_orphaned_files(srt_rel: str, json_rel: str) -> None:
        """
        Remove output files when ownership was lost between Phase 1 and Phase 3
        of complete_job. Called via run_in_executor.
        """
        for rel_path in (srt_rel, json_rel):
            if not rel_path:
                continue
            full_path = settings.output_base_dir / rel_path
            try:
                full_path.unlink(missing_ok=True)
                logger.warning(
                    "Cleaned up orphaned output file after ownership conflict",
                    extra={"path": str(full_path)},
                )
            except OSError as exc:
                logger.error(
                    "Failed to clean up orphaned file",
                    extra={"path": str(full_path), "error": str(exc)},
                )

    async def _release_worker(
        self,
        db: AsyncSession,
        worker_id: uuid.UUID,
        increment_completed: bool = False,
        increment_failed: bool = False,
        audio_seconds: Decimal = Decimal("0"),
        processing_seconds: Decimal = Decimal("0"),
    ) -> None:
        """Clear worker's current_job and update lifetime stats in one UPDATE."""
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

        await db.execute(update(Worker).where(Worker.id == worker_id).values(**updates))

    @staticmethod
    def _retry_delay(retry_count: int) -> int:
        delays = settings.retry_delays_seconds
        return delays[min(retry_count, len(delays) - 1)]

    @staticmethod
    def build_whisper_settings() -> WhisperSettings:
        return WhisperSettings(
            model=settings.whisper_model_path,
            language="tr",
            word_timestamps=True,
        )


# Module-level singleton
distributed_queue = DistributedQueue()
