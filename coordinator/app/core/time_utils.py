"""UTC timestamps compatible with TIMESTAMP WITHOUT TIME ZONE columns."""
from __future__ import annotations

from datetime import datetime, timezone

UTC = timezone.utc


def utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
