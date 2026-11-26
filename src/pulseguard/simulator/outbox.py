"""The readings a sensor keeps until the monitor has answered them (P-15, P-16, P-17, P-20).

No I/O and no clock of its own: `now` is passed in, so tests control time exactly.

A reading is UNSENT or WAITING (sent, no answer yet). It leaves the outbox on ACK or ERROR for
its seq, because an ERROR means refused for good. Nothing else removes it, except the limit.
"""

from dataclasses import dataclass, field
from datetime import datetime

from pulseguard.simulator.generator import Reading

NOW_ALWAYS = float("-inf")  # a waiting reading to send again at once


@dataclass
class Outbox:
    window: int = 50  # readings waiting for an answer at once (P-16)
    ack_s: float = 3.0  # wait this long for an answer, then send again (P-16)
    limit: int = 1200  # held readings; the oldest is dropped beyond it (P-17)
    dropped: int = 0
    resent: int = 0
    # seq -> time it was last sent, or None while unsent. Insertion order is seq order.
    _sent_at: dict[int, float | None] = field(default_factory=dict)
    _readings: dict[int, Reading] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self._readings)

    @property
    def waiting(self) -> int:
        return sum(1 for t in self._sent_at.values() if t is not None)

    def seqs(self) -> list[int]:
        return list(self._readings)

    def add(self, reading: Reading) -> None:
        if self._readings and reading.seq <= next(reversed(self._readings)):
            raise ValueError(f"seq {reading.seq} is not above the last one: a seq is never reused")
        self._readings[reading.seq] = reading
        self._sent_at[reading.seq] = None
        while len(self._readings) > self.limit:
            oldest = next(iter(self._readings))
            del self._readings[oldest], self._sent_at[oldest]
            self.dropped += 1

    def due(self, now: float) -> list[Reading]:
        """The readings to put on the wire now, oldest seq first.

        Waiting readings whose answer is overdue come first, then unsent ones while the window has
        room. A waiting reading is never sent twice in one call.
        """
        out: list[Reading] = []
        in_flight = 0
        for seq, sent in self._sent_at.items():
            if sent is None:
                continue
            in_flight += 1
            if sent + self.ack_s <= now:
                out.append(self._readings[seq])
                self._sent_at[seq] = now
                self.resent += 1
        for seq, sent in self._sent_at.items():
            if in_flight >= self.window:
                break
            if sent is None:
                out.append(self._readings[seq])
                self._sent_at[seq] = now
                in_flight += 1
        return out

    def next_deadline(self) -> float | None:
        """When the oldest waiting reading becomes overdue; None when nothing is waiting."""
        times = [t + self.ack_s for t in self._sent_at.values() if t is not None]
        return min(times) if times else None

    def answered(self, seq: int) -> bool:
        """An ACK or ERROR for `seq`. False for one that is not held (a late or repeated answer)."""
        if seq not in self._readings:
            return False
        del self._readings[seq], self._sent_at[seq]
        return True

    def connection_lost(self) -> None:
        """Every waiting reading is sent again as soon as the next connection is ready (P-20)."""
        for seq, sent in self._sent_at.items():
            if sent is not None:
                self._sent_at[seq] = NOW_ALWAYS

    def discard_measured_from(self, at: datetime) -> int:
        """Drop readings measured at or after `at`: the sensor was unpaired by then (P-10)."""
        gone = [seq for seq, r in self._readings.items() if r.measured_at >= at]
        for seq in gone:
            del self._readings[seq], self._sent_at[seq]
        return len(gone)
