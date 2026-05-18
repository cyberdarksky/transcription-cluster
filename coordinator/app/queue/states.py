"""
Distributed queue state machine.

Single source of truth for:
- Valid state transitions
- Active / leaseable / terminal groupings
- Transition validation (used by DistributedQueue to enforce at DB level)
"""
from __future__ import annotations

from ..models.enums import JobStatus

# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------
#
# Design rules:
# 1. Transitions are validated at the WORKER-ID-GUARDED DB level (WHERE clauses),
#    not just in Python, to prevent race conditions.
# 2. Only one "happy path" through the active states: QUEUED → ASSIGNED →
#    DOWNLOADING → PROCESSING → UPLOADING → COMPLETED.
# 3. Error states (RETRY_WAIT, FAILED) are entered via fail_job().
# 4. Terminal states have an empty target set.
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({
        JobStatus.ASSIGNED,
    }),
    JobStatus.ASSIGNED: frozenset({
        JobStatus.DOWNLOADING,
        JobStatus.RETRY_WAIT,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }),
    JobStatus.DOWNLOADING: frozenset({
        JobStatus.PROCESSING,
        JobStatus.RETRY_WAIT,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }),
    JobStatus.PROCESSING: frozenset({
        JobStatus.UPLOADING,
        JobStatus.PAUSED,
        JobStatus.RETRY_WAIT,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }),
    JobStatus.UPLOADING: frozenset({
        JobStatus.COMPLETED,
        JobStatus.RETRY_WAIT,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }),
    JobStatus.PAUSED: frozenset({
        JobStatus.PROCESSING,
        JobStatus.CANCELLED,
    }),
    JobStatus.RETRY_WAIT: frozenset({
        JobStatus.QUEUED,       # Retry delay passed — ready for re-assignment
        JobStatus.FAILED,       # Max retries exhausted (set by retry scheduler)
        JobStatus.CANCELLED,
    }),
    JobStatus.COMPLETED: frozenset(),   # Terminal
    JobStatus.FAILED: frozenset({
        JobStatus.QUEUED,       # Manual retry via dashboard resets retry_count
    }),
    JobStatus.CANCELLED: frozenset(),   # Terminal
}

# Linear worker progression: each step has exactly one required previous state.
REQUIRED_PREVIOUS_STATE: dict[JobStatus, JobStatus] = {
    JobStatus.DOWNLOADING: JobStatus.ASSIGNED,
    JobStatus.PROCESSING:  JobStatus.DOWNLOADING,
    JobStatus.UPLOADING:   JobStatus.PROCESSING,
    JobStatus.COMPLETED:   JobStatus.UPLOADING,
}

# States where the worker holds an active lease that must be renewed.
LEASEABLE_STATUSES: frozenset[JobStatus] = frozenset({
    JobStatus.ASSIGNED,
    JobStatus.DOWNLOADING,
    JobStatus.PROCESSING,
    JobStatus.UPLOADING,
})

# States that appear "stuck" from the coordinator's perspective if lease expired.
RECOVERABLE_STATUSES: frozenset[JobStatus] = LEASEABLE_STATUSES

# States the queue will offer to waiting workers.
CLAIMABLE_STATUSES: frozenset[JobStatus] = frozenset({JobStatus.QUEUED})

# All non-terminal states.
ACTIVE_STATUSES: frozenset[JobStatus] = frozenset({
    JobStatus.QUEUED,
    JobStatus.ASSIGNED,
    JobStatus.DOWNLOADING,
    JobStatus.PROCESSING,
    JobStatus.UPLOADING,
    JobStatus.PAUSED,
    JobStatus.RETRY_WAIT,
})


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

class InvalidTransitionError(ValueError):
    """Raised when a requested state transition is not permitted."""

    def __init__(self, from_status: JobStatus, to_status: JobStatus) -> None:
        super().__init__(f"Invalid transition: {from_status!r} → {to_status!r}")
        self.from_status = from_status
        self.to_status = to_status


def validate_transition(from_status: JobStatus, to_status: JobStatus) -> None:
    """Raise InvalidTransitionError if the transition is not in VALID_TRANSITIONS."""
    allowed = VALID_TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise InvalidTransitionError(from_status, to_status)


def can_transition(from_status: JobStatus, to_status: JobStatus) -> bool:
    return to_status in VALID_TRANSITIONS.get(from_status, frozenset())
