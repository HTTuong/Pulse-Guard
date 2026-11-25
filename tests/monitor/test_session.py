"""The protocol rules of the monitor, one frame at a time and without any socket."""

import json
import logging
from typing import Any

import pytest

from pulseguard.monitor.logs import MAX_TEXT
from pulseguard.protocol.codec import parse_wire_time
from tests.monitor.helpers import (
    DEVICE,
    MONITOR,
    PAIRING,
    Rig,
    at,
    code_of,
    heartbeat,
    hello,
    measurement,
    raw,
    run,
)

# The codes of protocol.md, plus BAD_FIELD added in d248 (docs/findings.md, F-04).
PROTOCOL_CODES = {
    "BAD_LENGTH", "BAD_JSON", "BAD_TIME", "BAD_UNIT", "MISSING_FIELD", "UNKNOWN_TYPE",
    "UNSUPPORTED_VERSION", "HELLO_FIRST", "NOT_PAIRED", "WRONG_DEVICE", "OUT_OF_RANGE",
    "SEQ_CONFLICT", "BAD_FIELD",
}  # fmt: skip


# ---- HELLO (P-07, P-08) --------------------------------------------------------------------


def test_a_valid_hello_is_acknowledged_with_the_version_and_the_monitor() -> None:
    rig = Rig()
    reply = rig.send(hello())
    ack = reply.messages[0]
    assert (ack["type"], ack["ack"], ack["version"], ack["monitor"]) == ("ACK", "HELLO", 1, MONITOR)
    assert not reply.close and rig.session.hello_done


def test_the_highest_common_version_is_chosen() -> None:
    reply = Rig().send(hello(versions=[3, 2, 1]))
    assert reply.messages[0]["version"] == 1


def test_the_highest_common_version_wins_when_the_monitor_speaks_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only version 1 exists, so with one supported version "highest" and "lowest" are the same;
    # a monitor that also spoke version 2 must pick it (P-07). Found by a mutant that survived.
    monkeypatch.setattr("pulseguard.monitor.session.SUPPORTED_VERSIONS", (1, 2))
    rig = Rig()
    assert rig.send(hello(versions=[1, 2, 3])).messages[0]["version"] == 2
    assert code_of(rig.send(heartbeat(v=1))) == "UNSUPPORTED_VERSION"  # every later frame: v 2
    assert rig.send(heartbeat(v=2)).messages == []


def test_no_common_version_is_refused_and_the_connection_closes() -> None:
    reply = Rig().send(hello(versions=[2, 3]))
    assert code_of(reply) == "UNSUPPORTED_VERSION" and reply.close


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"device": ...}, "MISSING_FIELD"),
        ({"device_type": ...}, "MISSING_FIELD"),
        ({"parameters": ...}, "MISSING_FIELD"),
        ({"versions": ...}, "MISSING_FIELD"),
        ({"device": ""}, "BAD_FIELD"),
        ({"device": "   "}, "BAD_FIELD"),
        ({"device": 7}, "BAD_FIELD"),
        ({"device_type": "THERMOMETER"}, "BAD_FIELD"),
        ({"device_type": None}, "BAD_FIELD"),
        ({"parameters": ["PR"]}, "BAD_FIELD"),
        ({"parameters": ["PR", "SPO2", "RR"]}, "BAD_FIELD"),
        ({"parameters": "PR"}, "BAD_FIELD"),
        ({"device_type": "RESPIRATION"}, "BAD_FIELD"),  # parameters still say PR and SPO2
        ({"versions": "1"}, "BAD_FIELD"),
        ({"versions": [True]}, "BAD_FIELD"),
        ({"versions": ["1"]}, "BAD_FIELD"),
        ({"sent_at": "2026-11-12T18:52:00Z"}, "BAD_TIME"),
        ({"sent_at": ...}, "MISSING_FIELD"),
        ({"v": ...}, "MISSING_FIELD"),
    ],
)
def test_a_bad_hello_is_refused_with_its_code(changes: dict[str, Any], code: str) -> None:
    rig = Rig()
    assert code_of(rig.send(hello(**changes))) == code
    assert not rig.session.hello_done


