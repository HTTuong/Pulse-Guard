"""The monitor over real TCP connections on localhost, with short timers.

Each test starts its own server on a free port. Timers are tens to hundreds of milliseconds and
the assertions leave a wide margin, so a slow machine does not make them fail (labs/README.md).
"""

import asyncio
import json
import logging
import struct
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import Any

import pytest

from pulseguard.monitor.buffer import Entry, MemoryBuffer, StoreResult
from pulseguard.monitor.pairing import StaticPairing
from pulseguard.monitor.server import MonitorConfig, MonitorServer
from pulseguard.protocol.codec import decode_object
from pulseguard.protocol.framing import FrameDecoder, encode_frame
from pulseguard.simulator.client import stream
from pulseguard.simulator.generator import SensorSimulator
from pulseguard.simulator.scenarios import scenario_named
from tests.conftest import FakeClock
from tests.monitor.helpers import DEVICE, NOW, PAIRING, heartbeat, hello, measurement

FAST = MonitorConfig(
    serial="MON-TEST", hello_s=0.4, frame_s=0.4, heartbeat_s=0.1, silent_s=0.6, pair_check_s=0.1
)


class Client:
    """A bare TCP client: it sends exactly the bytes a test gives it."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer = reader, writer
        self.decoder = FrameDecoder()
        self.queue: list[dict[str, Any]] = []
        self.eof = False

    async def send(self, message: dict[str, Any]) -> None:
        await self.send_bytes(encode_frame(message))

    async def send_bytes(self, data: bytes) -> None:
        self.writer.write(data)
        await self.writer.drain()

    async def _fill(self, limit: float) -> bool:
        try:
            async with asyncio.timeout(limit):
                data = await self.reader.read(4096)
        except TimeoutError:
            return False
        except (ConnectionError, OSError):  # a reset after the server closed counts as the end
            self.eof = True
            return False
        if not data:
            self.eof = True
            return False
        frames, _ = self.decoder.feed(data)
        self.queue += [decode_object(f) for f in frames]
        return True

    async def recv(self, limit: float = 2.0) -> dict[str, Any]:
        """The next frame; fails the test when none arrives in time."""
        deadline = time.monotonic() + limit
        while not self.queue:
            remaining = deadline - time.monotonic()
            assert remaining > 0 and not self.eof, "no frame arrived"
            await self._fill(remaining)
        return self.queue.pop(0)

    async def recv_type(self, kind: str, limit: float = 2.0) -> dict[str, Any]:
        while True:
            message = await self.recv(limit)
            if message["type"] == kind:
                return message

    async def closed_within(self, limit: float) -> float | None:
        """Seconds until the server closed the connection, or None if it stayed open."""
        started = time.monotonic()
        while time.monotonic() - started < limit:
            if self.eof:
                return time.monotonic() - started
            await self._fill(limit - (time.monotonic() - started))
        return time.monotonic() - started if self.eof else None

    async def connect_and_pair(self) -> None:
        await self.send(hello())
        assert (await self.recv())["ack"] == "HELLO"
        assert (await self.recv_type("PAIR"))["status"] == "PAIRED"


class Rig:
    def __init__(self, server: MonitorServer, port: int, clock: FakeClock) -> None:
        self.server, self.port, self.clock = server, port, clock

    @asynccontextmanager
    async def client(self) -> AsyncIterator[Client]:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        client = Client(reader, writer)
        try:
            yield client
        finally:
            writer.close()
            with suppress(ConnectionError, OSError):
                await writer.wait_closed()


@asynccontextmanager
async def running(
    pairs: dict[str, str] | None = None,
    config: MonitorConfig = FAST,
    buffer: MemoryBuffer | None = None,
) -> AsyncIterator[Rig]:
    clock = FakeClock(NOW)
    server = MonitorServer(
        clock,
        StaticPairing({DEVICE: PAIRING} if pairs is None else pairs),
        buffer or MemoryBuffer(),
        config,
    )
    port = await server.start()
    try:
        yield Rig(server, port, clock)
    finally:
        await server.stop()


def scenario(test: Callable[[Rig], Any], **kwargs: Any) -> None:
    async def go() -> None:
        async with running(**kwargs) as rig:
            await test(rig)

    asyncio.run(asyncio.wait_for(go(), timeout=30))  # a hung test fails instead of hanging the run


# ---- the whole path ------------------------------------------------------------------------


def test_the_simulator_streams_a_scenario_and_every_reading_is_acknowledged_and_kept() -> None:
    async def test(rig: Rig) -> None:
        sim = SensorSimulator(DEVICE, "PULSE_OXIMETER", scenario_named("normal"), 1, NOW)
        replies = await stream("127.0.0.1", rig.port, sim, 30, rig.clock)
        acks = [m for m in replies if m["type"] == "ACK" and m["ack"] == "MEASUREMENT"]
        assert [m["seq"] for m in acks] == list(range(1, 61))
        assert {m["status"] for m in acks} == {"STORED"}
        assert sorted(rig.server.buffer.readings[DEVICE]) == list(range(1, 61))
        assert replies[0]["ack"] == "HELLO" and replies[1]["type"] == "PAIR"

    scenario(test)


def test_the_sensor_off_scenario_leaves_no_gap_for_the_monitor_to_count() -> None:
    async def test(rig: Rig) -> None:
        # Started 70 s ago, so that no reading is more than 60 s ahead of the monitor's clock.
        start = NOW - timedelta(seconds=70)
        sim = SensorSimulator(DEVICE, "PULSE_OXIMETER", scenario_named("sensor_off"), 1, start)
        await stream("127.0.0.1", rig.port, sim, 70, rig.clock)
        assert rig.server.buffer.gaps == 0 and len(rig.server.buffer.readings[DEVICE]) == 80

    scenario(test)


def test_a_resend_is_a_duplicate_and_a_changed_reading_is_a_conflict_over_tcp() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            await c.send(measurement(seq=1))
            assert (await c.recv_type("ACK"))["status"] == "STORED"
            await c.send(measurement(seq=1))
            assert (await c.recv_type("ACK"))["status"] == "DUPLICATE"
            await c.send(measurement(seq=1, value=99))
            error = await c.recv_type("ERROR")
            assert (error["code"], error["seq"]) == ("SEQ_CONFLICT", 1)
            assert await c.closed_within(0.3) is None  # a conflict does not close the connection

    scenario(test)


# ---- framing on a real stream (P-01 to P-04) -----------------------------------------------


def test_a_frame_sent_one_byte_at_a_time_is_read() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            for byte in encode_frame(hello()):
                await c.send_bytes(bytes([byte]))
                await asyncio.sleep(0.005)
            assert (await c.recv())["ack"] == "HELLO"

    # Sending takes about 0.7 s, longer than hello_s of FAST: that timer is not what is tested here.
    scenario(
        test,
        config=MonitorConfig(**{**FAST.__dict__, "hello_s": 5.0, "frame_s": 5.0, "silent_s": 5.0}),
    )


def test_several_frames_in_one_write_are_all_answered() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            burst = b"".join(encode_frame(measurement(seq=n)) for n in range(1, 11))
            await c.send_bytes(burst)
            acks = [await c.recv_type("ACK") for _ in range(10)]
            assert [a["seq"] for a in acks] == list(range(1, 11))

    scenario(test)


@pytest.mark.parametrize("length", [0, 4097, 2**32 - 1])
def test_a_bad_length_gets_an_error_and_the_connection_is_closed(length: int) -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            await c.send_bytes(struct.pack(">I", length))  # nothing follows: the header is enough
            assert (await c.recv_type("ERROR"))["code"] == "BAD_LENGTH"
            assert await c.closed_within(1.0) is not None
        assert rig.server.closed["bad_length"] == 1

    scenario(test)


def test_bad_json_gets_an_error_and_the_connection_stays_usable() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            for body in (b"not json", b'{"a":NaN}', b'{"v":1,"v":1}', b"[1]"):
                await c.send_bytes(struct.pack(">I", len(body)) + body)
                assert (await c.recv_type("ERROR"))["code"] == "BAD_JSON"
            await c.send(measurement(seq=1))
            assert (await c.recv_type("ACK"))["status"] == "STORED"

    scenario(test)


def test_a_flood_of_bad_frames_is_refused_one_by_one_without_stopping_the_loop() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            await c.send_bytes((struct.pack(">I", 8) + b"garbage!") * 200)
            errors = [await c.recv_type("ERROR") for _ in range(200)]
            assert {e["code"] for e in errors} == {"BAD_JSON"}

    scenario(test)


# ---- timers (P-03, P-08, P-13, timeouts table) ---------------------------------------------


def test_no_hello_within_the_limit_closes_the_connection() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            took = await c.closed_within(3.0)
            assert took is not None and 0.3 <= took < 2.0
        assert rig.server.closed["hello_timeout"] == 1

    scenario(test)


def test_another_frame_before_hello_does_not_start_the_session_or_stop_the_hello_timer() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.send(heartbeat())
            assert (await c.recv())["code"] == "HELLO_FIRST"
            assert await c.closed_within(3.0) is not None  # still closed by the hello timer
        assert rig.server.closed["hello_timeout"] == 1

    scenario(test)


def test_a_frame_that_stays_incomplete_closes_the_connection() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            await c.send_bytes(encode_frame(heartbeat())[:7])  # header and 3 bytes of the body
            took = await c.closed_within(3.0)
            assert took is not None and 0.3 <= took < 2.0
        assert rig.server.closed["incomplete_frame"] == 1

    scenario(test, config=MonitorConfig(**{**FAST.__dict__, "silent_s": 30.0}))


def test_a_slow_but_steady_sender_is_not_closed_while_each_frame_completes_in_time() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            for n in range(1, 4):
                data = encode_frame(measurement(seq=n))
                await c.send_bytes(data[:10])
                await asyncio.sleep(0.2)  # under frame_s
                await c.send_bytes(data[10:])
                assert (await c.recv_type("ACK"))["seq"] == n
            assert not c.eof

    scenario(test, config=MonitorConfig(**{**FAST.__dict__, "silent_s": 30.0}))


def test_a_frame_that_is_completed_late_gets_a_fresh_deadline_for_the_next_one() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            a, b = encode_frame(measurement(seq=1)), encode_frame(measurement(seq=2))
            await c.send_bytes(a[:10])  # t = 0: frame A starts
            await asyncio.sleep(0.4)
            await c.send_bytes(a[10:] + b[:10])  # A is complete; B starts now, not at t = 0
            await asyncio.sleep(0.4)  # t = 0.8: more than 0.6 s after A started
            await c.send_bytes(b[10:])
            assert (await c.recv_type("ACK"))["seq"] == 1
            assert (await c.recv_type("ACK"))["seq"] == 2
            assert not c.eof

    scenario(test, config=MonitorConfig(**{**FAST.__dict__, "frame_s": 0.6, "silent_s": 30.0}))


def test_bytes_alone_do_not_count_as_a_frame_for_the_silence_timer() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            data = encode_frame(measurement())
            started = time.monotonic()
            # A byte every 0.1 s: never quiet, yet no frame is ever complete. The monitor closes
            # after silent_s (0.6 s); the writes that come after that fail, which is expected.
            with suppress(ConnectionError, OSError):
                for byte in data[:20]:
                    await c.send_bytes(bytes([byte]))
                    await asyncio.sleep(0.1)
            assert await c.closed_within(2.0) is not None
            assert time.monotonic() - started < 1.8  # it did not wait for a frame that never ends
        assert rig.server.closed == {"silent": 1}

    scenario(test, config=MonitorConfig(**{**FAST.__dict__, "frame_s": 30.0}))


def test_a_hello_with_no_common_version_gets_an_error_and_the_connection_closes() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.send(hello(versions=[2]))
            assert (await c.recv())["code"] == "UNSUPPORTED_VERSION"
            assert await c.closed_within(1.0) is not None
        assert rig.server.closed["unsupported_version"] == 1

    scenario(test)


def test_a_frame_with_another_version_after_hello_does_not_close_the_connection() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            await c.send(measurement(v=2))
            assert (await c.recv_type("ERROR"))["code"] == "UNSUPPORTED_VERSION"
            await c.send(measurement(seq=1))
            assert (await c.recv_type("ACK"))["status"] == "STORED"

    scenario(test)


def test_a_silent_connection_is_closed_even_though_the_monitor_keeps_sending_heartbeats() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            took = await c.closed_within(3.0)
            assert took is not None and 0.4 <= took < 2.0
            assert any(m["type"] == "HEARTBEAT" for m in c.queue)  # its own frames do not count
        assert rig.server.closed["silent"] == 1

    scenario(test)


def test_heartbeats_from_the_sensor_keep_the_connection_open() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            for _ in range(10):  # 1.5 s, more than twice silent_s
                await c.send(heartbeat(status="NO_SIGNAL"))
                await asyncio.sleep(0.15)
            assert not c.eof

    scenario(test)


def test_the_monitor_sends_a_heartbeat_every_interval_even_when_nothing_else_is_sent() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.send(hello())
            beats = [await c.recv_type("HEARTBEAT") for _ in range(4)]
            assert all(b["status"] == "OK" for b in beats)

    scenario(test, config=MonitorConfig(**{**FAST.__dict__, "silent_s": 30.0}))


def test_a_change_of_the_wall_clock_does_not_fire_or_stop_a_timer() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            rig.clock.current = rig.clock.current.replace(year=2030)  # the wall clock jumps
            for _ in range(8):  # 1.2 s: a timer on the wall clock would have fired by now
                await c.send(heartbeat(sent_at="2030-11-12T18:52:03.000Z"))
                await asyncio.sleep(0.15)
            assert not c.eof
            beat = await c.recv_type("HEARTBEAT")
            assert beat["sent_at"].startswith("2030")  # messages use the wall clock (P-18)
            await c.send(measurement(seq=1))  # judged against the new wall clock
            assert (await c.recv_type("ERROR"))["code"] == "BAD_TIME"

    scenario(test)


# ---- pairing over TCP (P-09) ---------------------------------------------------------------


def test_an_unpaired_sensor_gets_not_paired_and_is_paired_when_the_backend_says_so() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.send(hello())
            assert (await c.recv())["ack"] == "HELLO"
            assert (await c.recv_type("ERROR"))["code"] == "NOT_PAIRED"
            await c.send(measurement(seq=1))
            refused = await c.recv_type("ERROR")
            assert (refused["code"], refused["seq"]) == ("NOT_PAIRED", 1)
            assert rig.server.buffer.readings == {}
            rig.server.pairing.pairs[DEVICE] = PAIRING  # type: ignore[attr-defined]
            assert (await c.recv_type("PAIR"))["pairing"] == PAIRING
            await c.send(measurement(seq=2))
            assert (await c.recv_type("ACK"))["status"] == "STORED"

    scenario(test, pairs={})


# ---- several connections -------------------------------------------------------------------


def test_many_sensors_at_once_each_get_their_own_answers() -> None:
    async def one(rig: Rig, n: int) -> int:
        device = f"SN-PO-{n:04d}"
        sim = SensorSimulator(device, "PULSE_OXIMETER", scenario_named("normal"), n, NOW)
        replies = await stream("127.0.0.1", rig.port, sim, 10, rig.clock)
        return sum(1 for m in replies if m["type"] == "ACK" and m.get("ack") == "MEASUREMENT")

    async def test(rig: Rig) -> None:
        results = await asyncio.gather(*(one(rig, n) for n in range(1, 21)))
        assert results == [20] * 20
        assert len(rig.server.buffer.readings) == 20

    scenario(test, pairs={f"SN-PO-{n:04d}": f"p-{n}" for n in range(1, 21)})


def test_a_stalled_or_broken_connection_does_not_disturb_the_others() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as stalled, rig.client() as vandal, rig.client() as good:
            await stalled.send_bytes(b"\x00\x00")  # half a header, then nothing
            await vandal.send_bytes(struct.pack(">I", 0))
            await good.connect_and_pair()
            for n in range(1, 21):
                await good.send(measurement(seq=n))
                assert (await good.recv_type("ACK"))["seq"] == n
            assert await vandal.closed_within(1.0) is not None

    scenario(test, config=MonitorConfig(**{**FAST.__dict__, "silent_s": 30.0, "frame_s": 30.0}))


def test_a_peer_that_vanishes_in_the_middle_of_a_frame_is_cleaned_up() -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            await c.send_bytes(encode_frame(measurement())[:9])
        await asyncio.sleep(0.3)
        assert rig.server.handlers == set()
        assert rig.server.closed == {"peer_closed": 1}
        async with rig.client() as again:  # the server still serves
            await again.connect_and_pair()

    scenario(test, config=MonitorConfig(**{**FAST.__dict__, "silent_s": 30.0, "frame_s": 30.0}))


# ---- not acknowledging what was not stored (P-12) ------------------------------------------


class FailingBuffer(MemoryBuffer):
    def store(self, device: str, seq: int, entry: Entry) -> tuple[StoreResult, bool]:
        raise OSError("disk full")


def test_when_the_buffer_fails_there_is_no_ack_and_the_other_connections_go_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            await c.send(measurement(seq=1))
            assert await c.closed_within(2.0) is not None
            assert not any(m["type"] == "ACK" and m.get("ack") == "MEASUREMENT" for m in c.queue)
        async with rig.client() as other:
            await other.connect_and_pair()  # the server is still up

    with caplog.at_level(logging.INFO):
        scenario(test, buffer=FailingBuffer())
    assert any(r.getMessage() == "connection_error" for r in caplog.records)


# ---- shutdown (labs/asyncio_echo) ----------------------------------------------------------


def test_stop_closes_every_connection_and_each_one_cleans_up() -> None:
    async def go() -> None:
        clock = FakeClock(NOW)
        server = MonitorServer(clock, StaticPairing({DEVICE: PAIRING}), config=FAST)
        port = await server.start()
        rig = Rig(server, port, clock)
        async with rig.client() as a, rig.client() as b:
            await a.connect_and_pair()
            await b.send_bytes(b"\x00")  # a connection in the middle of a frame
            await asyncio.sleep(0.05)
            assert len(server.handlers) == 2
            tasks = set(server.handlers)
            await asyncio.wait_for(server.stop(), timeout=5)
            assert all(t.cancelled() for t in tasks)  # cancellation was passed on, not swallowed
            assert server.handlers == set()
            assert server.closed == {"shutdown": 2}
            assert await a.closed_within(1.0) is not None
            assert await b.closed_within(1.0) is not None

    asyncio.run(asyncio.wait_for(go(), timeout=30))


# ---- structured logs -----------------------------------------------------------------------


def test_a_connection_leaves_a_trail_of_events_with_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            await c.send(measurement(seq=1))
            await c.recv_type("ACK")
            await c.send_bytes(struct.pack(">I", 5) + b"\xff\xff\xff\xff\xff")
            await c.recv_type("ERROR")
        await asyncio.sleep(0.2)

    with caplog.at_level(logging.INFO):
        scenario(test, config=MonitorConfig(**{**FAST.__dict__, "silent_s": 30.0}))
    events = [r.getMessage() for r in caplog.records if r.name.startswith("pulseguard.monitor")]
    for expected in (
        "listening", "connection_open", "hello", "paired", "measurement", "frame_refused",
        "connection_closed",
    ):  # fmt: skip
        assert expected in events, events
    refused = next(r for r in caplog.records if r.getMessage() == "frame_refused")
    assert refused.fields["code"] == "BAD_JSON"  # type: ignore[attr-defined]
    closed = next(r for r in caplog.records if r.getMessage() == "connection_closed")
    assert closed.fields["reason"] == "peer_closed" and closed.fields["device"] == DEVICE  # type: ignore[attr-defined]
    json.dumps([r.fields for r in caplog.records if hasattr(r, "fields")], default=str)
