"""The history rule itself, on alarms built by hand (no database): a checker that never complains
would make every scenario test green, so each kind of broken history must be named."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from pulseguard.alarms.history import history_problems
from pulseguard.models import Alarm, AlarmEvent

T0 = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
NURSE = uuid.UUID("30000000-0000-0000-0000-000000000001")


def alarm(state: str = "RESOLVED", priority: str = "HIGH", **fields: object) -> Alarm:
    values = {
        "id": uuid.UUID(int=1),
        "state": state,
        "priority": priority,
        "started_at": T0,
        "resolved_at": T0 + timedelta(seconds=30) if state == "RESOLVED" else None,
        "acknowledged_at": T0 + timedelta(seconds=20),
    } | fields
    return Alarm(**values)


def event(
    second: int,
    from_state: str | None,
    to_state: str,
    priority: str = "HIGH",
    by: uuid.UUID | None = None,
) -> AlarmEvent:
    return AlarmEvent(
        at=T0 + timedelta(seconds=second),
        from_state=from_state,
        to_state=to_state,
        priority=priority,
        by_user=by,
        reason="test",
    )


GOOD = [
    event(0, None, "ACTIVE"),
    event(20, "ACTIVE", "ACKNOWLEDGED", by=NURSE),
    event(30, "ACKNOWLEDGED", "RESOLVED"),
]


def test_a_good_history_has_no_problems() -> None:
    assert history_problems(alarm(), GOOD) == []


@pytest.mark.parametrize(
    ("the_alarm", "events", "problem"),
    [
        (alarm(), [], "no history"),
        (alarm(), [GOOD[0], GOOD[2]], "step from ACKNOWLEDGED after ACTIVE"),
        (alarm(), [GOOD[0], GOOD[1], event(30, "ACKNOWLEDGED", "ACKNOWLEDGED")], "not allowed"),
        (
            alarm(),
            [
                event(0, None, "ACTIVE"),
                event(20, "ACTIVE", "ACKNOWLEDGED", by=NURSE),
                event(30, "ACKNOWLEDGED", "RESOLVED"),
                event(40, "RESOLVED", "ACTIVE"),
            ],
            "not allowed",
        ),
        (alarm(state="ACKNOWLEDGED"), GOOD, "history ends RESOLVED, alarm is ACKNOWLEDGED"),
        (alarm(priority="MEDIUM"), GOOD, "history ends HIGH, alarm is MEDIUM"),
        (alarm(started_at=T0 + timedelta(seconds=1)), GOOD, "history starts"),
        (alarm(resolved_at=T0 + timedelta(seconds=31)), GOOD, "resolved at"),
        (alarm(acknowledged_at=T0 + timedelta(seconds=21)), GOOD, "not in the history"),
        (alarm(), [GOOD[0], event(20, "ACTIVE", "ACKNOWLEDGED"), GOOD[2]], "not in the history"),
    ],
)
def test_each_kind_of_broken_history_is_named(
    the_alarm: Alarm, events: list[AlarmEvent], problem: str
) -> None:
    problems = history_problems(the_alarm, events)
    assert any(problem in p for p in problems), problems