def test_a_second_hello_is_refused_and_changes_nothing() -> None:
    rig = Rig()
    rig.send(hello())
    reply = rig.send(hello(device="SN-PO-0002"))
    assert code_of(reply) == "BAD_FIELD" and rig.session.device == DEVICE


@pytest.mark.parametrize("make", [measurement, heartbeat], ids=["MEASUREMENT", "HEARTBEAT"])
def test_any_frame_before_hello_is_hello_first(make: Any) -> None:
    rig = Rig()
    assert code_of(rig.send(make())) == "HELLO_FIRST"
    assert not rig.session.hello_done


def test_ack_and_error_frames_before_hello_are_hello_first_too() -> None:
    for kind in ("ACK", "ERROR"):
        assert code_of(Rig().send({"v": 1, "type": kind, "sent_at": at()})) == "HELLO_FIRST"


# ---- common fields (P-05, P-06, P-07) ------------------------------------------------------


def test_a_missing_field_is_named_in_the_error() -> None:
    rig = Rig()
    rig.connect()
    reply = rig.send(measurement(unit=...))
    assert code_of(reply) == "MISSING_FIELD" and "unit" in reply.messages[0]["detail"]
    assert reply.messages[0]["seq"] == 1  # the error says which reading it is about


def test_an_unknown_type_is_refused_and_the_connection_stays_open() -> None:
    rig = Rig()
    rig.connect()
    reply = rig.send({"v": 1, "type": "SELFDESTRUCT", "sent_at": at()})
    assert code_of(reply) == "UNKNOWN_TYPE" and not reply.close


def test_unknown_fields_are_ignored() -> None:
    rig = Rig()
    rig.connect()
    reply = rig.send(measurement(firmware="9.9", extra={"a": [1]}))
    assert reply.messages[0]["status"] == "STORED"


@pytest.mark.parametrize("bad_v", [2, 0, "1", None, 1.5, 1.0, True, [1]])
def test_a_frame_with_another_version_is_refused_for_that_frame_only(bad_v: Any) -> None:
    rig = Rig()
    rig.connect()
    reply = rig.send(measurement(v=bad_v))
    assert code_of(reply) == "UNSUPPORTED_VERSION" and not reply.close
    assert rig.send(measurement(seq=2)).messages[0]["status"] == "STORED"


@pytest.mark.parametrize(
    "text",
    ["2026-11-12T18:52:03Z", "2026-11-12T18:52:03.000", "2026-11-12T20:52:03.000+02:00", "now", 5],
)
def test_a_sent_at_that_is_not_utc_with_milliseconds_is_bad_time(text: Any) -> None:
    rig = Rig()
    rig.connect()
    assert code_of(rig.send(heartbeat(sent_at=text))) == "BAD_TIME"


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'{"a":NaN}', b'{"v":1,"v":1}', b"\xff", b'{"a":1e999}'],
)
def test_bad_json_is_answered_and_the_connection_stays_open(body: bytes) -> None:
    rig = Rig()
    rig.connect()
    reply = rig.send(body)
    assert code_of(reply) == "BAD_JSON" and not reply.close
    assert rig.send(measurement()).messages[0]["status"] == "STORED"


# ---- pairing (P-09) ------------------------------------------------------------------------


def test_an_unpaired_sensor_is_told_once_and_then_paired_when_the_backend_says_so() -> None:
    rig = Rig(paired=False)
    rig.send(hello())
    first = run(rig.session.check_pairing())
    assert [code_of_message for code_of_message in (m["code"] for m in first)] == ["NOT_PAIRED"]
    assert run(rig.session.check_pairing()) == []  # not repeated every 5 s
    rig.pairing.pairs[DEVICE] = PAIRING
    (pair,) = run(rig.session.check_pairing())
    assert (pair["type"], pair["status"], pair["pairing"]) == ("PAIR", "PAIRED", PAIRING)
    assert rig.session.paired
    assert run(rig.session.check_pairing()) == []  # PAIR is sent once


def test_a_reading_before_pair_is_refused_not_paired_with_its_seq() -> None:
    rig = Rig(paired=False)
    rig.send(hello())
    run(rig.session.check_pairing())
    reply = rig.send(measurement(seq=9))
    assert code_of(reply) == "NOT_PAIRED" and reply.messages[0]["seq"] == 9
    assert rig.buffer.readings == {}  # nothing was kept


