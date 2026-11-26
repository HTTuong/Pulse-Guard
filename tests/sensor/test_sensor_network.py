"""The sensor and the monitor over real TCP, with a proxy that cuts the connection or loses ACKs.

All timers are scaled down (tens of milliseconds); each assertion leaves a wide margin.
"""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest

from pulseguard.monitor.buffer import MemoryBuffer
from pulseguard.monitor.pairing import StaticPairing
from pulseguard.monitor.server import MonitorConfig, MonitorServer
from pulseguard.simulator.backoff import Backoff
from pulseguard.simulator.generator import SensorSimulator
from pulseguard.simulator.scenarios import Keyframe, Scenario, scenario_named
from pulseguard.simulator.sensor import SensorClient, SensorConfig
from tests.conftest import FakeClock
from tests.monitor.helpers import DEVICE, NOW, PAIRING
from tests.sensor.faultproxy import FaultProxy

MONITOR = MonitorConfig(
    serial="MON-TEST", hello_s=0.4, frame_s=0.4, heartbeat_s=0.1, silent_s=0.6, pair_check_s=0.1
)
SENSOR = SensorConfig(
    tick_s=0.02, heartbeat_s=0.1, silent_s=0.6, ack_s=0.15, connect_s=1.0, backoff_scale=0.02
)
CLOCK = FakeClock(NOW)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


async def serve(
    port: int = 0, pairs: dict[str, str] | None = None, buffer: MemoryBuffer | None = None
) -> tuple[MonitorServer, int]:
    server = MonitorServer(
        CLOCK,
        StaticPairing({DEVICE: PAIRING} if pairs is None else pairs),
        buffer or MemoryBuffer(),
        MONITOR,
    )
    return server, await server.start("127.0.0.1", port)


def make_sensor(
    port: int,
    ticks: int,
    config: SensorConfig = SENSOR,
    scenario: Scenario | None = None,
    seed: int = 1,
    **kwargs: Any,
) -> SensorClient:
    # One simulated 20 ms per tick, so measured_at stays near the monitor's fixed clock.
    sim = SensorSimulator(
        DEVICE, "PULSE_OXIMETER", scenario or scenario_named("normal"), seed, NOW,
        interval=timedelta(milliseconds=20),
    )  # fmt: skip
    return SensorClient("127.0.0.1", port, sim, CLOCK, seed, config, ticks, **kwargs)


@asynccontextmanager
async def running(sensor: SensorClient) -> AsyncIterator[SensorClient]:
    task = asyncio.create_task(sensor.run())
    try:
        yield sensor
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def until(condition: Any, limit: float = 10.0) -> None:
    async with asyncio.timeout(limit):
        while not condition():
            await asyncio.sleep(0.005)


def go(test: Any) -> None:
    asyncio.run(
        asyncio.wait_for(test(), timeout=60)
    )  # a hung test fails instead of hanging the run


def stored(server: MonitorServer) -> list[int]:
    return sorted(server.buffer.readings.get(DEVICE, {}))


# ---- the plain path ------------------------------------------------------------------------


def test_every_reading_is_delivered_once_and_acknowledged_on_a_quiet_network() -> None:
    async def test() -> None:
        server, port = await serve()
        try:
            async with running(make_sensor(port, ticks=100)) as sensor:
                await sensor.wait_drained(20)
            assert stored(server) == list(range(1, 201))
            assert dict(sensor.answers) == {"STORED": 200} and sensor.refused == []
            assert sensor.connections == 1 and sensor.delays == []
            assert server.buffer.gaps == 0 and server.buffer.conflicts == 0
        finally:
            await server.stop()

    go(test)


def test_the_wire_order_is_hello_then_pair_then_readings_and_the_sensor_acks_pair() -> None:
    async def test() -> None:
        server, mport = await serve()
        proxy = FaultProxy(mport)
        port = await proxy.start()
        try:
            async with running(make_sensor(port, ticks=20)) as sensor:
                await sensor.wait_drained(20)
            kinds = [(d, f["type"]) for d, f in proxy.timeline if f["type"] != "HEARTBEAT"]
            assert kinds[0] == ("to_monitor", "HELLO")
            first_reading = kinds.index(("to_monitor", "MEASUREMENT"))
            assert ("to_sensor", "PAIR") in kinds[:first_reading]  # P-09: nothing before PAIRED
            assert ("to_monitor", "ACK") in kinds  # the ACK of PAIR
            assert any(f.get("ack") == "PAIR" for f in proxy.sent("to_monitor", "ACK"))
        finally:
            await proxy.stop()
            await server.stop()

    go(test)


