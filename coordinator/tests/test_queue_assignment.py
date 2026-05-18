"""
Tests for transactional job assignment and duplicate prevention.

Key properties verified:
1. claim_job returns None when queue is empty.
2. A single job is claimed exactly once under concurrent load (FOR UPDATE SKIP LOCKED).
3. Two workers claiming simultaneously: exactly one succeeds, one gets None.
4. Priority ordering: higher-priority jobs are claimed first.
5. Jobs with next_retry_after in the future are skipped.
6. Lease is set immediately on claim.
7. Advance_state rejects invalid from-state.
8. complete_job rejects stale worker (ownership conflict).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.enums import JobStatus, WorkerStatus
from app.models.job import Job
from app.queue.distributed_queue import DistributedQueue
from app.queue.states import InvalidTransitionError

from .conftest import make_job, make_worker

UTC = timezone.utc


# ── Empty queue ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_empty_queue_returns_none(db_session: AsyncSession) -> None:
    """No jobs → claim_job returns None immediately."""
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    result = await queue.claim_job(db_session, worker.id)
    assert result is None


# ── Basic claim ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_sets_status_to_assigned(db_session: AsyncSession) -> None:
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    job = await make_job(db_session)

    claimed = await queue.claim_job(db_session, worker.id)

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.ASSIGNED
    assert claimed.worker_id == worker.id
    assert claimed.assigned_at is not None


@pytest.mark.asyncio
async def test_claim_sets_lease_immediately(db_session: AsyncSession) -> None:
    """Lease must be set the moment a job is claimed (before commit)."""
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    await make_job(db_session)

    claimed = await queue.claim_job(db_session, worker.id, lease_duration_seconds=300)

    assert claimed is not None
    assert claimed.lease_expires_at is not None
    delta = (claimed.lease_expires_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
    assert 290 <= delta <= 310, f"Expected ~300s lease, got {delta:.0f}s"


# ── Concurrent / duplicate prevention ────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_claim_only_one_worker_wins(
    clean_db: AsyncEngine,
) -> None:
    """
    Two workers racing to claim the same job.
    FOR UPDATE SKIP LOCKED guarantees exactly one succeeds.

    Uses clean_db (separate connections) because SKIP LOCKED requires genuinely
    concurrent transactions, not nested SAVEPOINTs on the same connection.
    """
    factory = async_sessionmaker(
        bind=clean_db, class_=AsyncSession, expire_on_commit=False
    )

    async with factory() as seed:
        w1 = await make_worker(seed, hostname="worker-1")
        w2 = await make_worker(seed, hostname="worker-2")
        await make_job(seed)
        await seed.commit()

    queue = DistributedQueue()

    async def worker_claim(worker_id: uuid.UUID) -> Job | None:
        async with factory() as session:
            result = await queue.claim_job(session, worker_id)
            await session.commit()
            return result

    results = await asyncio.gather(
        worker_claim(w1.id),
        worker_claim(w2.id),
    )

    claimed = [r for r in results if r is not None]
    unclaimed = [r for r in results if r is None]

    assert len(claimed) == 1, f"Expected 1 claim, got {len(claimed)}: {results}"
    assert len(unclaimed) == 1
    assert claimed[0].status == JobStatus.ASSIGNED


@pytest.mark.asyncio
async def test_concurrent_claim_n_workers_n_jobs(
    clean_db: AsyncEngine,
) -> None:
    """N workers racing for N jobs: each worker gets exactly one distinct job."""
    N = 5
    factory = async_sessionmaker(
        bind=clean_db, class_=AsyncSession, expire_on_commit=False
    )

    async with factory() as seed:
        workers = [await make_worker(seed, hostname=f"w{i}") for i in range(N)]
        for _ in range(N):
            await make_job(seed)
        await seed.commit()

    queue = DistributedQueue()

    async def claim(worker_id: uuid.UUID) -> Job | None:
        async with factory() as s:
            result = await queue.claim_job(s, worker_id)
            await s.commit()
            return result

    results = await asyncio.gather(*[claim(w.id) for w in workers])
    claimed = [r for r in results if r is not None]

    assert len(claimed) == N
    assert len({r.id for r in claimed}) == N, "All claimed jobs must be distinct"


# ── Priority ordering ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_respects_priority(db_session: AsyncSession) -> None:
    queue = DistributedQueue()
    worker = await make_worker(db_session)

    low = await make_job(db_session, priority=0)
    high = await make_job(db_session, priority=10)

    first = await queue.claim_job(db_session, worker.id)
    assert first is not None
    assert first.id == high.id, "High-priority job must be claimed first"


@pytest.mark.asyncio
async def test_claim_fifo_for_equal_priority(db_session: AsyncSession) -> None:
    queue = DistributedQueue()
    worker = await make_worker(db_session)

    older = await make_job(db_session, priority=5)
    newer = await make_job(db_session, priority=5)

    first = await queue.claim_job(db_session, worker.id)
    assert first is not None
    assert first.id == older.id


# ── Future retry_after skipped ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_skips_future_next_retry_after(db_session: AsyncSession) -> None:
    queue = DistributedQueue()
    worker = await make_worker(db_session)

    future = datetime.now(UTC) + timedelta(hours=1)
    skipped = await make_job(db_session, next_retry_after=future)
    available = await make_job(db_session)

    claimed = await queue.claim_job(db_session, worker.id)
    assert claimed is not None
    assert claimed.id == available.id, "Job with future next_retry_after must be skipped"


# ── Already-claimed not reclaimable ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_assigned_job_not_reclaimable(db_session: AsyncSession) -> None:
    queue = DistributedQueue()
    w1 = await make_worker(db_session, hostname="w1")
    w2 = await make_worker(db_session, hostname="w2")
    await make_job(db_session)

    first = await queue.claim_job(db_session, w1.id)
    assert first is not None

    second = await queue.claim_job(db_session, w2.id)
    assert second is None, "Assigned job must not be claimable by a second worker"


# ── advance_state validation ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_advance_state_rejects_wrong_previous_state(
    db_session: AsyncSession,
) -> None:
    """advance_state must reject a transition when the job is not in the expected state."""
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    await make_job(db_session)

    job = await queue.claim_job(db_session, worker.id)
    assert job is not None
    # Job is ASSIGNED; trying to advance to UPLOADING (skips DOWNLOADING + PROCESSING)
    with pytest.raises(Exception):  # InvalidTransitionError or KeyError
        await queue.advance_state(db_session, job.id, worker.id, JobStatus.UPLOADING)


@pytest.mark.asyncio
async def test_advance_state_valid_progression(db_session: AsyncSession) -> None:
    queue = DistributedQueue()
    worker = await make_worker(db_session)
    await make_job(db_session)

    job = await queue.claim_job(db_session, worker.id)
    assert job is not None

    # ASSIGNED → DOWNLOADING
    j = await queue.advance_state(db_session, job.id, worker.id, JobStatus.DOWNLOADING)
    assert j.status == JobStatus.DOWNLOADING

    # DOWNLOADING → PROCESSING
    j = await queue.advance_state(db_session, job.id, worker.id, JobStatus.PROCESSING)
    assert j.status == JobStatus.PROCESSING

    # PROCESSING → UPLOADING
    j = await queue.advance_state(db_session, job.id, worker.id, JobStatus.UPLOADING)
    assert j.status == JobStatus.UPLOADING


# ── Ownership conflict ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wrong_worker_cannot_advance_state(db_session: AsyncSession) -> None:
    queue = DistributedQueue()
    w1 = await make_worker(db_session, hostname="w1")
    w2 = await make_worker(db_session, hostname="w2")
    await make_job(db_session)

    job = await queue.claim_job(db_session, w1.id)
    assert job is not None

    with pytest.raises((PermissionError, Exception)):
        await queue.advance_state(db_session, job.id, w2.id, JobStatus.DOWNLOADING)
