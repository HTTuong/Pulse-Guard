"""The device state machine, as a state by action matrix (REQ-DEV-02 to REQ-DEV-04).

Every action is tried from every state; the expected outcome is written out in full, so a rule
that is missing or added shows up as one cell of the table.
"""

import pytest

from tests.ward import TECHNICIAN, Ward

pytestmark = pytest.mark.db

# (state, action) -> (HTTP status, code or None, state afterwards)
EXPECTED = {
    ("AVAILABLE", "assign"): (201, None, "ASSIGNED"),
    ("AVAILABLE", "unassign"): (404, "NOT_FOUND", "AVAILABLE"),
    ("AVAILABLE", "retire"): (200, None, "RETIRED"),
    ("ASSIGNED", "assign"): (409, "DEVICE_NOT_AVAILABLE", "ASSIGNED"),
    ("ASSIGNED", "unassign"): (204, None, "AVAILABLE"),
    ("ASSIGNED", "retire"): (409, "DEVICE_ASSIGNED", "ASSIGNED"),
    ("RETIRED", "assign"): (409, "DEVICE_RETIRED", "RETIRED"),
    ("RETIRED", "unassign"): (404, "NOT_FOUND", "RETIRED"),
    ("RETIRED", "retire"): (409, "DEVICE_RETIRED", "RETIRED"),
}


def put_in_state(ward: Ward, state: str, device: str, session: str) -> None:
    client = ward.client(TECHNICIAN)
    if state in ("ASSIGNED",):
        assert (
            client.post(
                f"/api/v1/sessions/{session}/devices", json={"device_id": device}
            ).status_code
            == 201
        )
    if state == "RETIRED":
        assert client.post(f"/api/v1/devices/{device}/retire").status_code == 200


@pytest.mark.parametrize(("state", "action"), sorted(EXPECTED))
def test_state_by_action(ward: Ward, state: str, action: str) -> None:
    session = ward.open_session(1, 1)
    device = ward.new_device("SN-PO-0001", "PULSE_OXIMETER")
    put_in_state(ward, state, device, session)
    client = ward.client(TECHNICIAN)
    if action == "assign":
        response = client.post(f"/api/v1/sessions/{session}/devices", json={"device_id": device})
    elif action == "unassign":
        response = client.delete(f"/api/v1/sessions/{session}/devices/{device}")
    else:
        response = client.post(f"/api/v1/devices/{device}/retire")

    status, code, after = EXPECTED[(state, action)]
    assert response.status_code == status, response.text
    if code:
        assert response.json()["code"] == code
    assert ward.scalar("SELECT status FROM device WHERE id = :id", id=device) == after
