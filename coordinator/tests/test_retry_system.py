"""
Tests for the retry system.

Covers:
- Transient failure moves job to RETRY_WAIT with correct delay
- Deterministic failure goes straight to FAILED (no retry)
- retry=False forces FAILED regardless of category
- Retry count increments correctly
- Max retries triggers FAILED
- RetryScheduler promotes RETRY_WAIT → QUEUED when delay passes
- RetryScheduler skips jobs whose delay hasn't passed
- Manual retry (dashboard) resets retry_count and moves to QUEUED
- Retry delay sequence (0s, 60s, 300s) is applied correctly
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ErrorCategory, JobStatus
from app.models.job import Job
from app.queue.distributed_queue import DistributedQueue
from app.queue.retry_scheduler import RetryScheduler

from .conftest import make_job, make_worker

UTC = timezone.utc


def _ws_mock() -> AsyncMock:
    ws = AsyncMock()
    ws.emit_job_status_changed = AsyncMock()
    return ws


# ── Transient failure → RETRY_WAIT ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_transient_failure_moves_to_retry_wait(db_session: AsyncSession) -> None:
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    await make_job(db_session)

    job = await queue.claim_job(db_session, worker.id)
    assert job is not None

    result = await queue.fail_job(
        db_session, job.id, worker.id,
        error_message="OOM",
        error_category=ErrorCategory.TRANSIENT,
        retry=True,
    )

    assert result.status == JobStatus.RETRY_WAIT
    assert result.retry_count == 1
    assert result.worker_id is None
    assert result.lease_expires_at is None


@pytest.mark.asyncio
async def test_first_retry_has_zero_delay(db_session: AsyncSession) -> None:
    """First retry (retry_count was 0) → next_retry_after = None (claim immediately)."""
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    await make_job(db_session)

    job = await queue.claim_job(db_session, worker.id)
    assert job is not None
    assert job.retry_count == 0

    result = await queue.fail_job(
        db_session, job.id, worker.id,
        error_message="error",
        error_category=ErrorCategory.TRANSIENT,
        retry=True,
    )
    # First retry: delay[0]=0, so next_retry_after should be None
    assert result.next_retry_after is None


@pytest.mark.asyncio
async def test_second_retry_has_60s_delay(db_session: AsyncSession) -> None:
    """Second retry (retry_count was 1) → next_retry_after ≈ now + 60s."""
    queue = DistributedQueue()
    worker = await make_worker(db_session)

    # Create a job that has already been retried once
    job = await make_job(db_session, status=JobStatus.PROCESSING)
    job.worker_id = worker.id
    job.retry_count = 1
    await db_session.flush()

    result = await queue.fail_job(
        db_session, job.id, worker.id,
        error_message="timeout",
        error_category=ErrorCategory.TRANSIENT,
        retry=True,
    )

    assert result.status == JobStatus.RETRY_WAIT
    assert result.retry_count == 2
    assert result.next_retry_after is not None
    delay = (result.next_retry_after.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
    assert 55 <= delay <= 65, f"Expected ~60s delay, got {delay:.0f}s"


@pytest.mark.asyncio
async def test_third_retry_has_300s_delay(db_session: AsyncSession) -> None:
    queue = DistributedQueue()
    worker = await make_worker(db_session)

    job = await make_job(db_session, status=JobStatus.PROCESSING)
    job.worker_id = worker.id
    job.retry_count = 2
    await db_session.flush()

    result = await queue.fail_job(
        db_session, job.id, worker.id,
        error_message="error",
        error_category=ErrorCategory.TRANSIENT,
        retry=True,
    )

    assert result.next_retry_after is not None
    delay = (result.next_retry_after.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
    assert 290 <= delay <= 310, f"Expected ~300s delay, got {delay:.0f}s"


# ── Max retries → FAILED ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_retries_exhausted_goes_to_failed(db_session: AsyncSession) -> None:
    queue = DistributedQueue()
    worker = await make_worker(db_session)

    job = await make_job(db_session, status=JobStatus.PROCESSING)
    job.worker_id = worker.id
    job.retry_count = job.max_retries  # Already at limit
    await db_session.flush()

    result = await queue.fail_job(
        db_session, job.id, worker.id,
        error_message="repeated failure",
        error_category=ErrorCategory.TRANSIENT,
        retry=True,
    )

    assert result.status == JobStatus.FAILED
    assert result.worker_id is None


# ── Deterministic failure ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deterministic_failure_always_fails(db_session: AsyncSession) -> None:
    """Deterministic failure goes to FAILED immediately, regardless of retry_count."""
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    await make_job(db_session)

    job = await queue.claim_job(db_session, worker.id)
    assert job is not None
    assert job.retry_count == 0  # Fresh job

    result = await queue.fail_job(
        db_session, job.id, worker.id,
        error_message="Corrupt MP3 file",
        error_category=ErrorCategory.DETERMINISTIC,
        retry=True,  # Even with retry=True, deterministic failures don't retry
    )

    assert result.status == JobStatus.FAILED
    assert result.retry_count == 0, "retry_count must not increment for deterministic failures"


@pytest.mark.asyncio
async def test_retry_false_forces_failed(db_session: AsyncSession) -> None:
    """retry=False always results in FAILED, even for transient errors."""
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    await make_job(db_session)

    job = await queue.claim_job(db_session, worker.id)
    assert job is not None

    result = await queue.fail_job(
        db_session, job.id, worker.id,
        error_message="graceful shutdown",
        error_category=ErrorCategory.TRANSIENT,
        retry=False,
    )

    assert result.status == JobStatus.FAILED


# ── RetryScheduler ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_promotes_ready_jobs(db_session: AsyncSession) -> None:
    """Jobs in RETRY_WAIT with next_retry_after in the past → QUEUED."""
    scheduler = RetryScheduler(_ws_mock())

    past = datetime.now(UTC) - timedelta(seconds=1)
    job = await make_job(db_session, status=JobStatus.RETRY_WAIT)
    job.next_retry_after = past
    await db_session.flush()
    await db_session.commit()

    await scheduler._sweep()

    await db_session.refresh(job)
    assert job.status == JobStatus.QUEUED
    assert job.next_retry_after is None


@pytest.mark.asyncio
async def test_scheduler_promotes_null_next_retry_after(db_session: AsyncSession) -> None:
    """
    BUG-FIX REGRESSION: Delay=0 retries set next_retry_after=None (immediately claimable).
    The scheduler must also promote these, not just jobs with IS NOT NULL AND <= NOW().
    """
    scheduler = RetryScheduler(_ws_mock())

    # Simulate a delay=0 retry: next_retry_after is None
    job = await make_job(db_session, status=JobStatus.RETRY_WAIT)
    job.next_retry_after = None  # Immediately claimable
    await db_session.flush()
    await db_session.commit()

    await scheduler._sweep()

    await db_session.refresh(job)
    assert job.status == JobStatus.QUEUED, (
        "RETRY_WAIT jobs with next_retry_after=NULL (delay=0) must be promoted to QUEUED. "
        "Bug: original scheduler only matched IS NOT NULL AND <= NOW()."
    )


@pytest.mark.asyncio
async def test_scheduler_skips_future_retry_jobs(db_session: AsyncSession) -> None:
    """Jobs with next_retry_after in the future must NOT be promoted."""
    scheduler = RetryScheduler(_ws_mock())

    future = datetime.now(UTC) + timedelta(hours=1)
    job = await make_job(db_session, status=JobStatus.RETRY_WAIT)
    job.next_retry_after = future
    await db_session.flush()
    await db_session.commit()

    await scheduler._sweep()

    await db_session.refresh(job)
    assert job.status == JobStatus.RETRY_WAIT, "Future retry job must not be promoted"


@pytest.mark.asyncio
async def test_scheduler_only_processes_retry_wait_status(
    db_session: AsyncSession,
) -> None:
    """Scheduler must not touch QUEUED or FAILED jobs, even with old next_retry_after."""
    scheduler = RetryScheduler(_ws_mock())

    past = datetime.now(UTC) - timedelta(hours=1)

    queued_job = await make_job(db_session, status=JobStatus.QUEUED)
    queued_job.next_retry_after = past

    failed_job = await make_job(db_session, status=JobStatus.FAILED)
    failed_job.next_retry_after = past

    await db_session.flush()
    await db_session.commit()

    await scheduler._sweep()

    await db_session.refresh(queued_job)
    await db_session.refresh(failed_job)
    assert queued_job.status == JobStatus.QUEUED, "QUEUED job must not be touched"
    assert failed_job.status == JobStatus.FAILED, "FAILED job must not be touched"


# ── Manual retry (dashboard) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manual_retry_resets_retry_count(db_session: AsyncSession) -> None:
    """dashboard retry_job() must reset retry_count to 0 and move to QUEUED."""
    queue = DistributedQueue()

    job = await make_job(db_session, status=JobStatus.FAILED)
    job.retry_count = 3
    job.last_error = "previous error"
    await db_session.flush()

    result = await queue.retry_job(db_session, job.id)

    assert result is not None
    assert result.status == JobStatus.QUEUED
    assert result.retry_count == 0
    assert result.last_error is None
    assert result.next_retry_after is None


@pytest.mark.asyncio
async def test_manual_retry_cancelled_job(db_session: AsyncSession) -> None:
    """Cancelled jobs can also be manually retried."""
    queue = DistributedQueue()
    job = await make_job(db_session, status=JobStatus.CANCELLED)

    result = await queue.retry_job(db_session, job.id)

    assert result is not None
    assert result.status == JobStatus.QUEUED


@pytest.mark.asyncio
async def test_manual_retry_active_job_returns_none(db_session: AsyncSession) -> None:
    """Cannot retry an active job (must be FAILED or CANCELLED)."""
    queue = DistributedQueue()
    job = await make_job(db_session, status=JobStatus.PROCESSING)

    result = await queue.retry_job(db_session, job.id)

    assert result is None, "retry_job should return None for non-retryable states"