def test_the_pairing_question_names_this_monitor_and_sensor() -> None:
    asked: list[tuple[str, str]] = []

    class Spy:
        async def pairing_of(self, device: str, monitor: str) -> str | None:
            asked.append((device, monitor))
            return None

    rig = Rig()
    rig.session.pairing = Spy()
    rig.send(hello())
    run(rig.session.check_pairing())
    assert asked == [(DEVICE, MONITOR)]


# ---- MEASUREMENT (P-11, P-12, P-14) --------------------------------------------------------


def connected() -> Rig:
    rig = Rig()
    rig.connect()
    return rig


def test_a_valid_reading_is_stored_then_acknowledged() -> None:
    rig = connected()
    reply = rig.send(measurement(seq=41))
    assert reply.messages == [
        {
            "v": 1,
            "type": "ACK",
            "ack": "MEASUREMENT",
            "seq": 41,
            "status": "STORED",
            "sent_at": at(),
        }
    ]
    assert 41 in rig.buffer.readings[DEVICE]  # P-12: the ACK says it is in the buffer


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"device": "SN-PO-0009"}, "WRONG_DEVICE"),
        ({"device": ...}, "MISSING_FIELD"),
        ({"seq": ...}, "MISSING_FIELD"),
        ({"parameter": ...}, "MISSING_FIELD"),
        ({"value": ...}, "MISSING_FIELD"),
        ({"unit": ...}, "MISSING_FIELD"),
        ({"measured_at": ...}, "MISSING_FIELD"),
        ({"seq": 0}, "BAD_FIELD"),
        ({"seq": -3}, "BAD_FIELD"),
        ({"seq": 1.5}, "BAD_FIELD"),
        ({"seq": "1"}, "BAD_FIELD"),
        ({"seq": True}, "BAD_FIELD"),
        ({"parameter": "RR"}, "BAD_FIELD"),  # a pulse oximeter did not say it sends RR
        ({"parameter": "TEMP"}, "BAD_FIELD"),
        ({"parameter": ["PR"]}, "BAD_FIELD"),
        ({"unit": "bpm"}, "BAD_UNIT"),
        ({"unit": "%"}, "BAD_UNIT"),
        ({"unit": None}, "BAD_UNIT"),
        ({"value": "72"}, "BAD_FIELD"),
        ({"value": None}, "BAD_FIELD"),
        ({"value": True}, "BAD_FIELD"),
        ({"value": [72]}, "BAD_FIELD"),
        ({"value": -1}, "OUT_OF_RANGE"),
        ({"value": 301}, "OUT_OF_RANGE"),
        ({"value": 300.05}, "OUT_OF_RANGE"),  # rounds to 300.1, as the database would store it
        ({"value": 10**30}, "OUT_OF_RANGE"),
        ({"measured_at": "2026-11-12T18:52:03Z"}, "BAD_TIME"),
        ({"measured_at": "2026-11-12T18:52:03.000+00:00"}, "BAD_TIME"),
        ({"measured_at": "2026-02-30T18:52:03.000Z"}, "BAD_TIME"),
        ({"measured_at": at(61)}, "BAD_TIME"),
        ({"measured_at": at(-86400.001)}, "BAD_TIME"),
    ],
)
def test_a_bad_reading_is_refused_with_its_code_and_not_kept(
    changes: dict[str, Any], code: str
) -> None:
    rig = connected()
    reply = rig.send(measurement(**changes))
    assert code_of(reply) == code and not reply.close
    assert rig.buffer.readings.get(DEVICE, {}) == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"measured_at": at(60)},  # the limit itself (REQ-MEA-05)
        {"measured_at": at(-86400)},
        {"value": 0},
        {"value": 300},
        {"value": 72.04},
    ],
)
def test_the_limits_themselves_are_accepted(changes: dict[str, Any]) -> None:
    assert connected().send(measurement(**changes)).messages[0]["status"] == "STORED"


@pytest.mark.parametrize(
    ("parameter", "unit", "value", "code"),
    [
        ("SPO2", "%", 100, None),
        ("SPO2", "%", 101, "OUT_OF_RANGE"),
        ("SPO2", "%", 97.5, "OUT_OF_RANGE"),  # a whole number only
        ("SPO2", "beats/min", 97, "BAD_UNIT"),
        ("PR", "beats/min", 0, None),
    ],
)
def test_each_parameter_has_its_own_unit_and_range(
    parameter: str, unit: str, value: float, code: str | None
) -> None:
    rig = connected()
    reply = rig.send(measurement(parameter=parameter, unit=unit, value=value))
    assert code_of(reply) == code


