"""Readings of one simulated sensor, the same for the same seed.

Two things keep a run repeatable (REQ-NFR-03):
- time is an argument, never read from the system clock
- each parameter has its own random generator, seeded from the seed, the device and the
  parameter, so reading RR does not change the values of PR
A string seed is hashed the same way on every run, unlike `hash()`, which changes per process.
"""

import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from pulseguard.simulator.scenarios import Scenario

# Parameters each device type sends, in seq order (protocol.md, HELLO).
PARAMETERS_OF = {
    "PULSE_OXIMETER": ("PR", "SPO2"),
    "RESPIRATION": ("RR",),
}
UNIT_OF = {"PR": "beats/min", "SPO2": "%", "RR": "breaths/min"}
# Plausible ranges of REQ-MEA-02; noise never takes a value outside them.
RANGE_OF = {"PR": (0, 300), "SPO2": (0, 100), "RR": (0, 120)}
# Standard deviation of the noise, in the unit of the parameter.
NOISE_OF = {"PR": 1.5, "SPO2": 0.6, "RR": 0.8}


@dataclass(frozen=True)
class Reading:
    seq: int
    parameter: str
    value: int
    unit: str
    measured_at: datetime


@dataclass(frozen=True)
class Tick:
    """One second of the sensor: its readings, or none when it has no signal."""

    at: datetime
    signal: bool
    readings: tuple[Reading, ...]


class SensorSimulator:
    def __init__(
        self,
        device: str,
        device_type: str,
        scenario: Scenario,
        seed: int,
        start: datetime,
        interval: timedelta = timedelta(seconds=1),
        first_seq: int = 1,
    ) -> None:
        if device_type not in PARAMETERS_OF:
            raise ValueError(f"unknown device type {device_type!r}")
        if start.tzinfo is None:
            raise ValueError("start must carry an offset")
        if interval <= timedelta(0):
            raise ValueError("interval must be positive")
        if first_seq < 1:
            raise ValueError("seq starts at 1 (P-14)")
        self.device = device
        self.device_type = device_type
        self.parameters = PARAMETERS_OF[device_type]
        self.scenario = scenario
        self._start = start
        self._interval = interval
        self._next_seq = first_seq
        self._ticks = 0
        self._noise = {p: random.Random(f"{seed}:{device}:{p}") for p in self.parameters}

    @property
    def last_seq(self) -> int:
        """The highest seq used so far; a sensor stores it to survive a restart (P-14)."""
        return self._next_seq - 1

    def skip(self) -> None:
        """One moment passes without a measurement, for a sensor that is not measuring (P-10).

        Time moves on, so the next reading carries the right `measured_at`, but no seq is used and
        no noise is drawn: the readings after a pause do not depend on how long it was.
        """
        self._ticks += 1

    def tick(self) -> Tick:
        """The next moment of the sensor."""
        at = self._start + self._ticks * self._interval
        t_s = (at - self._start).total_seconds()
        self._ticks += 1
        # A probe that is off does not measure, so its random generators do not advance either:
        # the readings after it do not depend on how long it was off (kept simple and repeatable).
        if not self.scenario.has_signal(t_s):
            return Tick(at, signal=False, readings=())
        pr, spo2, rr = self.scenario.target(t_s)
        targets = {"PR": pr, "SPO2": spo2, "RR": rr}
        readings = []
        for parameter in self.parameters:
            low, high = RANGE_OF[parameter]
            noisy = targets[parameter] + self._noise[parameter].gauss(0, NOISE_OF[parameter])
            value = min(high, max(low, round(noisy)))
            readings.append(Reading(self._next_seq, parameter, value, UNIT_OF[parameter], at))
            self._next_seq += 1
        return Tick(at, signal=True, readings=tuple(readings))

    def run(self, seconds: int) -> Iterator[Tick]:
        for _ in range(seconds):
            yield self.tick()
