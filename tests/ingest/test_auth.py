"""Monitors authenticate with their own credential (REQ-ACC-03)."""

import pytest

from tests.ingest.bed import equipped_bed
from tests.ward import TECHNICIAN, Ward

pytestmark = pytest.mark.db


def post(ward: Ward, headers: dict[str, str], monitor: str = "SN-PM-0001") -> int:
    body = {"monitor": monitor, "sent_at": "2026-11-12T18:52:03.000Z", "readings": []}
    status: int = (
        ward.client(None).post("/api/v1/ingest/batches", json=body, headers=headers).status_code
    )
    return status


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer ", "Basic abc", "bearer {c}", "Bearer {c} extra", "Bearer x{c}"],
)
def test_missing_or_wrong_credential_is_401(ward: Ward, header: str | None) -> None:
    bed = equipped_bed(ward, 1, sensors=False)
    headers = {} if header is None else {"Authorization": header.format(c=bed.credential)}
    assert post(ward, headers) == 401


def test_the_right_credential_is_accepted(ward: Ward) -> None:
    bed = equipped_bed(ward, 1, sensors=False)
    assert post(ward, {"Authorization": f"Bearer {bed.credential}"}) == 200


def test_a_batch_that_names_another_monitor_is_403(ward: Ward) -> None:
    bed = equipped_bed(ward, 1, sensors=False)
    equipped_bed(ward, 2, sensors=False)
    assert post(ward, {"Authorization": f"Bearer {bed.credential}"}, monitor="SN-PM-0002") == 403


def test_a_retired_monitor_is_refused_at_once(ward: Ward) -> None:
    bed = equipped_bed(ward, 1, sensors=False)
    monitor_id = bed.device_ids["SN-PM-0001"]
    client = ward.client(TECHNICIAN)
    assert client.delete(f"/api/v1/sessions/{bed.session}/devices/{monitor_id}").status_code == 204
    assert client.post(f"/api/v1/devices/{monitor_id}/retire").status_code == 200
    assert post(ward, {"Authorization": f"Bearer {bed.credential}"}) == 401


def test_a_user_route_does_not_accept_a_device_credential(ward: Ward) -> None:
    """403, as docs/api.md says. Changed in d244 from 401: before staff login existed every staff
    route answered 401 to anything, so the first version of this test could only expect that."""
    bed = equipped_bed(ward, 1, sensors=False)
    response = ward.client(None).get(
        f"/api/v1/sessions/{bed.session}", headers={"Authorization": f"Bearer {bed.credential}"}
    )
    assert response.status_code == 403


def test_pairings_list_the_sensors_of_the_monitor(ward: Ward) -> None:
    bed = equipped_bed(ward, 1)
    response = ward.client(None).get(
        "/api/v1/ingest/pairings", headers={"Authorization": f"Bearer {bed.credential}"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "monitor": "SN-PM-0001",
        "paired": [{"serial": "SN-PO-0001"}, {"serial": "SN-RR-0001"}],
        "unpaired": [],
    }