def test_a_respiration_sensor_may_send_rr_and_nothing_else() -> None:
    rig = Rig()
    rig.pairing.pairs["SN-RR-0001"] = "rr-1"
    rig.send(hello(device="SN-RR-0001", device_type="RESPIRATION", parameters=["RR"]))
    run(rig.session.check_pairing())
    ok = rig.send(measurement(device="SN-RR-0001", parameter="RR", unit="breaths/min", value=16))
    assert ok.messages[0]["status"] == "STORED"
    assert code_of(rig.send(measurement(seq=2, device="SN-RR-0001"))) == "BAD_FIELD"  # PR


# ---- duplicates, conflicts, order and gaps (P-14) ------------------------------------------


def test_the_same_reading_again_is_a_duplicate_stored_once() -> None:
    rig = connected()
    rig.send(measurement(seq=5))
    again = rig.send(measurement(seq=5, sent_at=at(1)))  # only sent_at differs: a resend
    assert again.messages[0]["status"] == "DUPLICATE"
    assert len(rig.buffer.readings[DEVICE]) == 1


def test_the_same_value_written_another_way_is_still_a_duplicate() -> None:
    rig = connected()
    rig.send(measurement(seq=5, value=72))
    assert rig.send(measurement(seq=5, value=72.0)).messages[0]["status"] == "DUPLICATE"
    assert rig.send(measurement(seq=5, value=72.04)).messages[0]["status"] == "DUPLICATE"


@pytest.mark.parametrize(
    "changes",
    [{"value": 73}, {"measured_at": at(-1)}, {"parameter": "SPO2", "unit": "%", "value": 72}],
)
def test_the_same_seq_with_another_reading_is_a_conflict_and_the_first_is_kept(
    changes: dict[str, Any],
) -> None:
    rig = connected()
    rig.send(measurement(seq=5))
    reply = rig.send(measurement(seq=5, **changes))
    assert code_of(reply) == "SEQ_CONFLICT" and reply.messages[0]["seq"] == 5
    assert rig.buffer.readings[DEVICE][5].value == 72 and rig.buffer.conflicts == 1


def test_a_gap_of_one_missing_reading_is_counted() -> None:
    rig = connected()
    rig.send(measurement(seq=1))
    rig.send(measurement(seq=2))
    assert rig.buffer.gaps == 0  # consecutive: no gap
    rig.send(measurement(seq=4))  # seq 3 never came
    assert rig.buffer.gaps == 1


def test_pinned_a_late_reading_is_counted_as_a_gap_when_its_successor_came_first() -> None:
    # PINNED, known limitation (docs/monitor.md): the monitor judges a gap when a reading arrives,
    # and cannot know that the missing one is only late. Nothing was lost here, yet gaps is 1.
    rig = connected()
    for seq in (2, 1):
        rig.send(measurement(seq=seq))
    assert sorted(rig.buffer.readings[DEVICE]) == [1, 2]
    assert rig.buffer.gaps == 1


def test_readings_may_arrive_in_any_order_and_all_are_kept() -> None:
    rig = connected()
    for seq in (3, 1, 2, 5, 4):
        assert rig.send(measurement(seq=seq)).messages[0]["status"] == "STORED"
    assert sorted(rig.buffer.readings[DEVICE]) == [1, 2, 3, 4, 5]


def test_a_gap_is_counted_and_logged_but_not_refused(caplog: pytest.LogCaptureFixture) -> None:
    rig = connected()
    with caplog.at_level(logging.INFO):
        rig.send(measurement(seq=1))
        reply = rig.send(measurement(seq=4))
    assert reply.messages[0]["status"] == "STORED" and rig.buffer.gaps == 1
    assert any(r.getMessage() == "seq_gap" for r in caplog.records)


