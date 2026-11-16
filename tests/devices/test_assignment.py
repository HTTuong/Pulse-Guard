"""Assigning devices to sessions (REQ-DEV-02, REQ-DEV-03, REQ-SES-03) and pairing (REQ-DEV-05)."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from httpx import Response

from pulseguard.access.actor import Actor
from pulseguard.devices.assignments import pairings_for_monitor, session_of_device_at
from tests.ward import NURSE_W1, NURSE_W2, PHYSICIAN_W1, TECHNICIAN, Ward

pytestmark = pytest.mark.db


def assign(ward: Ward, session: str, device: str, actor: Actor = NURSE_W1) -> Response:
    response: Response = ward.client(actor).post(
        f"/api/v1/sessions/{session}/devices", json={"device_id": device}
    )
    return response


def unassign(ward: Ward, session: str, device: str, actor: Actor = NURSE_W1) -> Response:
    response: Response = ward.client(actor).delete(f"/api/v1/sessions/{session}/devices/{device}")
    return response


def test_nurse_assigns_a_device_and_the_session_lists_it(ward: Ward) -> None:
    session = ward.open_session(1, 1)
    device = ward.new_device("SN-PO-0001", "PULSE_OXIMETER")
    response = assign(ward, session, device)
    assert response.status_code == 201
    assert response.json()["assigned_at"] == "2026-11-12T18:52:03.000Z"
    listed = ward.client(NURSE_W1).get(f"/api/v1/sessions/{session}").json()["devices"]
    assert [d["serial"] for d in listed] == ["SN-PO-0001"]


def test_a_session_takes_one_device_of_each_kind(ward: Ward) -> None:
    session = ward.open_session(1, 1)
    for serial, kind in [("SN-PM-0001", "PATIENT_MONITOR"), ("SN-PO-0001", "PULSE_OXIMETER"),
                         ("SN-RR-0001", "RESPIRATION")]:  # fmt: skip
        assert assign(ward, session, ward.new_device(serial, kind)).status_code == 201
    second = ward.new_device("SN-PO-0002", "PULSE_OXIMETER")
    response = assign(ward, session, second)
    assert response.status_code == 409
    assert response.json()["code"] == "SESSION_HAS_DEVICE_KIND"


def test_assign_to_a_closed_session_is_409(ward: Ward) -> None:
    session = ward.open_session(1, 1)
    ward.clock.current += timedelta(minutes=1)
    assert ward.client(NURSE_W1).post(f"/api/v1/sessions/{session}/discharge").status_code == 200
    response = assign(ward, session, ward.new_device("SN-PO-0001", "PULSE_OXIMETER"))
    assert response.status_code == 409
    assert response.json()["code"] == "SESSION_CLOSED"


@pytest.mark.parametrize(("actor", "status"), [(NURSE_W2, 403), (PHYSICIAN_W1, 403),
                                               (TECHNICIAN, 201)])  # fmt: skip
def test_who_may_assign(ward: Ward, actor: Actor, status: int) -> None:
    session = ward.open_session(1, 1)
    device = ward.new_device("SN-PO-0001", "PULSE_OXIMETER")
    assert assign(ward, session, device, actor).status_code == status


def test_unknown_device_or_session_is_404(ward: Ward) -> None:
    session = ward.open_session(1, 1)
    assert assign(ward, session, str(uuid.uuid4())).status_code == 404
    device = ward.new_device("SN-PO-0001", "PULSE_OXIMETER")
    assert assign(ward, str(uuid.uuid4()), device).status_code == 404


def test_device_of_another_session_cannot_be_unassigned_through_this_one(ward: Ward) -> None:
    first = ward.open_session(1, 1)
    second = ward.open_session(2, 2)
    device = ward.new_device("SN-PO-0001", "PULSE_OXIMETER")
    assert assign(ward, first, device).status_code == 201
    assert unassign(ward, second, device).status_code == 404
    assert ward.scalar("SELECT status FROM device") == "ASSIGNED"


def test_discharge_unassigns_every_device(ward: Ward) -> None:
    session = ward.open_session(1, 1)
    devices = [ward.new_device("SN-PM-0001", "PATIENT_MONITOR"),
               ward.new_device("SN-PO-0001", "PULSE_OXIMETER")]  # fmt: skip
    for device in devices:
        assert assign(ward, session, device).status_code == 201
    ward.clock.current += timedelta(hours=2)
    assert ward.client(NURSE_W1).post(f"/api/v1/sessions/{session}/discharge").status_code == 200
    assert ward.scalar("SELECT count(*) FROM device WHERE status = 'AVAILABLE'") == 2
    assert ward.scalar("SELECT count(*) FROM device_assignment WHERE unassigned_at IS NULL") == 0
    assert ward.client(NURSE_W1).get(f"/api/v1/sessions/{session}").json()["devices"] == []


def test_one_device_assigned_in_parallel_to_ten_sessions(ward: Ward) -> None:
    """R-ISO-02: one sensor, ten nurses, ten patients, the same moment."""
    sessions = [ward.open_session(n, n) for n in range(1, 11)]
    device = ward.new_device("SN-PO-0001", "PULSE_OXIMETER")
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(lambda s: assign(ward, s, device), sessions))
    assert sorted(r.status_code for r in responses) == [201] + [409] * 9
    assert ward.scalar("SELECT count(*) FROM device_assignment") == 1


def test_ten_sensors_of_one_kind_assigned_in_parallel_to_one_session(ward: Ward) -> None:
    """R-ISO-02: the one-per-kind rule lives in code, so it depends on the session row lock.
    A race is not lost every time, so the scenario runs on 5 sessions: without the lock, one run
    in 5 passed by luck in d241; five rounds make a lucky pass rare."""
    for round_number in range(1, 6):
        session = ward.open_session(round_number, round_number)
        devices = [
            ward.new_device(f"SN-PO-{round_number}{n:03d}", "PULSE_OXIMETER") for n in range(10)
        ]
        with ThreadPoolExecutor(max_workers=10) as pool:
            responses = list(pool.map(lambda d, s=session: assign(ward, s, d), devices))
        assert sorted(r.status_code for r in responses) == [201] + [409] * 9, round_number
    assert ward.scalar("SELECT count(*) FROM device_assignment") == 5


def test_sensor_moved_to_another_patient_keeps_its_history(ward: Ward) -> None:
    """REQ-MEA-06: a reading belongs to the session the sensor had when it was measured."""
    first = ward.open_session(1, 1)
    second = ward.open_session(2, 2)
    device = ward.new_device("SN-PO-0001", "PULSE_OXIMETER")
    start = ward.clock.current
    assert assign(ward, first, device).status_code == 201
    ward.clock.current = start + timedelta(minutes=10)
    assert unassign(ward, first, device).status_code == 204
    assert assign(ward, second, device).status_code == 201

    device_id = uuid.UUID(device)
    with ward.sessions() as db:
        assert session_of_device_at(db, device_id, start - timedelta(seconds=1)) is None
        assert session_of_device_at(db, device_id, start) == uuid.UUID(first)
        before_move = start + timedelta(minutes=10) - timedelta(milliseconds=1)
        assert session_of_device_at(db, device_id, before_move) == uuid.UUID(first)
        assert session_of_device_at(db, device_id, start + timedelta(minutes=10)) == uuid.UUID(
            second
        )


def test_pairings_of_a_monitor(ward: Ward) -> None:
    """REQ-DEV-05: a monitor accepts the sensors of its own session only."""
    first = ward.open_session(1, 1)
    second = ward.open_session(2, 2)
    monitor = ward.new_device("SN-PM-0001", "PATIENT_MONITOR")
    oximeter = ward.new_device("SN-PO-0001", "PULSE_OXIMETER")
    breathing = ward.new_device("SN-RR-0001", "RESPIRATION")
    other = ward.new_device("SN-PO-0002", "PULSE_OXIMETER")
    for session, device in [
        (first, monitor),
        (first, oximeter),
        (first, breathing),
        (second, other),
    ]:
        assert assign(ward, session, device).status_code == 201
    asked_at = ward.clock.current
    ward.clock.current += timedelta(minutes=5)
    assert unassign(ward, first, breathing).status_code == 204

    with ward.sessions() as db:
        pairings = pairings_for_monitor(db, uuid.UUID(monitor), since=asked_at)
    assert pairings.monitor_serial == "SN-PM-0001"
    assert pairings.paired == ["SN-PO-0001"]
    assert pairings.unpaired == [("SN-RR-0001", ward.clock.current)]


def test_a_monitor_without_a_session_has_no_pairings(ward: Ward) -> None:
    monitor = ward.new_device("SN-PM-0001", "PATIENT_MONITOR")
    with ward.sessions() as db:
        pairings = pairings_for_monitor(db, uuid.UUID(monitor), since=ward.clock.current)
    assert (pairings.paired, pairings.unpaired) == ([], [])


def test_unassign_at_the_same_instant_as_assign(ward: Ward) -> None:
    """A sensor assigned by mistake and taken off at once; the clock has not moved (as F-01)."""
    session = ward.open_session(1, 1)
    device = ward.new_device("SN-PO-0001", "PULSE_OXIMETER")
    assert assign(ward, session, device).status_code == 201
    assert unassign(ward, session, device).status_code == 204


def test_unassign_when_the_wall_clock_went_back(ward: Ward) -> None:
    session = ward.open_session(1, 1)
    device = ward.new_device("SN-PO-0001", "PULSE_OXIMETER")
    assert assign(ward, session, device).status_code == 201
    ward.clock.current -= timedelta(seconds=30)
    assert unassign(ward, session, device).status_code == 204
