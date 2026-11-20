"""Alarms of a session over a time range, and alarm history that always matches the current state
(REQ-VIEW-03, REQ-ALM-03)."""

import random
from datetime import datetime, timedelta

import pytest
from httpx import Response
from sqlalchemy import select

from pulseguard.alarms.history import history_problems
from pulseguard.clock import to_wire
from pulseguard.models import Alarm, AlarmEvent
from tests.ingest.bed import Bed, Pulse, equipped_bed
from tests.ward import NURSE_W1, NURSE_W2, Ward

pytestmark = pytest.mark.db


@pytest.fixture
def bed(ward: Ward) -> Bed:
    equipped = equipped_bed(ward, 1)
    ward.clock.current += timedelta(minutes=30)
    return equipped


def pulse_from(bed: Bed, minutes_after_start: int = 1) -> Pulse:
    pulse = Pulse(bed)
    pulse.start = bed.ward.clock.now() - timedelta(minutes=30 - minutes_after_start)
    return pulse


def alarms_between(bed: Bed, start: datetime, end: datetime, **params: object) -> Response:
    response: Response = bed.ward.client(NURSE_W1).get(
        f"/api/v1/sessions/{bed.session}/alarms",
        params={"from": to_wire(start), "to": to_wire(end)} | params,
    )
    return response


def all_problems(ward: Ward) -> list[str]:
    with ward.sessions() as db:
        problems: list[str] = []
        for alarm in db.scalars(select(Alarm)):
            events = list(
                db.scalars(
                    select(AlarmEvent)
                    .where(AlarmEvent.alarm_id == alarm.id)
                    .order_by(AlarmEvent.id)
                )
            )
            problems += history_problems(alarm, events)
        return problems


def test_an_alarm_is_listed_in_every_range_it_overlaps(bed: Bed, ward: Ward) -> None:
    """A medium alarm raised at 10 s (SpO2 93 from 0 s), resolved at 21 s (97 from 11 s, cleared
    after 10 s of normal)."""
    pulse = pulse_from(bed)
    pulse.send([93] * 11 + [97] * 11, "SPO2")
    started, resolved = pulse.at(10), pulse.at(21)

    def listed(start: datetime, end: datetime) -> int:
        return len(alarms_between(bed, start, end).json()["items"])

    second = timedelta(seconds=1)
    assert listed(started - 5 * second, started) == 0  # ends exactly when it starts
    assert listed(started - 5 * second, started + second) == 1
    assert listed(started + second, resolved - second) == 1  # inside
    assert listed(resolved, resolved + 5 * second) == 1  # starts exactly when it resolves
    assert listed(resolved + second, resolved + 5 * second) == 0
    body = alarms_between(bed, started, resolved).json()["items"][0]
    assert (body["code"], body["state"], body["resolved_at"]) == (
        "SPO2_LOW",
        "RESOLVED",
        to_wire(resolved),
    )


def test_an_open_alarm_is_listed_in_any_later_range(bed: Bed, ward: Ward) -> None:
    pulse = pulse_from(bed)
    pulse.send([140] * 6)
    later = ward.clock.now() + timedelta(days=1)
    assert len(alarms_between(bed, later, later + timedelta(hours=1)).json()["items"]) == 1


def test_alarms_are_paged_by_start_time(bed: Bed, ward: Ward) -> None:
    pulse = pulse_from(bed)
    for _ in range(4):  # four medium alarms, each resolved before the next
        pulse.send([93] * 11 + [97] * 11, "SPO2")
    window = (pulse.at(0), pulse.at(200))
    first = alarms_between(bed, *window, limit=3).json()
    rest = alarms_between(bed, *window, limit=3, cursor=first["next_cursor"]).json()
    starts = [a["started_at"] for a in first["items"] + rest["items"]]
    assert starts == sorted(starts) and len(set(starts)) == 4
    assert rest["next_cursor"] is None


def test_staff_of_another_unit_cannot_read_alarms(bed: Bed, ward: Ward) -> None:
    now = ward.clock.now()
    response = ward.client(NURSE_W2).get(
        f"/api/v1/sessions/{bed.session}/alarms",
        params={"from": to_wire(now - timedelta(hours=1)), "to": to_wire(now)},
    )
    assert response.status_code == 403


def test_history_of_one_alarm_through_every_kind_of_step(bed: Bed, ward: Ward) -> None:
    """Medium, acknowledged, escalated (active again), cleared and latched, back again, cleared
    again, acknowledged. The seeded scenarios below never happened to hit "back again"."""
    pulse = pulse_from(bed)
    pulse.send([120] * 11)
    alarm = str(ward.scalar("SELECT id FROM alarm"))
    assert ward.client(NURSE_W1).post(f"/api/v1/alarms/{alarm}/acknowledge").status_code == 200
    pulse.send([140] * 6 + [80] * 11 + [140] + [80] * 11)
    assert ward.client(NURSE_W1).post(f"/api/v1/alarms/{alarm}/acknowledge").status_code == 200
    history = ward.client(NURSE_W1).get(f"/api/v1/alarms/{alarm}").json()["history"]
    assert [h["reason"] for h in history][3:6] == [
        "Condition cleared; waits for acknowledgement",
        "Condition is back",
        "Condition cleared; waits for acknowledgement",
    ]
    assert [(h["from_state"], h["to_state"], h["priority"]) for h in history] == [
        (None, "ACTIVE", "MEDIUM"),
        ("ACTIVE", "ACKNOWLEDGED", "MEDIUM"),
        ("ACKNOWLEDGED", "ACTIVE", "HIGH"),
        ("ACTIVE", "ACTIVE", "HIGH"),
        ("ACTIVE", "ACTIVE", "HIGH"),
        ("ACTIVE", "ACTIVE", "HIGH"),
        ("ACTIVE", "RESOLVED", "HIGH"),
    ]
    assert all_problems(ward) == []


@pytest.mark.parametrize("seed", range(8))
def test_history_always_leads_to_the_current_state(ward: Ward, seed: int) -> None:
    """Seeded random pulse streams through every band, with acknowledgements at random moments
    and sometimes a discharge. Afterwards, every alarm's history must be a chain of allowed steps
    that ends in its current state (alarms/history.py, history_problems)."""
    rng = random.Random(seed)
    bed = equipped_bed(ward, 1)
    ward.clock.current += timedelta(minutes=30)
    pulse = pulse_from(bed)
    bands = [80, 80, 80, 115, 125, 135, 150, 35]  # normal more often than not
    for _ in range(12):
        value = rng.choice(bands)
        pulse.send([value + rng.uniform(-2, 2) for _ in range(rng.randint(1, 15))])
        if rng.random() < 0.4:
            active = ward.scalar("SELECT array_agg(id) FROM alarm WHERE state = 'ACTIVE'")
            for alarm_id in active if isinstance(active, list) else []:
                ward.client(NURSE_W1).post(f"/api/v1/alarms/{alarm_id}/acknowledge")
    if rng.random() < 0.5:
        assert (
            ward.client(NURSE_W1).post(f"/api/v1/sessions/{bed.session}/discharge").status_code
            == 200
        )
        assert ward.scalar("SELECT count(*) FROM alarm WHERE state <> 'RESOLVED'") == 0
    raised = ward.scalar("SELECT count(*) FROM alarm")
    assert isinstance(raised, int) and raised > 0, "the scenario raised no alarm at all"
    assert all_problems(ward) == []
