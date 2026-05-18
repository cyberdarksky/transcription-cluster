"""
Tests for the lease system and crash recovery.

Covers:
- Lease is granted on claim with correct expiry
- Lease renewal extends the expiry
- Expired lease renewal returns False
- LeaseRecoveryService re-queues expired leases
- Worker crash: job re-queued after lease expiry
- Max retries exceeded: job goes to FAILED
- Recovery uses FOR UPDATE SKIP LOCKED (concurrent recovery is safe)
- Worker.current_job_id cleared after recovery
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ErrorCategory, JobStatus, WorkerStatus
from app.models.job import Job
from app.models.worker import Worker
from app.queue.distributed_queue import DistributedQueue
from app.queue.lease_manager import LeaseManager
from app.queue.recovery_service import LeaseRecoveryService

from .conftest import make_job, make_worker

UTC = timezone.utc


# ── Lease grant ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_grants_lease(db_session: AsyncSession) -> None:
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    await make_job(db_session)

    job = await queue.claim_job(db_session, worker.id, lease_duration_seconds=120)

    assert job is not None
    assert job.lease_expires_at is not None
    delta = (job.lease_expires_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
    assert 110 <= delta <= 130, f"Expected ~120s lease, got {delta:.0f}s"
    assert job.lease_renewed_count == 0


# ── Lease renewal ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lease_renewal_extends_expiry(db_session: AsyncSession) -> None:
    queue = DistributedQueue()
    lm = LeaseManager()
    worker = await make_worker(db_session)
    await make_job(db_session)

    job = await queue.claim_job(db_session, worker.id, lease_duration_seconds=60)
    assert job is not None

    # Renew with longer duration
    renewed = await lm.renew(db_session, job.id, worker.id, duration_seconds=300)
    assert renewed is True

    # Re-fetch to see updated expiry
    await db_session.refresh(job)
    delta = (job.lease_expires_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
    assert delta > 60, "Renewal should have extended the lease beyond the original 60s"
    assert job.lease_renewed_count == 1


@pytest.mark.asyncio
async def test_expired_lease_renewal_returns_false(db_session: AsyncSession) -> None:
    """A worker cannot renew a lease that has already expired."""
    lm = LeaseManager()
    worker = await make_worker(db_session)

    # Create a job with an already-expired lease
    past = datetime.now(UTC) - timedelta(seconds=10)
    job = await make_job(db_session, status=JobStatus.ASSIGNED)
    job.worker_id = worker.id
    job.lease_expires_at = past
    await db_session.flush()

    renewed = await lm.renew(db_session, job.id, worker.id, duration_seconds=300)
    assert renewed is False, "Should not be able to renew an expired lease"


@pytest.mark.asyncio
async def test_wrong_worker_cannot_renew_lease(db_session: AsyncSession) -> None:
    """Worker B cannot renew the lease of a job assigned to Worker A."""
    lm = LeaseManager()
    queue = DistributedQueue()
    w1 = await make_worker(db_session, hostname="w1")
    w2 = await make_worker(db_session, hostname="w2")
    await make_job(db_session)

    job = await queue.claim_job(db_session, w1.id, lease_duration_seconds=300)
    assert job is not None

    renewed = await lm.renew(db_session, job.id, w2.id, duration_seconds=300)
    assert renewed is False


# ── Recovery service ──────────────────────────────────────────────────────────


def _make_ws_mock() -> AsyncMock:
    ws = AsyncMock()
    ws.emit_job_status_changed = AsyncMock()
    ws.emit_system_alert = AsyncMock()
    return ws


async def _expire_lease(session: AsyncSession, job: Job) -> None:
    """Helper: force a job's lease to be expired."""
    past = datetime.now(UTC) - timedelta(seconds=5)
    await session.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(lease_expires_at=past)
    )
    await session.flush()


@pytest.mark.asyncio
async def test_recovery_requeues_expired_lease_job(db_session: AsyncSession) -> None:
    """A job with an expired lease should be moved to RETRY_WAIT."""
    ws = _make_ws_mock()
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    await make_job(db_session)

    job = await queue.claim_job(db_session, worker.id, lease_duration_seconds=300)
    assert job is not None

    # Manually expire the lease
    await _expire_lease(db_session, job)
    await db_session.commit()

    # Run one recovery sweep
    recovery = LeaseRecoveryService(ws)
    await recovery._sweep()

    # Check the job was re-queued
    await db_session.refresh(job)
    assert job.status == JobStatus.RETRY_WAIT
    assert job.retry_count == 1
    assert job.worker_id is None
    assert job.lease_expires_at is None


