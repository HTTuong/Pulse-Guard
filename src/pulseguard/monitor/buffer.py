"""Where the monitor keeps readings until the backend has them (P-12, P-14).

This is in memory for now: an ACK means the reading is in this buffer, and a restart loses it.
The disk buffer that REQ-CON-03 needs comes with the pipeline to the backend (d250), behind the
same `store` method.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class StoreResult(StrEnum):
    STORED = "STORED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class Entry:
    parameter: str
    value: Decimal
    measured_at: datetime


@dataclass
class MemoryBuffer:
    readings: dict[str, dict[int, Entry]] = field(default_factory=dict)
    highest_seq: dict[str, int] = field(default_factory=dict)
    gaps: int = 0  # times a seq jumped ahead: readings were lost before the monitor (P-14)
    conflicts: int = 0

    def store(self, device: str, seq: int, entry: Entry) -> tuple[StoreResult, bool]:
        """Keep a reading. Also says whether it left a gap in the sequence."""
        known = self.readings.setdefault(device, {})
        if seq in known:
            if known[seq] == entry:
                return StoreResult.DUPLICATE, False
            self.conflicts += 1
            return StoreResult.CONFLICT, False
        previous = self.highest_seq.get(device, 0)
        gap = seq > previous + 1
        if gap:
            self.gaps += 1
        known[seq] = entry
        self.highest_seq[device] = max(previous, seq)
        return StoreResult.STORED, gap
