"""What a simulated patient does over time.

A scenario gives the target value of each vital sign at every second since the start, and
whether the sensor has a signal. The generator adds noise on top. Scenarios are plain data, so
a new one is a new entry and not new code.

The numbers are chosen against the default NEWS2 limits in `alarms/limits.py`:
- tachycardia ends at PR 140, above the HIGH limit of 131 by about 6 noise steps
- desaturation ends at SPO2 88, below the HIGH limit of 91
"""

from dataclasses import dataclass

NORMAL_PR = 72.0
NORMAL_SPO2 = 97.0
NORMAL_RR = 16.0


@dataclass(frozen=True)
class Keyframe:
    """Target values from `at_s` seconds on; between two keyframes they change linearly."""

    at_s: float
    pr: float
    spo2: float
    rr: float


@dataclass(frozen=True)
class Scenario:
    name: str
    keyframes: tuple[Keyframe, ...]
    # Half open intervals (start_s, end_s) in which the sensor has no signal (probe off).
    no_signal: tuple[tuple[float, float], ...] = ()

    def __post_init__(self) -> None:
        times = [k.at_s for k in self.keyframes]
        if not times or times[0] != 0 or times != sorted(set(times)):
            raise ValueError("keyframes must start at 0 and be in strictly increasing time")

    def target(self, t_s: float) -> tuple[float, float, float]:
        """The (pr, spo2, rr) the patient has at `t_s` seconds, before noise."""
        frames = self.keyframes
        if t_s >= frames[-1].at_s:
            last = frames[-1]
            return last.pr, last.spo2, last.rr
        for before, after in zip(frames, frames[1:], strict=False):
            if before.at_s <= t_s < after.at_s:
                share = (t_s - before.at_s) / (after.at_s - before.at_s)
                return (
                    before.pr + (after.pr - before.pr) * share,
                    before.spo2 + (after.spo2 - before.spo2) * share,
                    before.rr + (after.rr - before.rr) * share,
                )
        raise ValueError(f"no keyframe for t={t_s}")  # t below 0

    def has_signal(self, t_s: float) -> bool:
        return not any(start <= t_s < end for start, end in self.no_signal)


_NORMAL = Keyframe(0, NORMAL_PR, NORMAL_SPO2, NORMAL_RR)

SCENARIOS: dict[str, Scenario] = {
    "normal": Scenario("normal", (_NORMAL,)),
    "tachycardia": Scenario(
        "tachycardia",
        (
            _NORMAL,
            Keyframe(30, NORMAL_PR, NORMAL_SPO2, NORMAL_RR),
            Keyframe(60, 140, NORMAL_SPO2, NORMAL_RR),
        ),
    ),
    "desaturation": Scenario(
        "desaturation",
        (
            _NORMAL,
            Keyframe(30, NORMAL_PR, NORMAL_SPO2, NORMAL_RR),
            Keyframe(60, NORMAL_PR, 88, NORMAL_RR),
        ),
    ),
    # 30 s without signal is longer than the 15 s after which the monitor reports SENSOR_LOST.
    "sensor_off": Scenario("sensor_off", (_NORMAL,), no_signal=((30, 60),)),
}


def scenario_named(name: str) -> Scenario:
    try:
        return SCENARIOS[name]
    except KeyError:
        raise ValueError(
            f"unknown scenario {name!r}; known: {', '.join(sorted(SCENARIOS))}"
        ) from None
