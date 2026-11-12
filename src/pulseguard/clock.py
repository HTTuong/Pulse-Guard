"""Time is injected (architecture.md): code asks a Clock, never the system clock directly."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, always timezone aware in UTC."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def to_wire(instant: datetime) -> str:
    """Format a time as the API sends it: UTC, milliseconds, Z (api.md)."""
    if instant.tzinfo is None:
        raise ValueError("naive datetime: every time must carry an offset")
    return instant.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
