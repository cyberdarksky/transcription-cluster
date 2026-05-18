from __future__ import annotations

from fastapi import HTTPException, status


class CoordinatorError(Exception):
    """Base exception for all coordinator errors."""


class JobNotFoundError(CoordinatorError):
    def __init__(self, job_id: object) -> None:
        super().__init__(f"Job not found: {job_id}")
        self.job_id = job_id


class WorkerNotFoundError(CoordinatorError):
    def __init__(self, worker_id: object) -> None:
        super().__init__(f"Worker not found: {worker_id}")
        self.worker_id = worker_id


class InvalidJobTransitionError(CoordinatorError):
    def __init__(self, job_id: object, current_status: str, target_status: str) -> None:
        super().__init__(
            f"Cannot transition job {job_id} from '{current_status}' to '{target_status}'"
        )
        self.job_id = job_id
        self.current_status = current_status
        self.target_status = target_status


class JobOwnershipError(CoordinatorError):
    """Raised when a worker tries to complete/fail a job it no longer owns."""

    def __init__(self, job_id: object, claimed_worker_id: object) -> None:
        super().__init__(
            f"Job {job_id} is not owned by worker {claimed_worker_id}. "
            "Possible reassignment due to heartbeat timeout."
        )


class DuplicateJobError(CoordinatorError):
    def __init__(self, input_path: str) -> None:
        super().__init__(f"Active job already exists for: {input_path}")
        self.input_path = input_path


class PathTraversalError(CoordinatorError):
    def __init__(self, path: str) -> None:
        super().__init__(f"Path traversal attempt detected: {path}")


class FileTooLargeError(CoordinatorError):
    def __init__(self, size: int, limit: int) -> None:
        super().__init__(f"File size {size} exceeds limit {limit}")


# ── HTTP exception factories ───────────────────────────────────────────────────


def http_job_not_found(job_id: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"detail": f"Job not found: {job_id}", "error_code": "JOB_NOT_FOUND"},
    )


def http_worker_not_found(worker_id: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"detail": f"Worker not found: {worker_id}", "error_code": "WORKER_NOT_FOUND"},
    )


def http_invalid_transition(current: str, target: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "detail": f"Cannot transition from '{current}' to '{target}'",
            "error_code": "INVALID_STATUS_TRANSITION",
        },
    )


def http_job_ownership_conflict(job_id: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "detail": f"Job {job_id} is no longer assigned to the requesting worker",
            "error_code": "JOB_OWNERSHIP_CONFLICT",
        },
    )
