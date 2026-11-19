"""Alarms raised from real ingested readings, acknowledged by staff, resolved on discharge
(REQ-ALM-01 to REQ-ALM-04, REQ-ALM-08, R-REC-04)."""

import random
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from pulseguard.access.actor import Actor, current_actor
from pulseguard.api.app import create_app
from pulseguard.db import make_engine
from tests.ingest.bed import Bed, equipped_bed, reading
from tests.ward import NURSE_W1, NURSE_W2, PHYSICIAN_W1, TECHNICIAN, Ward

pytestmark = pytest.mark.db
PO, RR = "SN-PO-0001", "SN-RR-0001"


@pytest.fixture
def bed(ward: Ward) -> Bed:
    equipped = equipped_bed(ward, 1)
    ward.clock.current += timedelta(minutes=1)
    return equipped


class Pulse:
    """Pulse readings one per second from the ward clock, with their own seq numbers."""

    def __init__(self, bed: Bed) -> None:
        self.bed, self.seq = bed, 0
        self.start = bed.ward.clock.now() - timedelta(seconds=40)
        self.second = 0

    def readings(self, values: Sequence[float], parameter: str = "PR") -> list[dict[str, object]]:
        batch = []
        for value in values:
            self.seq += 1
            at = self.start + timedelta(seconds=self.second)
            batch.append(reading(PO, self.seq, at, parameter, value))
            self.second += 1
        return batch

    def send(self, values: Sequence[float], parameter: str = "PR") -> None:
        response = self.bed.send(self.readings(values, parameter))
        assert response.status_code == 200, response.text
        assert len(response.json()["stored"]) == len(values), response.json()

    def at(self, second: int) -> datetime:
        return self.start + timedelta(seconds=second)


def alarms(ward: Ward) -> list[tuple[object, ...]]:
    with ward.engine.connect() as connection:
        from sqlalchemy import text

        rows = connection.execute(
            text(
                "SELECT code, priority, state, condition_cleared, resolution, started_at "
                "FROM alarm ORDER BY started_at, code"
            )
        )
        return [tuple(row) for row in rows]


def only_alarm_id(ward: Ward) -> str:
    return str(ward.scalar("SELECT id FROM alarm"))


def acknowledge(ward: Ward, alarm_id: str, actor: Actor = NURSE_W1) -> int:
    status: int = ward.client(actor).post(f"/api/v1/alarms/{alarm_id}/acknowledge").status_code
    return status


def test_admission_writes_the_default_limits(ward: Ward) -> None:
    session = ward.open_session(1, 1)
    response = ward.client(PHYSICIAN_W1).get(f"/api/v1/sessions/{session}/limits")
    assert response.status_code == 200
    limits = {
        row["parameter"]: (row["high_low"], row["medium_low"], row["medium_high"], row["high_high"])
        for row in response.json()
    }
    assert limits == {
        "PR": (40, None, 111, 131),
        "RR": (8, None, 21, 25),
        "SPO2": (91, 93, None, None),
    }


def test_pulse_140_for_6_s_raises_one_high_alarm(bed: Bed, ward: Ward) -> None:
    pulse = Pulse(bed)
    pulse.send([140] * 7)
    assert alarms(ward) == [("PR_HIGH", "HIGH", "ACTIVE", False, None, pulse.at(5))]


def test_pulse_140_for_4_s_raises_nothing(bed: Bed, ward: Ward) -> None:
    Pulse(bed).send([140] * 5)
    assert alarms(ward) == []


def test_one_reading_per_batch_gives_the_same_result(bed: Bed, ward: Ward) -> None:
    """The engine state lives in the database between requests."""
    pulse = Pulse(bed)
    for _ in range(7):
        pulse.send([140])
    assert alarms(ward) == [("PR_HIGH", "HIGH", "ACTIVE", False, None, pulse.at(5))]


def test_a_backend_restart_in_the_middle_of_a_debounce_loses_nothing(
    bed: Bed, ward: Ward, migrated_database_url: str
) -> None:
    """R-REC-04: 3 s of pulse 140, a new backend process, then 3 s more: the alarm comes at 5 s."""
    pulse = Pulse(bed)
    pulse.send([140] * 3)
    ward.engine.dispose()
    restarted = create_app(engine=make_engine(migrated_database_url, 2), clock=ward.clock)
    restarted.dependency_overrides[current_actor] = lambda: NURSE_W1
    later = pulse.readings([140] * 3)
    response = TestClient(restarted).post(
        "/api/v1/ingest/batches",
        json={
            "monitor": bed.monitor_serial,
            "sent_at": "2026-11-12T18:53:03.000Z",
            "readings": later,
        },
        headers={"Authorization": f"Bearer {bed.credential}"},
    )
    assert response.status_code == 200, response.text
    assert alarms(ward) == [("PR_HIGH", "HIGH", "ACTIVE", False, None, pulse.at(5))]


def test_a_resent_batch_does_not_run_the_engine_twice(bed: Bed, ward: Ward) -> None:
    pulse = Pulse(bed)
    batch = pulse.readings([140] * 7)
    for _ in range(3):
        assert bed.send(batch).status_code == 200
    assert len(alarms(ward)) == 1
    assert ward.scalar("SELECT count(*) FROM alarm_event") == 1


def test_readings_shuffled_in_a_batch_give_the_same_alarm(bed: Bed, ward: Ward) -> None:
    pulse = Pulse(bed)
    batch = pulse.readings([140] * 7)
    random.Random(243).shuffle(batch)  # noqa: S311 - a fixed order, not a secret
    assert bed.send(batch).status_code == 200
    assert alarms(ward) == [("PR_HIGH", "HIGH", "ACTIVE", False, None, pulse.at(5))]