@pytest.mark.asyncio
async def test_recovery_fails_job_when_max_retries_exceeded(
    db_session: AsyncSession,
) -> None:
    """When retry_count == max_retries, recovery moves job to FAILED (not RETRY_WAIT)."""
    ws = _make_ws_mock()
    queue = DistributedQueue()
    worker = await make_worker(db_session)

    # Create job already at max retries
    job = await make_job(db_session, status=JobStatus.PROCESSING)
    job.worker_id = worker.id
    job.retry_count = job.max_retries  # Exhausted
    lease_past = datetime.now(UTC) - timedelta(seconds=5)
    job.lease_expires_at = lease_past
    await db_session.flush()
    await db_session.commit()

    recovery = LeaseRecoveryService(ws)
    await recovery._sweep()

    await db_session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert job.worker_id is None
    assert job.lease_expires_at is None


@pytest.mark.asyncio
async def test_recovery_clears_worker_current_job_id(db_session: AsyncSession) -> None:
    """
    BUG-FIX REGRESSION: After lease recovery, the worker's current_job_id must be cleared.

    Original bug: recovery set job.worker_id = None before capturing it into
    _RecoveryEvent.worker_id. The event always held None. The batch UPDATE to
    clear worker.current_job_id built an empty affected_worker_ids list and
    was a no-op — worker.current_job_id was never cleared.
    """
    ws = _make_ws_mock()
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    await make_job(db_session)

    job = await queue.claim_job(db_session, worker.id, lease_duration_seconds=60)
    assert job is not None

    # Set worker.current_job_id to simulate active state
    worker.current_job_id = job.id
    await db_session.flush()

    await _expire_lease(db_session, job)
    await db_session.commit()

    recovery = LeaseRecoveryService(ws)
    await recovery._sweep()

    await db_session.refresh(worker)
    assert worker.current_job_id is None, (
        "worker.current_job_id must be NULL after lease recovery. "
        "Bug: old_worker_id was captured AFTER job.worker_id was set to None."
    )


