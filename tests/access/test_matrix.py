"""Every route, every kind of caller, one table (REQ-ACC-02, docs/api.md "Who may call what").

Each probe aims at a resource in a state where a caller who is allowed gets past the permission
check and then stops at 404 or 409, while a caller who is not allowed gets 403. The order of checks
(401, 400, 404, 403, 409) makes that possible, and it means no probe changes anything, so the cells
do not depend on each other. The last check proves it: nothing changed.
"""

from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy import text

from tests.ingest.bed import equipped_bed
from tests.ward import ADMIN, NURSE_W1, NURSE_W2, PHYSICIAN_W1, TECHNICIAN, Ward, bed

pytestmark = pytest.mark.db

IDENTITIES = ["NURSE_W1", "NURSE_W2", "PHYSICIAN_W1", "TECHNICIAN", "ADMIN", "NONE", "DEVICE"]
ACTORS = {
    "NURSE_W1": NURSE_W1,
    "NURSE_W2": NURSE_W2,
    "PHYSICIAN_W1": PHYSICIAN_W1,
    "TECHNICIAN": TECHNICIAN,
    "ADMIN": ADMIN,
}
STAFF = IDENTITIES[:5]


@dataclass
class Scene:
    ward: Ward
    open_session: str
    closed_session: str
    assigned_device: str
    free_device: str
    alarm: str
    patient: str
    credential: str


@pytest.fixture
def scene(ward: Ward) -> Scene:
    equipped = equipped_bed(ward, 1)  # open session in W1-B1 with monitor and two sensors
    closed = ward.open_session(2, 2)
    ward.clock.current += timedelta(minutes=5)
    assert ward.client(NURSE_W1).post(f"/api/v1/sessions/{closed}/discharge").status_code == 200
    free = ward.new_device("SN-RR-0099", "RESPIRATION")
    alarm = "70000000-0000-0000-0000-000000000001"
    with ward.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO alarm (id, session_id, kind, code, parameter, priority, state,"
                " started_at, resolved_at, resolution) VALUES (:id, :s, 'PHYSIOLOGICAL',"
                " 'PR_HIGH', 'PR', 'HIGH', 'RESOLVED', now(), now(), 'AUTO_RESOLVED')"
            ),
            {"id": alarm, "s": equipped.session},
        )
    patient = str(
        ward.scalar("SELECT patient_id FROM monitoring_session WHERE id = :s", s=equipped.session)
    )
    return Scene(
        ward,
        equipped.session,
        closed,
        equipped.device_ids["SN-PO-0001"],
        free,
        alarm,
        patient,
        equipped.credential,
    )


def probes(s: Scene) -> list[tuple[str, str, dict[str, object] | None, dict[str, int]]]:
    """(method, path, body, expected status by identity). Missing staff identities expect 403;
    NONE expects 401 on every route but health."""

    def only(**allowed: int) -> dict[str, int]:
        return {who: allowed.get(who, 403) for who in STAFF} | {"NONE": 401, "DEVICE": 403}

    every_staff = {who: 200 for who in STAFF}
    return [
        ("GET", "/health", None, {who: 200 for who in IDENTITIES}),
        ("GET", "/auth/me", None, every_staff | {"NONE": 401, "DEVICE": 403}),
        (
            "POST",
            "/patients",
            {"mrn": "TEST-0001", "given_name": "A", "family_name": "B"},
            only(NURSE_W1=409, NURSE_W2=409),
        ),
        (
            "POST",
            "/sessions",
            {"patient_id": s.patient, "bed_id": str(bed(1, 3))},
            only(NURSE_W1=409),
        ),
        ("GET", f"/sessions/{s.open_session}", None, only(NURSE_W1=200, PHYSICIAN_W1=200)),
        ("POST", f"/sessions/{s.closed_session}/discharge", None, only(NURSE_W1=409)),
        (
            "POST",
            f"/sessions/{s.closed_session}/devices",
            {"device_id": s.free_device},
            only(NURSE_W1=409, TECHNICIAN=409),
        ),
        (
            "DELETE",
            f"/sessions/{s.open_session}/devices/{s.free_device}",
            None,
            only(NURSE_W1=404, TECHNICIAN=404),
        ),
        ("GET", f"/sessions/{s.open_session}/limits", None, only(NURSE_W1=200, PHYSICIAN_W1=200)),
        (
            "POST",
            "/devices",
            {"serial": "SN-PO-0001", "kind": "PULSE_OXIMETER"},
            only(TECHNICIAN=409),
        ),
        ("GET", "/devices", None, only(NURSE_W1=200, NURSE_W2=200, TECHNICIAN=200)),
        ("POST", f"/devices/{s.assigned_device}/retire", None, only(TECHNICIAN=409)),
        ("GET", f"/alarms/{s.alarm}", None, only(NURSE_W1=200, PHYSICIAN_W1=200)),
        ("POST", f"/alarms/{s.alarm}/acknowledge", None, only(NURSE_W1=409, PHYSICIAN_W1=409)),
        (
            "POST",
            "/users",
            {
                "email": "nurse1@example.com",
                "display_name": "X",
                "role": "NURSE",
                "password": "long enough password",
                "unit_codes": [],
            },
            only(ADMIN=409),
        ),
        ("PATCH", f"/users/{ADMIN.user_id}", {"active": False}, only(ADMIN=409)),
        (
            "POST",
            "/ingest/batches",
            {"monitor": "SN-PM-0001", "sent_at": "2026-11-12T18:52:03.000Z", "readings": []},
            {**{w: 403 for w in STAFF}, "NONE": 401, "DEVICE": 200},
        ),
        ("GET", "/ingest/pairings", None, {**{w: 403 for w in STAFF}, "NONE": 401, "DEVICE": 200}),
    ]


def snapshot(ward: Ward) -> tuple[object, ...]:
    return tuple(
        ward.scalar(sql)
        for sql in (
            "SELECT count(*) FROM patient",
            "SELECT count(*) FROM monitoring_session",
            "SELECT string_agg(status::text, ',' ORDER BY serial) FROM device",
            "SELECT count(*) FROM device_assignment WHERE unassigned_at IS NULL",
            "SELECT string_agg(state::text, ',') FROM alarm",
            "SELECT count(*) FROM alarm_event",
            "SELECT count(*) FROM app_user",
            "SELECT bool_and(active) FROM app_user",
            "SELECT sum(token_version) FROM app_user",
            "SELECT count(*) FROM measurement",
        )
    )


@pytest.mark.parametrize("identity", IDENTITIES)
def test_route_by_identity(scene: Scene, identity: str) -> None:
    ward = scene.ward
    if identity in ACTORS:
        client = ward.as_token(ward.token(ACTORS[identity]))
    elif identity == "DEVICE":
        client = ward.as_token(scene.credential)
    else:
        client = ward.as_token(None)
    before = snapshot(ward)
    wrong = []
    for method, path, body, expected in probes(scene):
        response = client.request(method, f"/api/v1{path}", json=body)
        if response.status_code != expected[identity]:
            wrong.append(
                f"{method} {path}: got {response.status_code}, expected {expected[identity]}"
                f" {response.text[:120]}"
            )
    assert wrong == []
    assert snapshot(ward) == before
