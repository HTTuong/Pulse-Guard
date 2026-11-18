"""Readings in a batch: each judged on its own (REQ-MEA-01 to REQ-MEA-05, REQ-CON-02)."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from tests.ingest.bed import Bed, equipped_bed, later, reading
from tests.ward import Ward

pytestmark = pytest.mark.db
PO, RR = "SN-PO-0001", "SN-RR-0001"


@pytest.fixture
def bed(ward: Ward) -> Bed:
    equipped = equipped_bed(ward, 1)
    ward.clock.current += timedelta(minutes=1)
    return equipped


def stored_count(ward: Ward) -> object:
    return ward.scalar("SELECT count(*) FROM measurement")


def test_readings_are_stored_for_the_session(bed: Bed, ward: Ward) -> None:
    readings = [
        reading(PO, 1, later(ward, -3), "PR", 72),
        reading(PO, 2, later(ward, -3), "SPO2", 97),
        reading(RR, 1, later(ward, -2), "RR", 16),
    ]
    response = bed.send(readings)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stored"] == [
        {"device": PO, "seq": 1},
        {"device": PO, "seq": 2},
        {"device": RR, "seq": 1},
    ]
    assert body["duplicates"] == body["refused"] == body["events_stored"] == []
    assert ward.scalar("SELECT count(DISTINCT session_id) FROM measurement") == 1
    assert str(ward.scalar("SELECT session_id FROM measurement LIMIT 1")) == bed.session


def test_the_same_reading_three_times_is_stored_once(bed: Bed, ward: Ward) -> None:
    """REQ-MEA-03: a resend after a lost acknowledgement."""
    batch = [reading(PO, 41, later(ward, -1))]
    outcomes = [bed.send(batch).json() for _ in range(3)]
    assert [len(o["stored"]) for o in outcomes] == [1, 0, 0]
    assert [len(o["duplicates"]) for o in outcomes] == [0, 1, 1]
    assert stored_count(ward) == 1


def test_same_seq_with_other_content_is_a_conflict_not_a_duplicate(bed: Bed, ward: Ward) -> None:
    """R-ORD-04: a sensor that restarted its numbering must be noticed, not silenced."""
    assert bed.send([reading(PO, 1, later(ward, -10), "PR", 72)]).json()["stored"]
    body = bed.send([reading(PO, 1, later(ward, -1), "PR", 88)]).json()
    assert body["refused"] == [
        {"device": PO, "seq": 1, "code": "SEQ_CONFLICT", "detail": "Same seq with other content"}
    ]
    assert stored_count(ward) == 1
    assert ward.scalar("SELECT count(*) FROM reading_rejection WHERE code = 'SEQ_CONFLICT'") == 1


def test_same_seq_twice_in_one_batch(bed: Bed, ward: Ward) -> None:
    same = reading(PO, 5, later(ward, -1))
    other = reading(PO, 5, later(ward, -1), "PR", 90)
    body = bed.send([same, same, other]).json()
    assert len(body["stored"]) == 1
    assert len(body["duplicates"]) == 1
    assert [r["code"] for r in body["refused"]] == ["SEQ_CONFLICT"]


def test_a_value_is_compared_as_stored(bed: Bed, ward: Ward) -> None:
    """The database keeps one decimal: 72.04 is stored as 72.0, so 72.04 again is a duplicate,
    while 72.06 (stored 72.1) is other content."""
    assert bed.send([reading(PO, 1, later(ward, -1), "PR", 72.04)]).json()["stored"]
    assert ward.scalar("SELECT value FROM measurement") == 72
    assert bed.send([reading(PO, 1, later(ward, -1), "PR", 72.04)]).json()["duplicates"]
    assert bed.send([reading(PO, 1, later(ward, -1), "PR", 72.06)]).json()["refused"]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"device": "SN-PO-9999"}, "UNKNOWN_DEVICE"),
        ({"parameter": "RR", "unit": "breaths/min"}, "WRONG_PARAMETER"),  # oximeter sends RR
        ({"device": "SN-PM-0001"}, "WRONG_PARAMETER"),  # a monitor measures nothing
        ({"unit": "bpm"}, "BAD_UNIT"),
        ({"unit": "%"}, "BAD_UNIT"),
        ({"measured_at": "2026-11-12T18:53:00"}, "BAD_TIME"),  # no offset
        ({"measured_at": "12.11.2026 18:53"}, "BAD_TIME"),
        ({"measured_at": "2026-02-30T10:00:00.000Z"}, "BAD_TIME"),  # no such day
        ({"value": 301}, "OUT_OF_RANGE"),
        ({"value": 300.05}, "OUT_OF_RANGE"),  # stored as 300.1
        ({"value": -1}, "OUT_OF_RANGE"),
        ({"parameter": "SPO2", "unit": "%", "value": 95.5}, "OUT_OF_RANGE"),
        ({"parameter": "SPO2", "unit": "%", "value": 101}, "OUT_OF_RANGE"),
    ],
)
def test_a_reading_breaking_a_rule_is_refused_with_its_code(
    bed: Bed, ward: Ward, change: dict[str, object], code: str
) -> None:
    body = bed.send([reading(PO, 1, later(ward, -1)) | change]).json()
    assert [r["code"] for r in body["refused"]] == [code]
    assert stored_count(ward) == 0
    assert ward.scalar("SELECT code FROM reading_rejection") == code


@pytest.mark.parametrize(
    "change",
    [
        {"value": 300},
        {"value": 300.04},  # stored as 300.0
        {"value": 0},
        {"parameter": "SPO2", "unit": "%", "value": 100},
        {"parameter": "SPO2", "unit": "%", "value": 0},
    ],
)
def test_edges_of_the_plausible_range_are_stored(
    bed: Bed, ward: Ward, change: dict[str, object]
) -> None:
    assert bed.send([reading(PO, 1, later(ward, -1)) | change]).json()["stored"]


def test_time_window_edges(bed: Bed, ward: Ward) -> None:
    """REQ-MEA-05: at most 60 s ahead and 24 h behind the server clock, both inclusive."""
    now = ward.clock.now()
    cases = [
        (1, now + timedelta(seconds=60), "stored"),
        (2, now + timedelta(seconds=60, milliseconds=1), "refused"),
        (3, now - timedelta(hours=24), "refused"),  # before the sensor was assigned
        (4, now - timedelta(seconds=30), "stored"),
    ]
    body = bed.send([reading(PO, seq, at) for seq, at, _ in cases]).json()
    assert [r["seq"] for r in body["stored"]] == [1, 4]
    assert {r["seq"]: r["code"] for r in body["refused"]} == {2: "BAD_TIME", 3: "NO_SESSION"}


def test_24_hours_behind_is_the_edge(ward: Ward) -> None:
    bed = equipped_bed(ward, 1)
    assigned = ward.clock.now()
    ward.clock.current = assigned + timedelta(hours=24)
    body = bed.send(
        [
            reading(PO, 1, assigned),
            reading(PO, 2, assigned - timedelta(milliseconds=1)),
            reading(PO, 3, assigned + timedelta(milliseconds=1)),
        ]
    ).json()
    # seq 1 is exactly 24 h old; seq 2 is older than 24 h; seq 3 is just inside
    assert [r["seq"] for r in body["stored"]] == [1, 3]
    assert [(r["seq"], r["code"]) for r in body["refused"]] == [(2, "BAD_TIME")]


def test_one_bad_reading_does_not_cost_the_others(bed: Bed, ward: Ward) -> None:
    body = bed.send(
        [
            reading(PO, 1, later(ward, -1)),
            reading(PO, 2, later(ward, -1), value=999),
            reading(RR, 1, later(ward, -1), "RR", 14),
        ]
    ).json()
    assert [r["seq"] for r in body["stored"]] == [1, 1]
    assert [r["seq"] for r in body["refused"]] == [2]


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_nan_and_infinity_are_refused_not_stored(bed: Bed, ward: Ward, literal: str) -> None:
    """Python's json module reads NaN and Infinity without complaint (protocol.md, P-04)."""
    item = reading(PO, 1, later(ward, -1))
    text = json.dumps(
        {"monitor": bed.monitor_serial, "sent_at": "2026-11-12T18:53:03.000Z", "readings": [item]}
    ).replace('"value": 72', f'"value": {literal}')
    response = ward.client(None).post(
        "/api/v1/ingest/batches",
        content=text,
        headers={"Authorization": f"Bearer {bed.credential}", "Content-Type": "application/json"},
    )
    assert response.status_code == 200, response.text
    assert [r["code"] for r in response.json()["refused"]] == ["OUT_OF_RANGE"]
    assert stored_count(ward) == 0