# ── Worker crash scenario ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_crash_job_recovered_and_reclaimable(
    db_session: AsyncSession,
) -> None:
    """
    Simulates a worker crash:
    1. Worker claims a job (lease set).
    2. Worker process dies (no heartbeat, no lease renewal).
    3. LeaseRecoveryService detects expired lease → RETRY_WAIT.
    4. RetryScheduler promotes → QUEUED.
    5. Another worker can claim the job.
    """
    from app.queue.retry_scheduler import RetryScheduler
    from sqlalchemy import update as sa_update

    ws = _make_ws_mock()
    queue = DistributedQueue()

    w1 = await make_worker(db_session, hostname="crashed-worker")
    w2 = await make_worker(db_session, hostname="rescue-worker")
    await make_job(db_session)

    # Step 1: Worker 1 claims
    job = await queue.claim_job(db_session, w1.id, lease_duration_seconds=300)
    assert job is not None
    job_id = job.id

    # Step 2: Simulate crash — expire the lease and advance to PROCESSING
    await db_session.execute(
        sa_update(Job)
        .where(Job.id == job_id)
        .values(
            status=JobStatus.PROCESSING,
            started_at=datetime.now(UTC),
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    await db_session.flush()
    await db_session.commit()

    # Step 3: Recovery detects expired lease → RETRY_WAIT
    recovery = LeaseRecoveryService(ws)
    await recovery._sweep()

    await db_session.refresh(job)
    assert job.status == JobStatus.RETRY_WAIT

    # Step 4: Scheduler promotes → QUEUED (simulate delay passed)
    await db_session.execute(
        sa_update(Job)
        .where(Job.id == job_id)
        .values(next_retry_after=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()

    scheduler = RetryScheduler(ws)
    await scheduler._sweep()

    await db_session.refresh(job)
    assert job.status == JobStatus.QUEUED

    # Step 5: Worker 2 claims it
    await db_session.commit()
    claimed_by_w2 = await queue.claim_job(db_session, w2.id)
    assert claimed_by_w2 is not None
    assert claimed_by_w2.id == job_id
    assert claimed_by_w2.worker_id == w2.id


# ── Pause / Resume lease bugs ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pause_clears_lease(db_session: AsyncSession) -> None:
    """
    BUG-FIX REGRESSION: pause_job must clear lease_expires_at.

    During PAUSED state, heartbeats cannot renew the lease (PAUSED is not in
    LEASEABLE_STATUSES). The original implementation left the ticking lease in
    place. When it expired, the recovery service re-queued the paused job as if
    it had crashed.
    """
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    await make_job(db_session)

    job = await queue.claim_job(db_session, worker.id, lease_duration_seconds=300)
    assert job is not None
    assert job.lease_expires_at is not None  # Lease was granted

    # Advance to PROCESSING
    j = await queue.advance_state(db_session, job.id, worker.id, JobStatus.DOWNLOADING)
    j = await queue.advance_state(db_session, job.id, worker.id, JobStatus.PROCESSING)

    # Pause the job
    paused = await queue.pause_job(db_session, job.id)
    assert paused is not None
    assert paused.status == JobStatus.PAUSED
    assert paused.lease_expires_at is None, (
        "Lease must be cleared on pause. "
        "Bug: without clearing, the expired lease triggers recovery on the paused job."
    )


@pytest.mark.asyncio
async def test_resume_grants_fresh_lease(db_session: AsyncSession) -> None:
    """
    BUG-FIX REGRESSION: resume_job must grant a fresh lease.

    After a pause (during which the lease was cleared), resuming to PROCESSING
    with no lease means the recovery service would immediately re-queue the job
    on the next sweep (no lease = lease_expires_at IS NULL, recovery ignores it,
    but once the job is PROCESSING the recovery service would pick it up as
    having an expired lease if one existed and wasn't renewed).
    """
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    await make_job(db_session)

    job = await queue.claim_job(db_session, worker.id, lease_duration_seconds=300)
    assert job is not None

    await queue.advance_state(db_session, job.id, worker.id, JobStatus.DOWNLOADING)
    await queue.advance_state(db_session, job.id, worker.id, JobStatus.PROCESSING)
    await queue.pause_job(db_session, job.id)

    # Resume
    resumed = await queue.resume_job(db_session, job.id)
    assert resumed is not None
    assert resumed.status == JobStatus.PROCESSING
    assert resumed.lease_expires_at is not None, (
        "A fresh lease must be granted on resume. "
        "Bug: without a new lease, the recovery service has no lease to expire "
        "but the paused_then_resumed job won't renew its (non-existent) lease."
    )
    delta = (resumed.lease_expires_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
    assert delta > 0, "Fresh lease must expire in the future"


# ── Cancel worker_id capture bug ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_clears_worker_current_job_id(db_session: AsyncSession) -> None:
    """
    BUG-FIX REGRESSION: cancel_job must clear worker.current_job_id.

    Original bug: cancel_job read job.worker_id from UPDATE...RETURNING AFTER
    the UPDATE set worker_id=NULL. The condition `if job.worker_id is not None`
    was always False, so worker.current_job_id was never cleared.
    """
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    await make_job(db_session)

    job = await queue.claim_job(db_session, worker.id)
    assert job is not None

    worker.current_job_id = job.id
    await db_session.flush()

    cancelled = await queue.cancel_job(db_session, job.id)
    assert cancelled is not None
    assert cancelled.status == JobStatus.CANCELLED

    await db_session.refresh(worker)
    assert worker.current_job_id is None, (
        "worker.current_job_id must be cleared when a job is cancelled. "
        "Bug: old_worker_id was read from RETURNING after it was already set to NULL."
    )


# ── Deterministic error: no retry ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deterministic_error_skips_recovery(db_session: AsyncSession) -> None:
    """A job marked as DETERMINISTIC failure should go to FAILED, not RETRY_WAIT."""
    ws = _make_ws_mock()
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    await make_job(db_session)

    job = await queue.claim_job(db_session, worker.id)
    assert job is not None

    # Mark as deterministic failure
    job.error_category = ErrorCategory.DETERMINISTIC
    # Expire the lease
    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    await db_session.commit()

    recovery = LeaseRecoveryService(ws)
    await recovery._sweep()

    await db_session.refresh(job)
    assert job.status == JobStatus.FAILED, "Deterministic failure should not be retried"