# ---- not paired (P-09) ---------------------------------------------------------------------


def test_a_sensor_that_is_not_paired_sends_no_reading_until_it_is_paired() -> None:
    async def test() -> None:
        server, mport = await serve(pairs={})
        proxy = FaultProxy(mport)
        port = await proxy.start()
        try:
            async with running(make_sensor(port, ticks=30)) as sensor:
                await asyncio.sleep(0.8)
                assert not sensor.paired and len(sensor.outbox) == 60  # measured, held, not sent
                assert proxy.sent("to_monitor", "MEASUREMENT") == [] and stored(server) == []
                assert [f["code"] for f in proxy.sent("to_sensor", "ERROR")] == ["NOT_PAIRED"]
                server.pairing.pair(DEVICE, PAIRING)  # type: ignore[attr-defined]
                await sensor.wait_drained(20)
            assert stored(server) == list(range(1, 61)) and sensor.refused == []
        finally:
            await proxy.stop()
            await server.stop()

    go(test)


# ---- reconnection (P-19, P-20) -------------------------------------------------------------


def test_with_no_monitor_the_sensor_retries_with_growing_waits_and_keeps_every_reading() -> None:
    async def test() -> None:
        port = free_port()
        async with running(make_sensor(port, ticks=60)) as sensor:
            await until(lambda: len(sensor.delays) >= 4)
            scaled = [1 * 0.02, 2 * 0.02, 4 * 0.02, 8 * 0.02]
            for got, base in zip(sensor.delays, scaled, strict=False):
                assert 0.9 * base <= got <= 1.1 * base
            server, _ = await serve(port=port)  # the monitor comes up late
            try:
                await sensor.wait_drained(20)
                assert stored(server) == list(range(1, 121))  # all readings, in order, none lost
                assert server.buffer.gaps == 0
            finally:
                await server.stop()

    go(test)


def test_the_waits_are_the_seeded_backoff_and_the_same_for_the_same_seed() -> None:
    async def one_run(seed: int) -> list[float]:
        recorded: list[float] = []
        gate = asyncio.Semaphore(7)

        async def sleeper(delay: float) -> None:
            recorded.append(delay)
            await gate.acquire()

        async with running(
            make_sensor(
                free_port(),
                ticks=0,
                seed=seed,
                config=SensorConfig(backoff_scale=1.0),
                sleep=sleeper,
            )
        ):
            await until(lambda: len(recorded) >= 8)
        return recorded[:8]

    async def test() -> None:
        first, again, other = await one_run(5), await one_run(5), await one_run(6)
        assert first == again != other
        expected = Backoff(5, DEVICE)
        assert first == [expected.next_delay() for _ in range(8)]

    go(test)


def test_the_backoff_starts_again_at_one_second_after_a_connection_that_got_its_handshake() -> None:
    async def test() -> None:
        port = free_port()
        recorded: list[float] = []
        gate = asyncio.Semaphore(3)

        async def sleeper(delay: float) -> None:
            recorded.append(delay)
            await gate.acquire()

        sensor = make_sensor(
            port, ticks=0, config=SensorConfig(backoff_scale=1.0, tick_s=0.02), sleep=sleeper
        )
        async with running(sensor):
            await until(lambda: len(recorded) >= 4)  # three failures slept, the fourth waits
            assert 7.2 <= recorded[3] <= 8.8  # the fourth failure was at the 8 s step
            server, _ = await serve(port=port)
            gate.release()
            await until(lambda: sensor.connected and sensor.paired)  # handshake done: backoff reset
            await server.stop()  # the monitor goes away
            await until(lambda: len(recorded) >= 5)
            assert 0.9 <= recorded[4] <= 1.1  # not 16 s: back to the first step

    go(test)


