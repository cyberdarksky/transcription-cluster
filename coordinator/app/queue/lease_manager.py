"""
Lease management for the distributed job queue.

A lease is a time-bounded exclusive claim on a job. The worker holding the
lease must renew it before it expires, or the recovery service will reclaim
the job and re-queue it.

Lease lifecycle:
    1. grant()  — called when a worker claims a job (QUEUED → ASSIGNED)
    2. renew()  — called on every worker heartbeat while job is active
    3. revoke() — called when a job completes/fails/is cancelled
    4. (auto)   — LeaseRecoveryService detects expired leases and re-queues them

Design:
    - Lease state is stored on the jobs.lease_expires_at column.
    - No separate lease table — avoids an extra JOIN on every heartbeat.
    - All operations are UPDATE statements with strict WHERE clauses to avoid
      race conditions (no separate SELECT + UPDATE).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from ..core.time_utils import utc_naive

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from ..models.enums import JobStatus
from ..models.job import Job
from .states import LEASEABLE_STATUSES

UTC = timezone.utc


class LeaseManager:
    """Low-level lease operations. Called from DistributedQueue — not from API routes."""

    def grant(
        self,
        job: Job,
        worker_id: uuid.UUID,
        duration_seconds: int,
    ) -> datetime:
        """
        Set initial lease on a job that has just been assigned.
        Mutates job in-place; caller must flush the session.

        Returns the lease expiry time.
        """
        expires_at = utc_naive() + timedelta(seconds=duration_seconds)
        job.lease_expires_at = expires_at
        job.lease_renewed_count = 0
        job.worker_id = worker_id
        return expires_at

    async def renew(
        self,
        db: AsyncSession,
        job_id: uuid.UUID,
        worker_id: uuid.UUID,
        duration_seconds: int,
    ) -> bool:
        """
        Extend the lease on an active job.

        Only succeeds if:
        - The job is in a leaseable state (ASSIGNED / DOWNLOADING / PROCESSING / UPLOADING)
        - The requesting worker still owns the job
        - The current lease has NOT yet expired (a tiny window: if the lease
          just expired and recovery already ran, we must NOT let the old worker
          renew; SKIP LOCKED in recovery prevents this race)

        Returns True if the lease was renewed, False otherwise.
        """
        new_expires = utc_naive() + timedelta(seconds=duration_seconds)

        result = await db.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.worker_id == worker_id,
                Job.status.in_(LEASEABLE_STATUSES),
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at >= func.now(),  # Do not renew an already-expired lease
            )
            .values(
                lease_expires_at=new_expires,
                lease_renewed_count=Job.lease_renewed_count + 1,
                updated_at=func.now(),
            )
        )
        return result.rowcount > 0

    async def renew_many(
        self,
        db: AsyncSession,
        job_ids: list[uuid.UUID],
        worker_id: uuid.UUID,
        duration_seconds: int,
    ) -> int:
        """
        Batch renew leases for multiple jobs owned by the same worker.
        Returns the count of successfully renewed leases.
        """
        if not job_ids:
            return 0

        new_expires = utc_naive() + timedelta(seconds=duration_seconds)
        result = await db.execute(
            update(Job)
            .where(
                Job.id.in_(job_ids),
                Job.worker_id == worker_id,
                Job.status.in_(LEASEABLE_STATUSES),
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at >= func.now(),
            )
            .values(
                lease_expires_at=new_expires,
                lease_renewed_count=Job.lease_renewed_count + 1,
                updated_at=func.now(),
            )
        )
        return result.rowcount

    async def revoke(
        self,
        db: AsyncSession,
        job_id: uuid.UUID,
        worker_id: uuid.UUID,
    ) -> bool:
        """
        Clear the lease when a job completes, fails, or is cancelled.
        Returns True if the lease was revoked, False if not owned by this worker.
        """
        result = await db.execute(
            update(Job)
            .where(Job.id == job_id, Job.worker_id == worker_id)
            .values(lease_expires_at=None, updated_at=func.now())
        )
        return result.rowcount > 0


# Module-level singleton
lease_manager = LeaseManager()
