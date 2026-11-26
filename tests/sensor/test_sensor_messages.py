"""How the sensor client reacts to each message of the monitor, without a connection."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest

from pulseguard.clock import to_wire
from pulseguard.simulator.generator import Reading, SensorSimulator
from pulseguard.simulator.scenarios import scenario_named
from pulseguard.simulator.sensor import SensorClient
from tests.conftest import FakeClock
from tests.monitor.helpers import DEVICE, NOW


class Sent:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def __call__(self, messages: list[dict[str, Any]]) -> None:
        self.frames += messages


def sensor_with(count: int) -> SensorClient:
    sim = SensorSimulator(DEVICE, "PULSE_OXIMETER", scenario_named("normal"), 1, NOW)
    client = SensorClient("127.0.0.1", 1, sim, FakeClock(NOW))
    for seq in range(1, count + 1):
        client.outbox.add(Reading(seq, "PR", 72, "beats/min", NOW + timedelta(seconds=seq)))
    client.outbox.due(0)  # all sent, all waiting
    return client


def feed(client: SensorClient, message: dict[str, Any]) -> Sent:
    sent = Sent()
    asyncio.run(client._on_message(message, sent))
    return sent


def test_an_ack_removes_the_reading_and_counts_its_status() -> None:
    client = sensor_with(3)
    feed(client, {"type": "ACK", "ack": "MEASUREMENT", "seq": 2, "status": "DUPLICATE"})
    assert client.outbox.seqs() == [1, 3] and client.answers == {"DUPLICATE": 1}


def test_an_error_with_a_seq_ends_the_resend_of_that_reading() -> None:
    client = sensor_with(3)
    feed(client, {"type": "ERROR", "code": "OUT_OF_RANGE", "seq": 1})
    assert client.outbox.seqs() == [2, 3] and client.refused == [(1, "OUT_OF_RANGE")]
    assert [r.seq for r in client.outbox.due(100)] == [2, 3]  # 1 is never sent again


def test_an_error_without_a_seq_removes_nothing() -> None:
    client = sensor_with(2)
    feed(client, {"type": "ERROR", "code": "NOT_PAIRED"})
    assert client.outbox.seqs() == [1, 2] and client.refused == []


@pytest.mark.parametrize("seq", [99, 0, -1, "1", None, 1.0, True, [1]])
def test_an_answer_for_a_seq_that_is_not_held_or_not_an_integer_removes_nothing(seq: Any) -> None:
    for kind in ({"type": "ACK", "ack": "MEASUREMENT", "status": "STORED"}, {"type": "ERROR"}):
        client = sensor_with(2)
        feed(client, {**kind, "seq": seq})
        assert client.outbox.seqs() == [1, 2], (kind, seq)


def test_a_repeated_ack_is_counted_once() -> None:
    client = sensor_with(1)
    for _ in range(3):
        feed(client, {"type": "ACK", "ack": "MEASUREMENT", "seq": 1, "status": "STORED"})
    assert client.answers == {"STORED": 1}


def test_pair_paired_starts_measuring_and_is_acknowledged_with_ack_pair() -> None:
    client = sensor_with(0)
    client.measuring = False
    sent = feed(client, {"type": "PAIR", "status": "PAIRED", "pairing": "pr-1"})
    assert client.paired and client.measuring
    assert [(f["type"], f["ack"]) for f in sent.frames] == [("ACK", "PAIR")]


def test_pair_unpaired_stops_measuring_and_drops_readings_measured_since() -> None:
    client = sensor_with(5)  # reading n was measured at NOW + n s
    client.paired = True
    sent = feed(
        client,
        {"type": "PAIR", "status": "UNPAIRED", "unpaired_at": to_wire(NOW + timedelta(seconds=3))},
    )
    assert not client.paired and not client.measuring
    assert client.outbox.seqs() == [1, 2] and client.discarded == 3  # 3, 4, 5: at or after
    assert [(f["type"], f["ack"]) for f in sent.frames] == [("ACK", "PAIR")]


def test_pair_with_an_unknown_status_is_ignored_without_an_ack() -> None:
    client = sensor_with(1)
    sent = feed(client, {"type": "PAIR", "status": "MAYBE"})
    assert sent.frames == [] and not client.paired


def test_other_messages_change_nothing() -> None:
    client = sensor_with(2)
    for message in ({"type": "HEARTBEAT", "status": "OK"}, {"type": "WHATEVER"}, {}):
        assert feed(client, message).frames == []
    assert client.outbox.seqs() == [1, 2]
