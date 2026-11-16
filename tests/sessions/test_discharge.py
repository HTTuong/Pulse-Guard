"""Discharge (REQ-SES-03)."""

import uuid
from datetime import timedelta

import pytest
from httpx import Response

from pulseguard.access.actor import Actor
from tests.ward import NURSE_W1, NURSE_W2, PHYSICIAN_W1, Ward, bed

pytestmark = pytest.mark.db


def admitted(ward: Ward, number: int = 1, bed_number: int = 1) -> str:
    response = ward.client(NURSE_W1).post(
        "/api/v1/sessions",
        json={"patient_id": ward.new_patient(number), "bed_id": str(bed(1, bed_number))},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def discharge(ward: Ward, session_id: str, actor: Actor = NURSE_W1) -> Response:
    response: Response = ward.client(actor).post(f"/api/v1/sessions/{session_id}/discharge")
    return response


def test_discharge_closes_the_session_and_frees_the_bed(ward: Ward) -> None:
    session_id = admitted(ward)
    ward.clock.current += timedelta(hours=3)
    response = discharge(ward, session_id)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CLOSED"
    assert body["bed_id"] is None
    assert body["discharged_at"] == "2026-11-12T21:52:03.000Z"
    discharged_by = ward.scalar(
        "SELECT discharged_by FROM monitoring_session WHERE id = :id", id=session_id
    )
    assert discharged_by == NURSE_W1.user_id
    assert ward.scalar("SELECT count(*) FROM bed_stay WHERE to_at IS NULL") == 0


def test_freed_bed_and_discharged_patient_can_be_admitted_again(ward: Ward) -> None:
    session_id = admitted(ward)
    ward.clock.current += timedelta(minutes=1)
    assert discharge(ward, session_id).status_code == 200
    patient = ward.scalar("SELECT patient_id FROM monitoring_session WHERE id = :id", id=session_id)
    response = ward.client(NURSE_W1).post(
        "/api/v1/sessions", json={"patient_id": str(patient), "bed_id": str(bed(1, 1))}
    )
    assert response.status_code == 201
    assert response.json()["id"] != session_id


def test_discharge_twice_is_409(ward: Ward) -> None:
    session_id = admitted(ward)
    ward.clock.current += timedelta(minutes=1)
    assert discharge(ward, session_id).status_code == 200
    response = discharge(ward, session_id)
    assert response.status_code == 409
    assert response.json()["code"] == "SESSION_CLOSED"


@pytest.mark.parametrize("actor", [PHYSICIAN_W1, NURSE_W2])
def test_only_a_nurse_of_the_unit_discharges(ward: Ward, actor: Actor) -> None:
    session_id = admitted(ward)
    assert discharge(ward, session_id, actor).status_code == 403
    status = ward.scalar("SELECT status FROM monitoring_session WHERE id = :id", id=session_id)
    assert status == "OPEN"


def test_discharge_unknown_session_is_404(ward: Ward) -> None:
    assert discharge(ward, str(uuid.uuid4())).status_code == 404


def test_discharge_at_the_same_instant_as_admission(ward: Ward) -> None:
    """An admission made by mistake and undone at once; the clock has not moved."""
    session_id = admitted(ward)
    response = discharge(ward, session_id)
    assert response.status_code == 200, response.text


def test_discharge_when_the_wall_clock_went_back(ward: Ward) -> None:
    """The server clock is set back after an admission (for example by NTP)."""
    session_id = admitted(ward)
    ward.clock.current -= timedelta(seconds=30)
    response = discharge(ward, session_id)
    assert response.status_code == 200, response.text
    # Recorded as the admission time, never before it.
    assert response.json()["discharged_at"] == response.json()["admitted_at"]
