"""The alarm engine. Pure: no database, no clock, no I/O. It is fed one reading at a time, in
measured order, and returns the new state and what changed (architecture.md, design rules).

Words used here:
- side: a parameter can be too LOW or too HIGH; each side is watched on its own and has its own
  alarm code (PR_LOW, PR_HIGH, ...).
- band: where a value falls on one side: None (out of the alarm band), MEDIUM or HIGH.
- run: an unbroken stretch of readings in a band. Its duration is the measured time of the latest
  reading minus that of the first. Readings more than MAX_GAP apart break a run.

Rules (prd.md):
- REQ-ALM-01: a HIGH alarm is raised when a HIGH run lasts HIGH_DEBOUNCE; a MEDIUM alarm when a run
  of at least MEDIUM lasts MEDIUM_DEBOUNCE. Durations are inclusive: exactly 5 s raises.
- REQ-ALM-02: one alarm per side; a MEDIUM alarm whose HIGH run reaches HIGH_DEBOUNCE is escalated
  to HIGH, and becomes ACTIVE again even if it was acknowledged, so the worse value is seen.
- REQ-ALM-04: "cleared" means out of the alarm band for CLEAR_TIME. An ACTIVE HIGH alarm latches:
  when cleared it stays ACTIVE, marked condition_cleared, until someone acknowledges it. Any other
  open alarm (MEDIUM, or already ACKNOWLEDGED) is resolved when cleared.
- REQ-ALM-05: no data, no decision. A gap longer than MAX_GAP starts every run again.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

Side = Literal["LOW", "HIGH"]
Priority = Literal["MEDIUM", "HIGH"]
AlarmState = Literal["ACTIVE", "ACKNOWLEDGED"]

HIGH_DEBOUNCE = timedelta(seconds=5)
MEDIUM_DEBOUNCE = timedelta(seconds=10)
CLEAR_TIME = timedelta(seconds=10)
MAX_GAP = timedelta(seconds=15)


@dataclass(frozen=True)
class Limits:
    """At or below high_low, or at or above high_high: HIGH. At or below medium_low, or at or
    above medium_high: MEDIUM. None turns that limit off (database.md, session_limit)."""

    high_low: Decimal | None
    medium_low: Decimal | None
    medium_high: Decimal | None
    high_high: Decimal | None


def band(limits: Limits, side: Side, value: Decimal) -> Priority | None:
    if side == "LOW":
        if limits.high_low is not None and value <= limits.high_low:
            return "HIGH"
        if limits.medium_low is not None and value <= limits.medium_low:
            return "MEDIUM"
        return None
    if limits.high_high is not None and value >= limits.high_high:
        return "HIGH"
    if limits.medium_high is not None and value >= limits.medium_high:
        return "MEDIUM"
    return None


def watched_sides(limits: Limits) -> list[Side]:
    """A side with no limit at all can never alarm, so it has no alarm code (for example no
    SPO2_HIGH with the default limits)."""
    sides: list[Side] = []
    if limits.high_low is not None or limits.medium_low is not None:
        sides.append("LOW")
    if limits.high_high is not None or limits.medium_high is not None:
        sides.append("HIGH")
    return sides


@dataclass(frozen=True)
class SideState:
    """What the engine remembers about one side between readings."""

    last_at: datetime | None = None
    abnormal_since: datetime | None = None  # run of MEDIUM or HIGH
    high_since: datetime | None = None  # run of HIGH only
    normal_since: datetime | None = None  # run out of the alarm band


@dataclass(frozen=True)
class OpenAlarm:
    priority: Priority
    state: AlarmState
    condition_cleared: bool


@dataclass(frozen=True)
class Change:
    """RAISE and ESCALATE carry a priority; RESOLVE carries the reason AUTO_RESOLVED."""

    kind: Literal["RAISE", "ESCALATE", "CLEARED", "RECURRED", "RESOLVE"]
    at: datetime
    priority: Priority | None = None


@dataclass(frozen=True)
class Outcome:
    state: SideState
    alarm: OpenAlarm | None
    changes: tuple[Change, ...]
    skipped: bool = False  # the reading was not newer than the last one; nothing was decided


def lasted(since: datetime | None, at: datetime, needed: timedelta) -> bool:
    return since is not None and at - since >= needed


def step(
    state: SideState, alarm: OpenAlarm | None, value_band: Priority | None, at: datetime
) -> Outcome:
    """Apply one reading, measured at `at`, that falls in `value_band` on this side."""
    if state.last_at is not None and at <= state.last_at:
        # A late reading: alarms are decided in measured order, and this moment has passed.
        return Outcome(state, alarm, (), skipped=True)
    if state.last_at is not None and at - state.last_at > MAX_GAP:
        state = SideState()

    if value_band is None:
        state = SideState(last_at=at, normal_since=state.normal_since or at)
    else:
        state = SideState(
            last_at=at,
            abnormal_since=state.abnormal_since or at,
            high_since=(state.high_since or at) if value_band == "HIGH" else None,
        )

    changes: list[Change] = []
    if alarm is None:
        if lasted(state.high_since, at, HIGH_DEBOUNCE):
            alarm = OpenAlarm("HIGH", "ACTIVE", False)
            changes.append(Change("RAISE", at, "HIGH"))
        elif lasted(state.abnormal_since, at, MEDIUM_DEBOUNCE):
            alarm = OpenAlarm("MEDIUM", "ACTIVE", False)
            changes.append(Change("RAISE", at, "MEDIUM"))
    elif value_band is not None:
        if alarm.priority == "MEDIUM" and lasted(state.high_since, at, HIGH_DEBOUNCE):
            alarm = OpenAlarm("HIGH", "ACTIVE", False)
            changes.append(Change("ESCALATE", at, "HIGH"))
        elif alarm.condition_cleared:
            alarm = replace(alarm, condition_cleared=False)
            changes.append(Change("RECURRED", at))
    elif lasted(state.normal_since, at, CLEAR_TIME):
        if alarm.state == "ACTIVE" and alarm.priority == "HIGH":
            if not alarm.condition_cleared:
                alarm = replace(alarm, condition_cleared=True)
                changes.append(Change("CLEARED", at))
        else:
            alarm = None
            changes.append(Change("RESOLVE", at))
    return Outcome(state, alarm, tuple(changes))
