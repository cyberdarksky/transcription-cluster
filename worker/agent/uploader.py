"""
Result uploader — sends SRT and JSON files to the coordinator.

The coordinator's /complete endpoint validates job ownership under a
database-level lock. If ownership was lost (lease expired, job recovered),
the upload is rejected with 409 Conflict and we raise OwnershipLostError.

Retry policy for transient network errors:
  Up to MAX_RETRIES attempts with exponential backoff.
  409 Conflict (ownership lost) is NOT retried — it's a definitive signal
  that the coordinator has reassigned the job.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

import httpx

from .coordinator_client import CoordinatorClient

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]


class OwnershipLostError(RuntimeError):
    """Raised when the coordinator rejects the upload (409 Conflict)."""


async def upload_results(
    client: CoordinatorClient,
    job_id: uuid.UUID,
    worker_id: uuid.UUID,
    srt_path: Path,
    json_path: Path,
    audio_duration_seconds: float,
    processing_time_seconds: float,
    segment_count: int,
    word_count: int,
) -> None:
    """
    Upload output files to coordinator with retry on transient errors.

    Raises:
        OwnershipLostError: coordinator returned 409 (job was reassigned).
        RuntimeError: all retries exhausted on network errors.
    """
    for attempt in range(MAX_RETRIES):
        try:
            await client.complete_job(
                job_id=job_id,
                worker_id=worker_id,
                srt_path=srt_path,
                json_path=json_path,
                audio_duration_seconds=audio_duration_seconds,
                processing_time_seconds=processing_time_seconds,
                segment_count=segment_count,
                word_count=word_count,
            )
            logger.info(
                "Upload complete",
                extra={
                    "job_id": str(job_id),
                    "srt_bytes": srt_path.stat().st_size,
                    "json_bytes": json_path.stat().st_size,
                },
            )
            return

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                # Coordinator rejected: job ownership was lost mid-upload.
                raise OwnershipLostError(
                    f"Job {job_id} ownership conflict during upload: "
                    f"coordinator returned 409"
                ) from exc

            # Other HTTP errors → retry
            logger.warning(
                "Upload HTTP error (attempt %d/%d): %s",
                attempt + 1, MAX_RETRIES, exc,
            )

        except (httpx.RequestError, OSError) as exc:
            logger.warning(
                "Upload network error (attempt %d/%d): %s",
                attempt + 1, MAX_RETRIES, exc,
            )

        if attempt < MAX_RETRIES - 1:
            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
            logger.info("Retrying upload in %ds...", delay)
            await asyncio.sleep(delay)

    raise RuntimeError(
        f"Upload failed after {MAX_RETRIES} attempts for job {job_id}"
    )
