"""Waiting times between connection attempts (P-19).

1 s, 2 s, 4 s, 8 s, 16 s, then 30 s for every later attempt, each plus or minus 10 % of jitter.
Jitter keeps a ward of sensors from reconnecting at the same moment after a monitor restart. It
comes from a seeded generator, so a test can predict every wait (REQ-NFR-03).
"""

import random
from dataclasses import dataclass, field

STEPS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)  # the last one repeats
JITTER = 0.10


@dataclass
class Backoff:
    seed: int
    device: str
    steps: tuple[float, ...] = STEPS
    jitter: float = JITTER
    scale: float = 1.0  # tests shrink every wait by the same factor
    attempt: int = 0
    _random: random.Random = field(init=False)

    def __post_init__(self) -> None:
        # A string seed is hashed the same way in every process; hash() of a string is not.
        self._random = random.Random(f"{self.seed}:{self.device}:backoff")

    def next_delay(self) -> float:
        """The wait before the next attempt, in seconds."""
        base = self.steps[min(self.attempt, len(self.steps) - 1)]
        self.attempt += 1
        factor = 1 + self._random.uniform(-self.jitter, self.jitter)
        return base * factor * self.scale

    def reset(self) -> None:
        """After a connection that got through its handshake the next failure starts at 1 s again.

        The random generator is not reset: the jitter keeps being a fixed sequence.
        """
        self.attempt = 0
