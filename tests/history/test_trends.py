"""Trends of one parameter over a time range (REQ-VIEW-03, REQ-MEA-04, docs/api.md Downsampling)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import Response

from pulseguard.access.actor import Actor
from pulseguard.clock import to_wire
from tests.ingest.bed import Bed, Pulse, equipped_bed, reading
from tests.ward import NURSE_W1, NURSE_W2, PHYSICIAN_W1, TECHNICIAN, Ward

pytestmark = pytest.mark.db
PO = "SN-PO-0001"


@dataclass
class Scene:
    ward: Ward
    bed: Bed
    pulse: Pulse
    t0: datetime  # the first reading of `pulse`


@pytest.fixture
def scene(ward: Ward) -> Scene:
    bed = equipped_bed(ward, 1)
    t0 = ward.clock.now() + timedelta(minutes=1)
    ward.clock.current += timedelta(minutes=30)
    pulse = Pulse(bed)
    pulse.start = t0
    return Scene(ward, bed, pulse, t0)


def get(
    s: Scene, start: datetime | str, end: datetime | str, *, actor: Actor = NURSE_W1, **params: Any
) -> Response:
    query = {
        "parameter": "PR",
        "from": start if isinstance(start, str) else to_wire(start),
        "to": end if isinstance(end, str) else to_wire(end),
    } | params
    response: Response = s.ward.client(actor).get(
        f"/api/v1/sessions/{s.bed.session}/measurements", params=query
    )
    return response


def seconds(s: Scene, n: float) -> datetime:
    return s.t0 + timedelta(seconds=n)


def values_of(body: dict[str, object]) -> list[float]:
    items = body["items"]
    assert isinstance(items, list)
    return [float(item["value"]) for item in items]


def test_raw_readings_in_measured_order(scene: Scene) -> None:
    scene.pulse.send([70 + n for n in range(30)])
    body = get(scene, seconds(scene, 0), seconds(scene, 30)).json()
    assert body["downsampled"] is False
    assert body["total"] == 30
    assert values_of(body) == [70 + n for n in range(30)]
    assert body["items"][0] == {
        "seq": 1,
        "device_id": scene.bed.device_ids[PO],
        "serial": PO,
        "parameter": "PR",
        "value": 70.0,
        "measured_at": to_wire(seconds(scene, 0)),
    }


def test_the_range_is_half_open(scene: Scene) -> None:
    scene.pulse.send([70, 71, 72, 73, 74])  # at 0..4 s
    assert values_of(get(scene, seconds(scene, 1), seconds(scene, 3)).json()) == [71, 72]


def test_readings_that_arrived_out_of_order_come_back_in_measured_order(scene: Scene) -> None:
    batch = scene.pulse.readings([70, 71, 72, 73, 74])
    for item in [batch[4], batch[0], batch[3], batch[1], batch[2]]:
        assert scene.bed.send([item]).status_code == 200
    assert values_of(get(scene, seconds(scene, 0), seconds(scene, 5)).json()) == [
        70,
        71,
        72,
        73,
        74,
    ]


def test_other_parameters_and_sessions_are_not_mixed_in(scene: Scene, ward: Ward) -> None:
    scene.pulse.send([70, 71])
    scene.pulse.send([97, 98], "SPO2")
    other = equipped_bed(ward, 2)  # assigned now, so its readings come after this moment
    other_pulse = Pulse(other, "SN-PO-0002")
    other_pulse.start = ward.clock.now()
    ward.clock.current += timedelta(minutes=1)
    other_pulse.send([120, 121])
    everything = get(scene, seconds(scene, 0), ward.clock.now()).json()
    assert values_of(everything) == [70, 71]


def test_pages_cover_every_reading_once(scene: Scene) -> None:
    scene.pulse.send([70 + n for n in range(30)])
    seen, cursor = [], None
    for _ in range(10):
        params: dict[str, Any] = {"limit": 7} | ({"cursor": cursor} if cursor else {})
        body = get(scene, seconds(scene, 0), seconds(scene, 60), **params).json()
        seen += values_of(body)
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == [70 + n for n in range(30)]


def test_readings_at_the_same_instant_are_paged_by_id(scene: Scene) -> None:
    """Three readings measured at the same moment: the cursor must not skip or repeat one."""
    same = seconds(scene, 5)
    batch = [reading(PO, n, same, "PR", 80 + n) for n in range(1, 4)]
    assert len(scene.bed.send(batch).json()["stored"]) == 3
    seen, cursor = [], None
    for _ in range(5):
        body = get(
            scene,
            seconds(scene, 0),
            seconds(scene, 10),
            limit=1,
            **({"cursor": cursor} if cursor else {}),
        ).json()
        seen += values_of(body)
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == [81, 82, 83]


def test_a_late_reading_before_the_cursor_does_not_shift_the_next_page(scene: Scene) -> None:
    scene.pulse.send([70, 71, 72, 73])  # 0..3 s
    late = scene.pulse.readings([99])[0] | {"measured_at": to_wire(seconds(scene, 0.5))}
    first = get(scene, seconds(scene, 0), seconds(scene, 10), limit=2).json()
    assert values_of(first) == [70, 71]
    assert scene.bed.send([late]).status_code == 200
    rest = get(scene, seconds(scene, 0), seconds(scene, 10), limit=10, cursor=first["next_cursor"])
    assert values_of(rest.json()) == [72, 73]


def test_downsampled_buckets_keep_the_spike(scene: Scene) -> None:
    """120 readings into at most 10 buckets: counts add up, and a single beat of 150 among 80s is
    still the max of its bucket, while the mean hides it."""
    values = [80.0] * 120
    values[57] = 150
    scene.pulse.send(values)
    body = get(scene, seconds(scene, 0), seconds(scene, 120), max_points=10).json()
    assert body["downsampled"] is True and body["total"] == 120
    buckets = body["items"]
    assert len(buckets) == 10
    assert sum(b["count"] for b in buckets) == 120
    assert [b["max"] for b in buckets].count(150) == 1
    spike = next(b for b in buckets if b["max"] == 150)
    assert spike["min"] == 80 and spike["mean"] == pytest.approx(85.8)
    assert buckets[0]["start"] == to_wire(seconds(scene, 0))
    assert buckets[-1]["end"] == to_wire(seconds(scene, 120))
    assert [b["end"] for b in buckets[:-1]] == [b["start"] for b in buckets[1:]]


def test_a_gap_in_the_data_is_a_gap_in_the_buckets(scene: Scene) -> None:
    scene.pulse.send([80.0] * 30)  # 0..29 s
    scene.pulse.second = 90
    scene.pulse.send([80.0] * 30)  # 90..119 s
    body = get(scene, seconds(scene, 0), seconds(scene, 120), max_points=12).json()
    starts = [b["start"] for b in body["items"]]
    assert len(starts) == 6  # 12 buckets of 10 s, 6 of them empty and left out
    assert to_wire(seconds(scene, 30)) not in starts


def test_max_points_is_the_edge_between_raw_and_buckets(scene: Scene) -> None:
    scene.pulse.send([80.0] * 10)
    assert (
        get(scene, seconds(scene, 0), seconds(scene, 60), max_points=10).json()["downsampled"]
        is False
    )
    scene.pulse.send([80.0])
    assert (
        get(scene, seconds(scene, 0), seconds(scene, 60), max_points=10).json()["downsampled"]
        is True
    )


def test_a_cursor_on_downsampled_data_is_400(scene: Scene) -> None:
    scene.pulse.send([80.0] * 11)
    response = get(scene, seconds(scene, 0), seconds(scene, 60), max_points=10, cursor="e30")
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CURSOR"


def test_a_cursor_with_other_filters_is_400(scene: Scene) -> None:
    scene.pulse.send([80.0] * 5)
    cursor = get(scene, seconds(scene, 0), seconds(scene, 60), limit=2).json()["next_cursor"]
    response = get(scene, seconds(scene, 0), seconds(scene, 61), limit=2, cursor=cursor)
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CURSOR"


@pytest.mark.parametrize(
    ("start", "end", "code"),
    [
        ("2026-11-12T19:00:00", "2026-11-12T20:00:00.000Z", "INVALID_TIME"),  # no offset
        ("yesterday", "2026-11-12T20:00:00.000Z", "INVALID_TIME"),
        ("2026-11-12T20:00:00.000Z", "2026-11-12T20:00:00.000Z", "INVALID_RANGE"),  # empty
        ("2026-11-12T21:00:00.000Z", "2026-11-12T20:00:00.000Z", "INVALID_RANGE"),  # backwards
        ("2026-11-05T20:00:00.000Z", "2026-11-12T20:00:00.001Z", "INVALID_RANGE"),  # 7 days + 1 ms
    ],
)
def test_a_wrong_range_is_400(scene: Scene, start: str, end: str, code: str) -> None:
    response = get(scene, start, end)
    assert response.status_code == 400
    assert response.json()["code"] == code


def test_seven_days_exactly_is_allowed(scene: Scene) -> None:
    assert get(scene, "2026-11-05T20:00:00.000Z", "2026-11-12T20:00:00.000Z").status_code == 200


@pytest.mark.parametrize(
    "params", [{"max_points": 9}, {"max_points": 2001}, {"limit": 0}, {"parameter": "TEMP"}]
)
def test_wrong_parameters_are_400(scene: Scene, params: dict[str, Any]) -> None:
    assert get(scene, seconds(scene, 0), seconds(scene, 10), **params).status_code == 400


@pytest.mark.parametrize(
    ("actor", "status"), [(PHYSICIAN_W1, 200), (NURSE_W2, 403), (TECHNICIAN, 403)]
)
def test_who_may_read_trends(scene: Scene, actor: Actor, status: int) -> None:
    assert get(scene, seconds(scene, 0), seconds(scene, 10), actor=actor).status_code == status


def test_a_closed_session_can_still_be_read(scene: Scene) -> None:
    scene.pulse.send([80.0] * 3)
    client = scene.ward.client(NURSE_W1)
    assert client.post(f"/api/v1/sessions/{scene.bed.session}/discharge").status_code == 200
    assert values_of(get(scene, seconds(scene, 0), seconds(scene, 10)).json()) == [80, 80, 80]


def test_unknown_session_is_404_but_a_wrong_range_is_400_first(scene: Scene) -> None:
    client = scene.ward.client(NURSE_W1)
    url = "/api/v1/sessions/00000000-0000-0000-0000-000000000001/measurements"
    good = {"parameter": "PR", "from": "2026-11-12T19:00:00.000Z", "to": "2026-11-12T20:00:00.000Z"}
    assert client.get(url, params=good).status_code == 404
    assert client.get(url, params=good | {"from": "2026-11-12T19:00:00"}).status_code == 400


def test_never_more_buckets_than_max_points(scene: Scene) -> None:
    """Widths are whole microseconds, rounded up. Rounding down shrinks a bucket by less than one
    microsecond, so it only shows when readings fill the very end of the range: 2000 readings one
    millisecond apart in 2 s, at most 1999 buckets. Width 1001 us gives 1998 buckets; width
    1000 us would give 2000. A first version of this test used 120 readings in 120 s, where both
    widths give the same buckets, and a mutant that rounded down survived it (d245)."""
    batch = [
        reading(PO, n + 1, scene.t0 + timedelta(milliseconds=n), "PR", 80) for n in range(2000)
    ]
    for half in (batch[:1000], batch[1000:]):
        assert len(scene.bed.send(half).json()["stored"]) == 1000
    body = get(scene, seconds(scene, 0), seconds(scene, 2), max_points=1999).json()
    assert body["downsampled"] is True
    assert sum(b["count"] for b in body["items"]) == 2000
    assert len(body["items"]) <= 1999
