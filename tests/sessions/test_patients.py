"""Registering a patient: validation and error format."""

import pytest

from tests.sessions.conftest import NURSE_W1, PHYSICIAN_W1, Ward

pytestmark = pytest.mark.db

VALID = {"mrn": "TEST-0001", "given_name": "Aino", "family_name": "Example", "birth_year": 1950}


def test_nurse_registers_a_patient(ward: Ward) -> None:
    response = ward.client(NURSE_W1).post("/api/v1/patients", json=VALID)
    assert response.status_code == 201
    body = response.json()
    assert {k: body[k] for k in VALID} == VALID
    assert ward.scalar("SELECT count(*) FROM patient") == 1


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"mrn": "1234567"}, "mrn"),  # a real looking record number is refused (REQ-NFR-04)
        ({"mrn": "TEST-123"}, "mrn"),  # 3 digits, one short of the minimum
        ({"given_name": ""}, "given_name"),
        ({"family_name": "x" * 101}, "family_name"),
        ({"birth_year": 1899}, "birth_year"),
        ({"birth_year": 2101}, "birth_year"),
        ({"ward": "W1"}, "ward"),  # unknown fields are refused, not ignored
    ],
)
def test_invalid_patient_is_400_with_the_field(
    ward: Ward, change: dict[str, object], field: str
) -> None:
    response = ward.client(NURSE_W1).post("/api/v1/patients", json=VALID | change)
    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert [e["field"] for e in body["errors"]] == [field]
    assert ward.scalar("SELECT count(*) FROM patient") == 0


@pytest.mark.parametrize("edge", [{"mrn": "TEST-0000"}, {"mrn": "TEST-9999999999"},
                                  {"birth_year": 1900}, {"birth_year": 2100},
                                  {"given_name": "x" * 100}])  # fmt: skip
def test_edges_of_valid_input_are_accepted(ward: Ward, edge: dict[str, object]) -> None:
    assert ward.client(NURSE_W1).post("/api/v1/patients", json=VALID | edge).status_code == 201


def test_missing_field_is_400(ward: Ward) -> None:
    body = {k: v for k, v in VALID.items() if k != "family_name"}
    response = ward.client(NURSE_W1).post("/api/v1/patients", json=body)
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "family_name"


def test_same_record_number_twice_is_409(ward: Ward) -> None:
    client = ward.client(NURSE_W1)
    assert client.post("/api/v1/patients", json=VALID).status_code == 201
    response = client.post("/api/v1/patients", json=VALID)
    assert response.status_code == 409
    assert response.json()["code"] == "MRN_TAKEN"
    assert ward.scalar("SELECT count(*) FROM patient") == 1


def test_physician_may_not_register_a_patient(ward: Ward) -> None:
    response = ward.client(PHYSICIAN_W1).post("/api/v1/patients", json=VALID)
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert ward.scalar("SELECT count(*) FROM patient") == 0


def test_without_authentication_is_401_even_with_a_bad_body(ward: Ward) -> None:
    response = ward.client(None).post("/api/v1/patients", json={"mrn": "bad"})
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
