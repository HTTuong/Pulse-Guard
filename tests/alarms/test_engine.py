"""The pure alarm engine (REQ-ALM-01, REQ-ALM-02, REQ-ALM-04), with no database and no waiting.

`feed` sends one reading per second (or as given) through the engine for one side and returns
what changed at which second, so each test reads as a small timeline.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from pulseguard.alarms.engine import OpenAlarm, Outcome, Side, SideState, band, step, watched_sides
from pulseguard.alarms.limits import DEFAULT_LIMITS

T0 = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)


def feed(
    values: Sequence[float],
    parameter: str = "PR",
    side: Side = "HIGH",
    every: float = 1.0,
    alarm: OpenAlarm | None = None,
) -> tuple[list[tuple[float, str, str | None]], OpenAlarm | None]:
    """Readings at 0, every, 2*every, ... seconds. Returns [(second, change, priority)] and the
    alarm left open at the end."""
    state = SideState()
    seen: list[tuple[float, str, str | None]] = []
    for i, value in enumerate(values):
        at = T0 + timedelta(seconds=i * every)
        outcome: Outcome = step(
            state, alarm, band(DEFAULT_LIMITS[parameter], side, Decimal(str(value))), at
        )
        state, alarm = outcome.state, outcome.alarm
        seen += [(i * every, c.kind, c.priority) for c in outcome.changes]
    return seen, alarm


# Bands of the default limits, at every edge on both sides


@pytest.mark.parametrize(
    ("parameter", "side", "value", "expected"),
    [
        ("PR", "HIGH", 110, None), ("PR", "HIGH", 110.9, None), ("PR", "HIGH", 111, "MEDIUM"),
        ("PR", "HIGH", 130, "MEDIUM"), ("PR", "HIGH", 130.9, "MEDIUM"), ("PR", "HIGH", 131, "HIGH"),
        ("PR", "LOW", 41, None), ("PR", "LOW", 40.1, None), ("PR", "LOW", 40, "HIGH"),
        ("SPO2", "LOW", 94, None), ("SPO2", "LOW", 93, "MEDIUM"), ("SPO2", "LOW", 92, "MEDIUM"),
        ("SPO2", "LOW", 91, "HIGH"), ("SPO2", "HIGH", 100, None),
        ("RR", "HIGH", 20, None), ("RR", "HIGH", 21, "MEDIUM"), ("RR", "HIGH", 24, "MEDIUM"),
        ("RR", "HIGH", 25, "HIGH"), ("RR", "LOW", 9, None), ("RR", "LOW", 8, "HIGH"),
    ],
)  # fmt: skip
def test_bands_of_the_default_limits(
    parameter: str, side: Side, value: float, expected: str | None
) -> None:
    assert band(DEFAULT_LIMITS[parameter], side, Decimal(str(value))) == expected


def test_sides_watched_by_default() -> None:
    assert {p: watched_sides(limits) for p, limits in DEFAULT_LIMITS.items()} == {
        "RR": ["LOW", "HIGH"], "SPO2": ["LOW"], "PR": ["LOW", "HIGH"],
    }  # fmt: skip


# REQ-ALM-01: the examples of the PRD, and the edges around them


def test_pulse_140_for_6_s_raises_one_high_alarm() -> None:
    changes, alarm = feed([140] * 7)  # readings at 0..6 s
    assert changes == [(5, "RAISE", "HIGH")]
    assert alarm == OpenAlarm("HIGH", "ACTIVE", False)


def test_pulse_140_for_4_s_raises_nothing() -> None:
    assert feed([140] * 5) == ([], None)  # 0..4 s


def test_high_debounce_edge_is_exactly_5_s() -> None:
    assert feed([140] * 6)[0] == [(5, "RAISE", "HIGH")]  # 0..5 s


def test_pulse_120_for_11_s_raises_one_medium_alarm() -> None:
    assert feed([120] * 12)[0] == [(10, "RAISE", "MEDIUM")]


def test_pulse_120_for_9_s_raises_nothing() -> None:
    assert feed([120] * 10) == ([], None)


def test_respiration_every_5_s() -> None:
    """RR comes every 5 s, so a HIGH alarm needs two readings in the band, 5 s apart."""
    assert feed([30], "RR", every=5)[0] == []
    assert feed([30, 30], "RR", every=5)[0] == [(5, "RAISE", "HIGH")]
    assert feed([22, 22, 22], "RR", every=5)[0] == [(10, "RAISE", "MEDIUM")]


def test_one_normal_reading_breaks_the_run() -> None:
    changes, _ = feed([140, 140, 140, 140, 100, 140, 140, 140, 140, 140])
    assert changes == []  # 4 s, a break, then 4 s again


def test_a_gap_over_15_s_breaks_the_run() -> None:
    """REQ-ALM-05: 140 at 0 s and at 16 s, nothing between: not 16 s of tachycardia."""
    assert feed([140, 140], every=16)[0] == []


def test_a_gap_of_exactly_15_s_keeps_the_run() -> None:
    assert feed([140, 140], every=15)[0] == [(15, "RAISE", "HIGH")]


def test_a_mixed_run_of_medium_and_high_counts_for_medium() -> None:
    """120, 140, 120, 140 ...: never 5 s of HIGH, but 10 s of at least MEDIUM."""
    assert feed([120, 140] * 6)[0] == [(10, "RAISE", "MEDIUM")]


# REQ-ALM-02: one alarm per condition


def test_60_s_of_pulse_140_give_exactly_one_alarm() -> None:
    assert feed([140] * 61)[0] == [(5, "RAISE", "HIGH")]


def test_medium_alarm_is_escalated_not_duplicated() -> None:
    changes, alarm = feed([120] * 11 + [140] * 6)
    assert changes == [(10, "RAISE", "MEDIUM"), (16, "ESCALATE", "HIGH")]
    assert alarm is not None and alarm.priority == "HIGH"


def test_escalation_makes_an_acknowledged_alarm_active_again() -> None:
    acknowledged = OpenAlarm("MEDIUM", "ACKNOWLEDGED", False)
    changes, alarm = feed([140] * 6, alarm=acknowledged)
    assert changes == [(5, "ESCALATE", "HIGH")]
    assert alarm == OpenAlarm("HIGH", "ACTIVE", False)


def test_a_high_alarm_is_never_lowered_back_to_medium() -> None:
    changes, alarm = feed([140] * 6 + [120] * 20)
    assert changes == [(5, "RAISE", "HIGH")]
    assert alarm is not None and alarm.priority == "HIGH"


# REQ-ALM-04: latching and clearing


def test_high_alarm_latches_when_the_value_returns() -> None:
    changes, alarm = feed([140] * 6 + [80] * 11)  # normal from 6 s to 16 s
    assert changes == [(5, "RAISE", "HIGH"), (16, "CLEARED", None)]
    assert alarm == OpenAlarm("HIGH", "ACTIVE", True)


def test_high_alarm_is_not_cleared_before_10_s_of_normal() -> None:
    changes, _ = feed([140] * 6 + [80] * 10)  # normal 6..15 s, 9 s
    assert changes == [(5, "RAISE", "HIGH")]


def test_a_cleared_high_alarm_that_recurs_is_the_same_alarm() -> None:
    changes, alarm = feed([140] * 6 + [80] * 11 + [140])
    assert changes == [(5, "RAISE", "HIGH"), (16, "CLEARED", None), (17, "RECURRED", None)]
    assert alarm == OpenAlarm("HIGH", "ACTIVE", False)


def test_medium_alarm_resolves_by_itself_after_10_s_of_normal() -> None:
    changes, alarm = feed([120] * 11 + [80] * 11)
    assert changes == [(10, "RAISE", "MEDIUM"), (21, "RESOLVE", None)]
    assert alarm is None


def test_an_acknowledged_high_alarm_resolves_when_cleared() -> None:
    acknowledged = OpenAlarm("HIGH", "ACKNOWLEDGED", False)
    changes, alarm = feed([80] * 11, alarm=acknowledged)
    assert changes == [(10, "RESOLVE", None)]
    assert alarm is None


def test_after_resolving_a_new_alarm_needs_a_new_debounce() -> None:
    changes, _ = feed([120] * 11 + [80] * 11 + [120] * 11)
    assert changes == [(10, "RAISE", "MEDIUM"), (21, "RESOLVE", None), (32, "RAISE", "MEDIUM")]


def feed_at(points: Sequence[tuple[float, float]], side: Side = "HIGH") -> list[tuple[float, str]]:
    """Pulse readings at chosen seconds: [(second, value)] -> [(second, change)]."""
    state, alarm = SideState(), None
    seen: list[tuple[float, str]] = []
    for second, value in points:
        outcome = step(state, alarm, band(DEFAULT_LIMITS["PR"], side, Decimal(str(value))),
                       T0 + timedelta(seconds=second))  # fmt: skip
        state, alarm = outcome.state, outcome.alarm
        seen += [(second, c.kind) for c in outcome.changes]
    return seen


def test_a_gap_does_not_clear_an_alarm() -> None:
    """No data is not normal data: 80 at 6 s and at 22 s, nothing between, is not 16 s of normal."""
    points = [(s, 140.0) for s in range(6)] + [(6, 80), (22, 80)]
    assert feed_at(points) == [(5, "RAISE")]


def test_after_a_gap_normal_data_clears_on_its_own_10_s() -> None:
    points = [(s, 140.0) for s in range(6)] + [(22 + s, 80.0) for s in range(11)]
    assert feed_at(points) == [(5, "RAISE"), (32, "CLEARED")]


# Order


def test_a_reading_not_newer_than_the_last_is_skipped() -> None:
    first = step(SideState(), None, "HIGH", T0 + timedelta(seconds=10))
    for at in (T0 + timedelta(seconds=10), T0 + timedelta(seconds=3)):
        late = step(first.state, first.alarm, "HIGH", at)
        assert late.skipped and late.state == first.state and late.changes == ()