def test_readings_unanswered_when_the_connection_is_cut_are_sent_again_and_stored_once() -> None:
    async def test() -> None:
        server, mport = await serve()
        proxy = FaultProxy(mport)
        port = await proxy.start()
        try:
            async with running(make_sensor(port, ticks=150)) as sensor:
                await until(lambda: len(stored(server)) >= 20)
                proxy.to_sensor_blackhole = (
                    True  # ACKs stop arriving; readings still reach the monitor
                )
                await until(lambda: len(stored(server)) >= 40)
                lost_acks = len(sensor.outbox)
                assert lost_acks > 0
                await proxy.sever()
                proxy.to_sensor_blackhole = False
                await sensor.wait_drained(20)
            assert stored(server) == list(range(1, 301))  # each exactly once
            assert sensor.connections >= 2 and server.buffer.conflicts == 0
            # The readings whose ACK was lost were stored the first time; the monitor now said
            # DUPLICATE, and the sensor took that as its answer.
            assert sensor.answers["DUPLICATE"] >= 1
            assert sensor.outbox.resent >= 1
        finally:
            await proxy.stop()
            await server.stop()

    go(test)


def test_after_reconnecting_the_resends_come_before_new_readings() -> None:
    async def test() -> None:
        server, mport = await serve()
        proxy = FaultProxy(mport)
        port = await proxy.start()
        try:
            async with running(make_sensor(port, ticks=150)) as sensor:
                await until(lambda: len(stored(server)) >= 20)
                proxy.to_sensor_blackhole = True
                await until(lambda: len(sensor.outbox) >= 10)
                held = sensor.outbox.seqs()
                mark = len(proxy.timeline)
                await proxy.sever()
                proxy.to_sensor_blackhole = False
                await sensor.wait_drained(20)
            after = [f["seq"] for d, f in proxy.timeline[mark:] if f["type"] == "MEASUREMENT"]
            # On the new connection the first readings sent are the held ones, in seq order.
            assert after[: len(held)] == held[: len(after[: len(held)])] and held[0] == after[0]
            assert after[0] == min(held)
        finally:
            await proxy.stop()
            await server.stop()

    go(test)


def test_answers_that_come_late_cause_resends_and_nothing_is_stored_twice() -> None:
    async def test() -> None:
        server, mport = await serve()
        proxy = FaultProxy(mport)
        port = await proxy.start()
        try:
            async with running(make_sensor(port, ticks=100)) as sensor:
                await until(lambda: len(stored(server)) >= 20)
                proxy.stall("to_sensor")  # the monitor's answers are held up for a while
                await asyncio.sleep(0.4)  # longer than ack_s, shorter than silent_s
                proxy.release("to_sensor")
                await sensor.wait_drained(20)
            assert sensor.connections == 1  # no reconnect was needed
            assert stored(server) == list(range(1, 201)) and sensor.outbox.resent >= 1
            # The sensor sent again, and the monitor said DUPLICATE: it knew them already.
            assert any(
                f["status"] == "DUPLICATE" for f in proxy.sent("to_sensor", "ACK") if "seq" in f
            )
        finally:
            await proxy.stop()
            await server.stop()

    go(test)


def test_a_monitor_that_goes_silent_makes_the_sensor_close_and_reconnect() -> None:
    async def test() -> None:
        server, mport = await serve()
        proxy = FaultProxy(mport)
        port = await proxy.start()
        try:
            async with running(make_sensor(port, ticks=100)) as sensor:
                await until(lambda: len(stored(server)) >= 10)
                proxy.stall("to_sensor")  # nothing comes back, not even heartbeats
                await until(lambda: sensor.connections >= 2, limit=10)  # it gave up after silent_s
                proxy.release("to_sensor")
                await sensor.wait_drained(20)
            assert stored(server) == list(range(1, 201)) and proxy.connections >= 2
        finally:
            await proxy.stop()
            await server.stop()

    go(test)


def test_the_monitor_closing_the_connection_is_met_with_a_reconnect() -> None:
    async def test() -> None:
        server, mport = await serve()
        proxy = FaultProxy(mport)
        port = await proxy.start()
        try:
            async with running(make_sensor(port, ticks=150)) as sensor:
                for _ in range(3):  # cut it three times while it streams
                    await until(lambda: len(sensor.outbox) < 5 and sensor.connected)
                    await asyncio.sleep(0.15)
                    await proxy.sever()
                await sensor.wait_drained(30)
            assert stored(server) == list(range(1, 301)) and sensor.connections >= 3
        finally:
            await proxy.stop()
            await server.stop()

    go(test)