def test_two_sensors_may_use_the_same_seq() -> None:
    rig = connected()
    rig.send(measurement(seq=1))
    other = Rig()
    other.buffer = rig.buffer
    other.session.buffer = rig.buffer
    other.pairing.pairs["SN-PO-0002"] = "pr-2"
    other.send(hello(device="SN-PO-0002"))
    run(other.session.check_pairing())
    assert other.send(measurement(seq=1, device="SN-PO-0002")).messages[0]["status"] == "STORED"


# ---- HEARTBEAT, ACK, ERROR from the sensor -------------------------------------------------


@pytest.mark.parametrize("status", ["OK", "NO_SIGNAL"])
def test_a_heartbeat_gets_no_reply(status: str) -> None:
    rig = connected()
    assert rig.send(heartbeat(status=status)).messages == []


@pytest.mark.parametrize("status", ["FINE", None, 1])
def test_a_heartbeat_with_another_status_is_bad_field(status: Any) -> None:
    assert code_of(connected().send(heartbeat(status=status))) == "BAD_FIELD"
    assert code_of(connected().send(heartbeat(status=...))) == "BAD_FIELD"


def test_an_ack_from_the_sensor_is_accepted_silently() -> None:
    reply = connected().send({"v": 1, "type": "ACK", "ack": "PAIR", "sent_at": at()})
    assert reply.messages == [] and not reply.close


def test_an_error_from_the_sensor_is_logged_not_answered(caplog: pytest.LogCaptureFixture) -> None:
    rig = connected()
    with caplog.at_level(logging.WARNING):
        reply = rig.send({"v": 1, "type": "ERROR", "code": "BAD_JSON", "sent_at": at()})
    assert reply.messages == []  # two ERRORs must never ping-pong
    assert any(r.getMessage() == "sensor_error" for r in caplog.records)


def test_a_pair_frame_from_a_sensor_is_refused() -> None:
    reply = connected().send({"v": 1, "type": "PAIR", "status": "PAIRED", "sent_at": at()})
    assert code_of(reply) == "UNKNOWN_TYPE"


# ---- what the monitor sends --------------------------------------------------------------


def test_every_frame_the_monitor_sends_is_well_formed() -> None:
    rig = Rig(paired=False)
    sent = [*rig.send(hello()).messages, *run(rig.session.check_pairing())]
    rig.pairing.pairs[DEVICE] = PAIRING
    sent += run(rig.session.check_pairing())
    sent += rig.send(measurement()).messages + rig.send(b"nope").messages
    sent += [rig.session.heartbeat()]
    for message in sent:
        assert message["v"] == 1 and parse_wire_time(message["sent_at"]) is not None, message
        if message["type"] == "ERROR":
            assert message["code"] in PROTOCOL_CODES and message["detail"]


def test_a_reply_uses_the_wall_clock_of_the_monitor() -> None:
    rig = connected()
    rig.clock.current = rig.clock.current.replace(second=30)
    assert rig.session.heartbeat()["sent_at"] == "2026-11-12T18:52:30.000Z"


# ---- structured logs -----------------------------------------------------------------------


def test_a_hostile_device_name_cannot_forge_a_log_line() -> None:
    from pulseguard.monitor.logs import JsonFormatter, log_event

    record_log = logging.getLogger("t.forge")
    lines: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(self.format(record))

    handler = Capture()
    handler.setFormatter(JsonFormatter())
    record_log.addHandler(handler)
    record_log.setLevel(logging.INFO)
    evil = 'SN-1\n{"event":"paired","device":"SN-2"}' + "x" * 1000
    log_event(record_log, "hello", device=evil)
    (line,) = lines
    assert "\n" not in line
    entry = json.loads(line)
    assert entry["event"] == "hello" and len(entry["device"]) == MAX_TEXT


def test_logs_are_json_lines_with_an_event_and_fields() -> None:
    from pulseguard.monitor.logs import JsonFormatter

    record = logging.LogRecord("x", logging.WARNING, "f", 1, "frame_refused", None, None)
    record.fields = {"code": "BAD_JSON", "device": None}
    entry = json.loads(JsonFormatter().format(record))
    assert entry["event"] == "frame_refused" and entry["level"] == "WARNING"
    assert entry["code"] == "BAD_JSON" and entry["ts"].endswith("+00:00")


def test_raw_helper_builds_what_the_session_reads() -> None:
    assert json.loads(raw(hello()))["type"] == "HELLO"