def test_a_late_batch_older_than_what_the_engine_saw_changes_nothing(bed: Bed, ward: Ward) -> None:
    """R-ORD-02: normal readings for 10..15 s arrive first, then 140 for 0..9 s. The late ones are
    stored for the history but are not run through the engine, which has moved on."""
    pulse = Pulse(bed)
    early = pulse.readings([140] * 10)
    normal = pulse.readings([80] * 6)
    assert bed.send(normal).status_code == 200
    assert len(bed.send(early).json()["stored"]) == 10
    assert alarms(ward) == []


def test_high_alarm_latches_until_acknowledged(bed: Bed, ward: Ward) -> None:
    pulse = Pulse(bed)
    pulse.send([140] * 6 + [80] * 11)
    assert alarms(ward) == [("PR_HIGH", "HIGH", "ACTIVE", True, None, pulse.at(5))]
    alarm = only_alarm_id(ward)
    assert acknowledge(ward, alarm) == 200
    assert alarms(ward)[0][2:5] == ("RESOLVED", True, "ACKNOWLEDGED_AFTER_CLEAR")
    history = ward.client(NURSE_W1).get(f"/api/v1/alarms/{alarm}").json()["history"]
    assert [(h["from_state"], h["to_state"], h["by_user"]) for h in history] == [
        (None, "ACTIVE", None),
        ("ACTIVE", "ACTIVE", None),
        ("ACTIVE", "RESOLVED", str(NURSE_W1.user_id)),
    ]


def test_acknowledged_alarm_resolves_when_the_condition_clears(bed: Bed, ward: Ward) -> None:
    pulse = Pulse(bed)
    pulse.send([140] * 6)
    alarm = only_alarm_id(ward)
    assert acknowledge(ward, alarm, PHYSICIAN_W1) == 200
    assert alarms(ward)[0][2] == "ACKNOWLEDGED"
    assert acknowledge(ward, alarm) == 409
    pulse.send([80] * 11)
    assert alarms(ward)[0][2:5] == ("RESOLVED", False, "AUTO_RESOLVED")


def test_medium_alarm_resolves_by_itself(bed: Bed, ward: Ward) -> None:
    pulse = Pulse(bed)
    pulse.send([93] * 11, "SPO2")
    assert alarms(ward)[0][:3] == ("SPO2_LOW", "MEDIUM", "ACTIVE")
    pulse.send([97] * 11, "SPO2")
    assert alarms(ward)[0][2:5] == ("RESOLVED", False, "AUTO_RESOLVED")


def test_two_parameters_alarm_on_their_own(bed: Bed, ward: Ward) -> None:
    pulse = Pulse(bed)
    batch = []
    for second in range(6):
        batch += [
            reading(PO, 2 * second + 1, pulse.at(second), "PR", 140),
            reading(PO, 2 * second + 2, pulse.at(second), "SPO2", 88),
        ]
    batch += [reading(RR, n + 1, pulse.at(5 * n), "RR", 30) for n in range(2)]
    assert bed.send(batch).status_code == 200
    assert sorted((a[0], a[1]) for a in alarms(ward)) == [
        ("PR_HIGH", "HIGH"),
        ("RR_HIGH", "HIGH"),
        ("SPO2_LOW", "HIGH"),
    ]


def test_ten_nurses_acknowledge_at_once(bed: Bed, ward: Ward) -> None:
    """One 200, nine 409, one acknowledgement in the history. A lost race is not lost every time
    (findings.md), so three alarms are raised and acknowledged in turn."""
    pulse = Pulse(bed)
    for round_number in range(3):
        pulse.send([140] * 6)
        alarm = str(ward.scalar("SELECT id FROM alarm WHERE state = 'ACTIVE'"))
        with ThreadPoolExecutor(max_workers=10) as pool:
            statuses = sorted(pool.map(lambda _, a=alarm: acknowledge(ward, a), range(10)))
        assert statuses == [200] + [409] * 9, round_number
        pulse.send([80] * 11)  # resolves the acknowledged alarm, so the next round raises anew
    assert ward.scalar("SELECT count(*) FROM alarm_event WHERE by_user IS NOT NULL") == 3


@pytest.mark.parametrize(("actor", "status"), [(NURSE_W2, 403), (TECHNICIAN, 403)])
def test_only_staff_of_the_unit_acknowledge(
    bed: Bed, ward: Ward, actor: Actor, status: int
) -> None:
    Pulse(bed).send([140] * 6)
    assert acknowledge(ward, only_alarm_id(ward), actor) == status
    assert alarms(ward)[0][2] == "ACTIVE"


def test_unknown_alarm_is_404(ward: Ward) -> None:
    assert acknowledge(ward, "00000000-0000-0000-0000-000000000001") == 404


def test_discharge_resolves_open_alarms_and_later_readings_raise_none(bed: Bed, ward: Ward) -> None:
    pulse = Pulse(bed)
    pulse.send([140] * 6)
    assert (
        ward.client(NURSE_W1).post(f"/api/v1/sessions/{bed.session}/discharge").status_code == 200
    )
    assert alarms(ward)[0][2:5] == ("RESOLVED", False, "DISCHARGED")
    # buffered readings measured before the discharge are kept (F-03) but never alarm again
    pulse.send([80] * 3 + [88] * 6, "SPO2")
    assert len(alarms(ward)) == 1
