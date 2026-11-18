"""A reading belongs to the patient its sensor had when it was measured (REQ-MEA-06, R-ISO-01).

Two beds: patient 1 with monitor SN-PM-0001, patient 2 with monitor SN-PM-0002. At T the pulse
oximeter SN-PO-0001 moves from patient 1 to patient 2. Readings measured before T and sent late
must stay with patient 1, whichever monitor sends them; readings after T belong to patient 2.
"""

import uuid
from datetime import timedelta

import pytest

from tests.ingest.bed import Bed, assign, equipped_bed, reading
from tests.ward import NURSE_W1, Ward

pytestmark = pytest.mark.db
PO = "SN-PO-0001"


@pytest.fixture
def moved(ward: Ward) -> tuple[Bed, Bed]:
    first = equipped_bed(ward, 1)
    second = equipped_bed(ward, 2, sensors=False)
    ward.clock.current += timedelta(minutes=10)  # the move happens at T = start + 10 min
    sensor = first.device_ids[PO]
    response = ward.client(NURSE_W1).delete(f"/api/v1/sessions/{first.session}/devices/{sensor}")
    assert response.status_code == 204
    assign(ward, second.session, sensor)
    ward.clock.current += timedelta(minutes=1)  # the late readings arrive at T + 1 min
    return first, second


def outcome(bed: Bed, seq: int, minutes_from_move: float, ward: Ward) -> str:
    at = ward.clock.now() - timedelta(minutes=1) + timedelta(minutes=minutes_from_move)
    body = bed.send([reading(PO, seq, at)]).json()
    if body["stored"]:
        return "stored"
    return str(body["refused"][0]["code"])


def session_of(ward: Ward, seq: int) -> str:
    return str(ward.scalar("SELECT session_id FROM measurement WHERE seq = :seq", seq=seq))


def test_late_reading_from_before_the_move_through_the_old_monitor_goes_to_patient_1(
    ward: Ward, moved: tuple[Bed, Bed]
) -> None:
    first, _ = moved
    assert outcome(first, 1, -5, ward) == "stored"
    assert session_of(ward, 1) == first.session


def test_late_reading_from_before_the_move_through_the_new_monitor_is_refused(
    ward: Ward, moved: tuple[Bed, Bed]
) -> None:
    """The new monitor was never in patient 1's session, so it may not deliver patient 1's data,
    and the reading must never show for patient 2."""
    _, second = moved
    assert outcome(second, 1, -5, ward) == "NOT_PAIRED"
    assert ward.scalar("SELECT count(*) FROM measurement") == 0


def test_reading_after_the_move_through_the_new_monitor_goes_to_patient_2(
    ward: Ward, moved: tuple[Bed, Bed]
) -> None:
    _, second = moved
    assert outcome(second, 2, 0.5, ward) == "stored"
    assert session_of(ward, 2) == second.session


def test_reading_after_the_move_through_the_old_monitor_is_refused(
    ward: Ward, moved: tuple[Bed, Bed]
) -> None:
    first, _ = moved
    assert outcome(first, 2, 0.5, ward) == "NOT_PAIRED"


def test_the_exact_moment_of_the_move_belongs_to_patient_2(
    ward: Ward, moved: tuple[Bed, Bed]
) -> None:
    first, second = moved
    assert outcome(first, 1, 0, ward) == "NOT_PAIRED"
    assert outcome(second, 1, 0, ward) == "stored"
    assert session_of(ward, 1) == second.session


def test_reading_measured_before_the_sensor_was_assigned_has_no_session(ward: Ward) -> None:
    bed = equipped_bed(ward, 1)
    body = bed.send([reading(PO, 1, ward.clock.now() - timedelta(seconds=1))]).json()
    assert [r["code"] for r in body["refused"]] == ["NO_SESSION"]


def test_after_discharge_only_readings_measured_before_it_are_kept(ward: Ward) -> None:
    """REQ-SES-03 with REQ-CON-03: readings buffered while the backend was down must not be lost,
    so those measured before the discharge are kept in the closed session's history; anything
    measured after it is refused."""
    from pulseguard.measurements.queries import history

    bed = equipped_bed(ward, 1)
    start = ward.clock.now()
    ward.clock.current = start + timedelta(minutes=30)
    assert (
        ward.client(NURSE_W1).post(f"/api/v1/sessions/{bed.session}/discharge").status_code == 200
    )
    ward.clock.current = start + timedelta(minutes=35)
    body = bed.send(
        [
            reading(PO, 1, start + timedelta(minutes=29)),
            reading(PO, 2, start + timedelta(minutes=31)),
        ]
    ).json()
    assert [r["seq"] for r in body["stored"]] == [1]
    assert [(r["seq"], r["code"]) for r in body["refused"]] == [(2, "NO_SESSION")]
    with ward.sessions() as db:
        kept = history(db, uuid.UUID(bed.session), "PR", start, start + timedelta(hours=1))
    assert [m.seq for m in kept] == [1]


def test_late_reading_of_a_sensor_retired_since_is_refused(ward: Ward) -> None:
    """REQ-DEV-04: a retired device's readings are refused, even ones measured while it was in
    use; a device is retired because it is not to be trusted any more."""
    from tests.ward import TECHNICIAN

    bed = equipped_bed(ward, 1)
    measured = ward.clock.now() + timedelta(seconds=5)
    ward.clock.current += timedelta(minutes=1)
    sensor = bed.device_ids[PO]
    client = ward.client(TECHNICIAN)
    assert client.delete(f"/api/v1/sessions/{bed.session}/devices/{sensor}").status_code == 204
    assert client.post(f"/api/v1/devices/{sensor}/retire").status_code == 200
    body = bed.send([reading(PO, 1, measured)]).json()
    assert [r["code"] for r in body["refused"]] == ["DEVICE_RETIRED"]
