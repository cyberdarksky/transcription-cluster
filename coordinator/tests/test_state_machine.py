"""
Tests for the job state machine.

Covers:
- All valid transitions are accepted
- All invalid transitions are rejected
- can_transition helper
- validate_transition helper
"""
from __future__ import annotations

import pytest

from app.models.enums import JobStatus
from app.queue.states import (
    VALID_TRANSITIONS,
    InvalidTransitionError,
    can_transition,
    validate_transition,
)


class TestValidTransitions:
    """Every pair in VALID_TRANSITIONS should be accepted."""

    @pytest.mark.parametrize(
        "from_s, to_s",
        [
            (JobStatus.QUEUED, JobStatus.ASSIGNED),
            (JobStatus.ASSIGNED, JobStatus.DOWNLOADING),
            (JobStatus.ASSIGNED, JobStatus.RETRY_WAIT),
            (JobStatus.ASSIGNED, JobStatus.FAILED),
            (JobStatus.ASSIGNED, JobStatus.CANCELLED),
            (JobStatus.DOWNLOADING, JobStatus.PROCESSING),
            (JobStatus.DOWNLOADING, JobStatus.RETRY_WAIT),
            (JobStatus.DOWNLOADING, JobStatus.CANCELLED),
            (JobStatus.PROCESSING, JobStatus.UPLOADING),
            (JobStatus.PROCESSING, JobStatus.PAUSED),
            (JobStatus.PROCESSING, JobStatus.RETRY_WAIT),
            (JobStatus.PROCESSING, JobStatus.FAILED),
            (JobStatus.PROCESSING, JobStatus.CANCELLED),
            (JobStatus.UPLOADING, JobStatus.COMPLETED),
            (JobStatus.UPLOADING, JobStatus.RETRY_WAIT),
            (JobStatus.UPLOADING, JobStatus.CANCELLED),
            (JobStatus.PAUSED, JobStatus.PROCESSING),
            (JobStatus.PAUSED, JobStatus.CANCELLED),
            (JobStatus.RETRY_WAIT, JobStatus.QUEUED),
            (JobStatus.RETRY_WAIT, JobStatus.FAILED),
            (JobStatus.RETRY_WAIT, JobStatus.CANCELLED),
            (JobStatus.FAILED, JobStatus.QUEUED),   # Manual retry
        ],
    )
    def test_valid_transition_accepted(self, from_s: JobStatus, to_s: JobStatus) -> None:
        assert can_transition(from_s, to_s) is True
        validate_transition(from_s, to_s)  # Must not raise


class TestInvalidTransitions:
    """Non-transitions and backwards jumps must be rejected."""

    @pytest.mark.parametrize(
        "from_s, to_s",
        [
            (JobStatus.QUEUED, JobStatus.PROCESSING),     # Skip ASSIGNED
            (JobStatus.QUEUED, JobStatus.COMPLETED),      # Skip everything
            (JobStatus.ASSIGNED, JobStatus.UPLOADING),    # Skip steps
            (JobStatus.DOWNLOADING, JobStatus.COMPLETED), # Skip steps
            (JobStatus.PROCESSING, JobStatus.QUEUED),     # Backward
            (JobStatus.COMPLETED, JobStatus.QUEUED),      # Terminal → re-enter
            (JobStatus.COMPLETED, JobStatus.PROCESSING),  # Terminal
            (JobStatus.CANCELLED, JobStatus.QUEUED),      # Terminal
            (JobStatus.FAILED, JobStatus.ASSIGNED),       # FAILED → only QUEUED allowed
            (JobStatus.PAUSED, JobStatus.QUEUED),         # PAUSED → only PROCESSING or CANCEL
            (JobStatus.PAUSED, JobStatus.UPLOADING),
        ],
    )
    def test_invalid_transition_rejected(self, from_s: JobStatus, to_s: JobStatus) -> None:
        assert can_transition(from_s, to_s) is False
        with pytest.raises(InvalidTransitionError) as exc_info:
            validate_transition(from_s, to_s)
        assert exc_info.value.from_status == from_s
        assert exc_info.value.to_status == to_s


class TestStatusGroupings:
    """Test the JobStatus semantic properties."""

    @pytest.mark.parametrize(
        "status, expected",
        [
            (JobStatus.ASSIGNED, True),
            (JobStatus.DOWNLOADING, True),
            (JobStatus.PROCESSING, True),
            (JobStatus.UPLOADING, True),
            (JobStatus.PAUSED, True),
            (JobStatus.QUEUED, False),
            (JobStatus.COMPLETED, False),
            (JobStatus.FAILED, False),
            (JobStatus.CANCELLED, False),
            (JobStatus.RETRY_WAIT, False),
        ],
    )
    def test_is_active(self, status: JobStatus, expected: bool) -> None:
        assert status.is_active == expected

    @pytest.mark.parametrize(
        "status",
        [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED],
    )
    def test_terminal_states(self, status: JobStatus) -> None:
        assert status.is_terminal
        assert VALID_TRANSITIONS[status] == frozenset()

    @pytest.mark.parametrize(
        "status",
        [JobStatus.ASSIGNED, JobStatus.DOWNLOADING, JobStatus.PROCESSING, JobStatus.UPLOADING],
    )
    def test_leaseable_states(self, status: JobStatus) -> None:
        assert status.is_leaseable

    def test_paused_is_not_leaseable(self) -> None:
        # PAUSED is active but does not require lease renewal
        assert JobStatus.PAUSED.is_active
        assert not JobStatus.PAUSED.is_leaseable
