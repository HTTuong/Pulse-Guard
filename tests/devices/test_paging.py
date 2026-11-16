"""Cursor pagination of the device list (docs/api.md, Pagination)."""

import pytest

from tests.ward import TECHNICIAN, Ward

pytestmark = pytest.mark.db


def page(ward: Ward, **params: object) -> dict[str, object]:
    response = ward.client(TECHNICIAN).get("/api/v1/devices", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def serials(body: dict[str, object]) -> list[str]:
    items = body["items"]
    assert isinstance(items, list)
    return [str(item["serial"]) for item in items]


def test_pages_cover_every_device_once_in_order(ward: Ward) -> None:
    for n in range(1, 8):
        ward.new_device(f"SN-PO-{n:04d}", "PULSE_OXIMETER")
    first = page(ward, limit=3)
    second = page(ward, limit=3, cursor=first["next_cursor"])
    third = page(ward, limit=3, cursor=second["next_cursor"])
    assert serials(first) + serials(second) + serials(third) == [
        f"SN-PO-{n:04d}" for n in range(1, 8)
    ]
    assert third["next_cursor"] is None


def test_an_exact_last_page_has_no_cursor(ward: Ward) -> None:
    for n in range(1, 4):
        ward.new_device(f"SN-PO-{n:04d}", "PULSE_OXIMETER")
    assert page(ward, limit=3)["next_cursor"] is None


def test_devices_added_between_pages_shift_nothing(ward: Ward) -> None:
    """With offset paging a new row before the boundary pushes one row onto the next page twice."""
    for n in (2, 4, 6, 8):
        ward.new_device(f"SN-PO-{n:04d}", "PULSE_OXIMETER")
    first = page(ward, limit=2)
    assert serials(first) == ["SN-PO-0002", "SN-PO-0004"]
    ward.new_device("SN-PO-0001", "PULSE_OXIMETER")  # before the boundary
    ward.new_device("SN-PO-0007", "PULSE_OXIMETER")  # after it
    rest = page(ward, limit=10, cursor=first["next_cursor"])
    assert serials(rest) == ["SN-PO-0006", "SN-PO-0007", "SN-PO-0008"]


def test_filters(ward: Ward) -> None:
    ward.new_device("SN-PM-0001", "PATIENT_MONITOR")
    retired = ward.new_device("SN-PO-0001", "PULSE_OXIMETER")
    ward.new_device("SN-PO-0002", "PULSE_OXIMETER")
    assert ward.client(TECHNICIAN).post(f"/api/v1/devices/{retired}/retire").status_code == 200
    assert serials(page(ward, kind="PULSE_OXIMETER")) == ["SN-PO-0001", "SN-PO-0002"]
    assert serials(page(ward, kind="PULSE_OXIMETER", status="AVAILABLE")) == ["SN-PO-0002"]


def test_cursor_used_with_other_filters_is_400(ward: Ward) -> None:
    for n in range(1, 4):
        ward.new_device(f"SN-PO-{n:04d}", "PULSE_OXIMETER")
    cursor = page(ward, limit=1)["next_cursor"]
    response = ward.client(TECHNICIAN).get(
        "/api/v1/devices", params={"cursor": cursor, "status": "AVAILABLE"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CURSOR"


@pytest.mark.parametrize("cursor", ["not base64 !", "e30", "bnVsbA", "eyJhZnRlciI6IDF9"])
def test_broken_cursor_is_400(ward: Ward, cursor: str) -> None:
    # e30 = {}, bnVsbA = null, eyJhZnRlciI6IDF9 = {"after": 1}
    response = ward.client(TECHNICIAN).get("/api/v1/devices", params={"cursor": cursor})
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CURSOR"


@pytest.mark.parametrize("limit", [0, 501, -1])
def test_limit_outside_1_to_500_is_400(ward: Ward, limit: int) -> None:
    response = ward.client(TECHNICIAN).get("/api/v1/devices", params={"limit": limit})
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "limit"
