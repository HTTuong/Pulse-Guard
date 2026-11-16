"""Registering, listing and retiring devices (REQ-DEV-01, REQ-DEV-04, REQ-ACC-03)."""

import hashlib

import pytest
from httpx import Response

from pulseguard.access.actor import Actor
from tests.ward import NURSE_W1, PHYSICIAN_W1, TECHNICIAN, Ward

pytestmark = pytest.mark.db


def register(ward: Ward, serial: str, kind: str = "PULSE_OXIMETER") -> Response:
    response: Response = ward.client(TECHNICIAN).post(
        "/api/v1/devices", json={"serial": serial, "kind": kind}
    )
    return response


def test_technician_registers_an_available_sensor_without_a_credential(ward: Ward) -> None:
    response = register(ward, "SN-PO-0001")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "AVAILABLE"
    assert body["session_id"] is None
    assert body["connection"] == {"state": "UNKNOWN", "since": None}
    assert body["credential"] is None


def test_a_patient_monitor_gets_its_credential_once_and_only_its_hash_is_stored(
    ward: Ward,
) -> None:
    body = register(ward, "SN-PM-0001", "PATIENT_MONITOR").json()
    credential = body["credential"]
    assert len(credential) >= 43  # 32 random bytes, base64url
    stored = ward.scalar("SELECT credential_hash FROM device WHERE serial = 'SN-PM-0001'")
    assert stored == hashlib.sha256(credential.encode()).hexdigest()
    assert stored != credential
    listed = ward.client(TECHNICIAN).get("/api/v1/devices").json()["items"][0]
    assert "credential" not in listed


def test_duplicate_serial_is_409(ward: Ward) -> None:
    assert register(ward, "SN-PO-0001").status_code == 201
    response = register(ward, "SN-PO-0001", "RESPIRATION")
    assert response.status_code == 409
    assert response.json()["code"] == "SERIAL_TAKEN"


@pytest.mark.parametrize(
    "body",
    [
        {"serial": "sn-po-0001", "kind": "PULSE_OXIMETER"},  # lower case
        {"serial": "SN", "kind": "PULSE_OXIMETER"},  # too short
        {"serial": "S" * 41, "kind": "PULSE_OXIMETER"},  # too long
        {"serial": "SN PO 1", "kind": "PULSE_OXIMETER"},  # spaces
        {"serial": "SN-PO-0001", "kind": "THERMOMETER"},  # unknown kind
        {"serial": "SN-PO-0001"},  # kind missing
    ],
)
def test_invalid_device_is_400(ward: Ward, body: dict[str, str]) -> None:
    response = ward.client(TECHNICIAN).post("/api/v1/devices", json=body)
    assert response.status_code == 400
    assert ward.scalar("SELECT count(*) FROM device") == 0


@pytest.mark.parametrize("actor", [NURSE_W1, PHYSICIAN_W1])
def test_only_technicians_register(ward: Ward, actor: Actor) -> None:
    response = ward.client(actor).post(
        "/api/v1/devices", json={"serial": "SN-PO-0001", "kind": "PULSE_OXIMETER"}
    )
    assert response.status_code == 403


def test_physician_may_not_list_devices(ward: Ward) -> None:
    assert ward.client(PHYSICIAN_W1).get("/api/v1/devices").status_code == 403


def test_retire_an_available_device(ward: Ward) -> None:
    device = ward.new_device("SN-PO-0001", "PULSE_OXIMETER")
    response = ward.client(TECHNICIAN).post(f"/api/v1/devices/{device}/retire")
    assert response.status_code == 200
    assert response.json()["status"] == "RETIRED"
    assert ward.scalar("SELECT retired_at FROM device") is not None


def test_nurse_may_not_retire(ward: Ward) -> None:
    device = ward.new_device("SN-PO-0001", "PULSE_OXIMETER")
    assert ward.client(NURSE_W1).post(f"/api/v1/devices/{device}/retire").status_code == 403