def test_readings_arriving_out_of_order_are_ordered_by_measured_time(bed: Bed, ward: Ward) -> None:
    """REQ-MEA-04: measured t1 < t2 < t3, arriving t3, t1, t2."""
    from pulseguard.measurements.queries import current_values, history

    t1, t2, t3 = later(ward, -30), later(ward, -20), later(ward, -10)
    for seq, at, value in [(3, t3, 90), (1, t1, 70), (2, t2, 80)]:
        assert bed.send([reading(PO, seq, at, "PR", value)]).json()["stored"]
    session = __import__("uuid").UUID(bed.session)
    with ward.sessions() as db:
        assert current_values(db, session)["PR"].value == 90
        assert [m.value for m in history(db, session, "PR", t1, t3 + timedelta(seconds=1))] == [
            70,
            80,
            90,
        ]


def test_the_same_batch_sent_twice_in_parallel_is_stored_once(bed: Bed, ward: Ward) -> None:
    """A monitor resends after a timeout while the first send is still being processed."""
    batch = [reading(PO, seq, later(ward, -seq)) for seq in range(1, 51)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        bodies = [r.json() for r in pool.map(lambda _: bed.send(batch), range(2))]
    assert sorted(len(b["stored"]) for b in bodies) == [0, 50]
    assert sorted(len(b["duplicates"]) for b in bodies) == [0, 50]
    assert stored_count(ward) == 50


def test_more_than_1000_readings_is_413(bed: Bed, ward: Ward) -> None:
    batch = [reading(PO, seq, later(ward, -1)) for seq in range(1, 1002)]
    response = bed.send(batch)
    assert response.status_code == 413
    assert response.json()["code"] == "BATCH_TOO_LARGE"
    assert stored_count(ward) == 0


def test_exactly_1000_readings_is_accepted(bed: Bed, ward: Ward) -> None:
    batch = [reading(PO, seq, later(ward, -1)) for seq in range(1, 1001)]
    assert len(bed.send(batch).json()["stored"]) == 1000


def test_a_body_over_1_mb_is_413(bed: Bed, ward: Ward) -> None:
    response = bed.send([reading(PO, 1, later(ward, -1))], padding="x" * 1_000_001)
    assert response.status_code == 413


def test_a_batch_with_status_events_is_refused_whole_for_now(bed: Bed, ward: Ward) -> None:
    """Events arrive with technical alarms; until then they are refused, not dropped in silence."""
    event = {"event_seq": 1, "type": "SENSOR_LOST", "device": PO, "at": "2026-11-12T18:53:00.000Z"}
    response = bed.send([reading(PO, 1, later(ward, -1))], events=[event])
    assert response.status_code == 400
    assert stored_count(ward) == 0


def test_an_unreadable_reading_makes_the_batch_400(bed: Bed, ward: Ward) -> None:
    response = bed.send([reading(PO, 1, later(ward, -1)), reading(PO, "two", later(ward, -1))])  # type: ignore[arg-type]
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "readings.1.seq"
    assert stored_count(ward) == 0
