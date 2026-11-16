"""Admission (REQ-SES-01, REQ-SES-02) and reading a session."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from httpx import Response

from pulseguard.access.actor import Actor
from tests.sessions.conftest import NURSE_W1, NURSE_W2, PHYSICIAN_W1, TECHNICIAN, W1, Ward, bed

pytestmark = pytest.mark.db


def admit(ward: Ward, patient_id: str, bed_id: uuid.UUID, actor: Actor = NURSE_W1) -> Response:
    response: Response = ward.client(actor).post(
        "/api/v1/sessions", json={"patient_id": patient_id, "bed_id": str(bed_id)}
    )
    return response


def test_nurse_admits_a_patient_to_a_free_bed(ward: Ward) -> None:
    patient = ward.new_patient(1)
    response = admit(ward, patient, bed(1, 1))
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "OPEN"
    assert body["patient"]["id"] == patient
    assert body["bed_id"] == str(bed(1, 1))
    assert body["unit_id"] == str(W1)
    assert body["admitted_at"] == "2026-11-12T18:52:03.000Z"
    assert body["discharged_at"] is None
    admitted_by = ward.scalar(
        "SELECT admitted_by FROM monitoring_session WHERE id = :id", id=body["id"]
    )
    assert admitted_by == NURSE_W1.user_id


def test_unknown_patient_is_404(ward: Ward) -> None:
    response = admit(ward, str(uuid.uuid4()), bed(1, 1))
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_unknown_bed_is_404(ward: Ward) -> None:
    response = admit(ward, ward.new_patient(1), uuid.uuid4())
    assert response.status_code == 404


def test_malformed_id_is_400(ward: Ward) -> None:
    response = ward.client(NURSE_W1).post(
        "/api/v1/sessions", json={"patient_id": "not-a-uuid", "bed_id": str(bed(1, 1))}
    )
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "patient_id"


def test_bed_of_another_unit_is_403_and_nothing_is_created(ward: Ward) -> None:
    response = admit(ward, ward.new_patient(1), bed(2, 1), actor=NURSE_W1)
    assert response.status_code == 403
    assert ward.scalar("SELECT count(*) FROM monitoring_session") == 0


@pytest.mark.parametrize("actor", [PHYSICIAN_W1, TECHNICIAN])
def test_only_nurses_admit(ward: Ward, actor: Actor) -> None:
    assert admit(ward, ward.new_patient(1), bed(1, 1), actor=actor).status_code == 403


def test_404_comes_before_403(ward: Ward) -> None:
    """Unknown patient and a bed of another unit: existence is checked first (docs/api.md)."""
    assert admit(ward, str(uuid.uuid4()), bed(2, 1), actor=NURSE_W1).status_code == 404


def test_second_open_session_of_a_patient_is_409(ward: Ward) -> None:
    patient = ward.new_patient(1)
    assert admit(ward, patient, bed(1, 1)).status_code == 201
    response = admit(ward, patient, bed(1, 2))
    assert response.status_code == 409
    assert response.json()["code"] == "PATIENT_HAS_OPEN_SESSION"
    assert ward.scalar("SELECT count(*) FROM monitoring_session") == 1


def test_occupied_bed_is_409(ward: Ward) -> None:
    assert admit(ward, ward.new_patient(1), bed(1, 1)).status_code == 201
    response = admit(ward, ward.new_patient(2), bed(1, 1))
    assert response.status_code == 409
    assert response.json()["code"] == "BED_OCCUPIED"
    assert ward.scalar("SELECT count(*) FROM monitoring_session") == 1


def test_parallel_admissions_of_one_patient_open_one_session(ward: Ward) -> None:
    """R-ISO-02: 10 nurses admit the same patient to 10 beds at the same moment."""
    patient = ward.new_patient(1)
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(lambda n: admit(ward, patient, bed(1, n)), range(1, 11)))
    statuses = [r.status_code for r in responses]
    assert sorted(statuses) == [201] + [409] * 9
    assert ward.scalar("SELECT count(*) FROM monitoring_session WHERE status = 'OPEN'") == 1
    assert ward.scalar("SELECT count(*) FROM bed_stay") == 1


def test_parallel_admissions_to_one_bed_fill_it_once(ward: Ward) -> None:
    """R-ISO-02: 10 patients admitted to the same bed at the same moment."""
    patients = [ward.new_patient(n) for n in range(1, 11)]
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(lambda p: admit(ward, p, bed(1, 1)), patients))
    assert sorted(r.status_code for r in responses) == [201] + [409] * 9
    assert {r.json()["code"] for r in responses if r.status_code == 409} == {"BED_OCCUPIED"}
    assert ward.scalar("SELECT count(*) FROM monitoring_session") == 1


def test_get_session_for_staff_of_its_unit(ward: Ward) -> None:
    session_id = admit(ward, ward.new_patient(1), bed(1, 1)).json()["id"]
    for actor in (NURSE_W1, PHYSICIAN_W1):
        response = ward.client(actor).get(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 200
        assert response.json()["id"] == session_id


@pytest.mark.parametrize("actor", [NURSE_W2, TECHNICIAN])
def test_get_session_of_another_unit_or_role_is_403(ward: Ward, actor: Actor) -> None:
    session_id = admit(ward, ward.new_patient(1), bed(1, 1)).json()["id"]
    assert ward.client(actor).get(f"/api/v1/sessions/{session_id}").status_code == 403


def test_get_unknown_session_is_404(ward: Ward) -> None:
    assert ward.client(NURSE_W1).get(f"/api/v1/sessions/{uuid.uuid4()}").status_code == 404