def test_pinned_readings_acknowledged_before_a_monitor_restart_are_gone_from_the_new_monitor() -> (
    None
):
    # PINNED: ACK follows an in-memory store (docs/monitor.md), so a restart loses what was
    # acknowledged. REQ-CON-03 is met only when the disk buffer of d250 sits behind `store`.
    async def test() -> None:
        port = free_port()
        old, _ = await serve(port=port)
        sensor = make_sensor(port, ticks=150)
        async with running(sensor):
            await until(lambda: len(stored(old)) >= 60 and len(sensor.outbox) < 20)
            before = set(stored(old))
            await old.stop()
            fresh, _ = await serve(port=port, buffer=MemoryBuffer())
            try:
                await sensor.wait_drained(30)
                after = set(stored(fresh))
            finally:
                await fresh.stop()
        assert after, "the new monitor received the later readings"
        lost = before - after
        assert lost, "readings acknowledged by the old monitor are not in the new one"
        assert before | after == set(range(1, 301))  # nothing is missing across the two monitors

    go(test)


# ---- the limit (P-17) ----------------------------------------------------------------------


def test_with_no_monitor_only_the_newest_readings_are_kept_and_the_monitor_sees_the_gap() -> None:
    async def test() -> None:
        port = free_port()
        config = SensorConfig(**{**SENSOR.__dict__, "limit": 30})
        sensor = make_sensor(port, ticks=40, config=config)  # 80 readings, room for 30
        async with running(sensor):
            await until(lambda: sensor.finished)
            assert len(sensor.outbox) == 30 and sensor.outbox.dropped == 50
            assert sensor.outbox.seqs() == list(range(51, 81))
            server, _ = await serve(port=port)
            try:
                await sensor.wait_drained(20)
                assert stored(server) == list(range(51, 81))
                assert server.buffer.gaps == 1  # the jump from nothing to seq 51 (P-14)
            finally:
                await server.stop()

    go(test)


# ---- unpairing (P-10) ----------------------------------------------------------------------


def test_the_sensor_stops_measuring_when_unpaired_and_goes_on_without_reusing_a_seq() -> None:
    async def test() -> None:
        server, port = await serve()
        try:
            sensor = make_sensor(port, ticks=200)
            async with running(sensor):
                await until(lambda: len(stored(server)) >= 20)
                server.pairing.unpair(DEVICE, NOW + timedelta(seconds=10))  # type: ignore[attr-defined]
                await until(lambda: not sensor.measuring)
                last = sensor.sim.last_seq
                await asyncio.sleep(0.3)
                assert sensor.sim.last_seq == last and not sensor.paired  # nothing measured
                server.pairing.pair(DEVICE, "pr-2")  # type: ignore[attr-defined]
                await until(lambda: sensor.measuring and sensor.paired)
                await sensor.wait_drained(20)
            seqs = stored(server)
            assert seqs == list(range(1, sensor.sim.last_seq + 1))  # no seq reused, none skipped
            assert server.buffer.conflicts == 0 and sensor.refused == []
            # Time kept moving while it did not measure: the readings after the pause are later.
            readings = server.buffer.readings[DEVICE]
            step = readings[last + 1].measured_at - readings[last].measured_at
            assert step >= timedelta(milliseconds=200)
        finally:
            await server.stop()

    go(test)


# ---- NO_SIGNAL (P-13) ----------------------------------------------------------------------


def test_a_probe_that_is_off_sends_heartbeats_with_no_signal_and_no_readings() -> None:
    async def test() -> None:
        off = Scenario("off", (Keyframe(0, 72, 97, 16),), no_signal=((0.2, 0.6),))
        server, mport = await serve()
        proxy = FaultProxy(mport)
        port = await proxy.start()
        try:
            async with running(make_sensor(port, ticks=60, scenario=off)) as sensor:
                await sensor.wait_drained(20)
            statuses = {f["status"] for f in proxy.sent("to_monitor", "HEARTBEAT")}
            assert statuses == {"OK", "NO_SIGNAL"}
            assert stored(server) == list(range(1, len(stored(server)) + 1))  # a pause is no gap
            assert server.buffer.gaps == 0 and len(stored(server)) < 120
        finally:
            await proxy.stop()
            await server.stop()

    go(test)


@pytest.mark.parametrize("ticks", [0, 1])
def test_a_sensor_with_almost_nothing_to_send_still_connects_and_finishes(ticks: int) -> None:
    async def test() -> None:
        server, port = await serve()
        try:
            async with running(make_sensor(port, ticks=ticks)) as sensor:
                await sensor.wait_drained(10)
            assert len(stored(server)) == ticks * 2
        finally:
            await server.stop()

    go(test)
