"""A simulated sensor that keeps its readings until the monitor has answered them.

It follows protocol.md from the sensor's side: HELLO, wait for PAIR, send readings, keep each one
until ACK or ERROR (P-15), send again after 3 s (P-16), reconnect with backoff (P-19) and then
send again everything still unanswered, oldest first (P-20).

Time inside is the event loop's monotonic clock, as P-18 asks; the wall clock only stamps messages.
"""

import asyncio
import contextlib
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pulseguard.clock import Clock, to_wire
from pulseguard.monitor.logs import log_event
from pulseguard.protocol.codec import BadJson, decode_object, parse_wire_time
from pulseguard.protocol.framing import FrameDecoder, encode_frame
from pulseguard.simulator import wire
from pulseguard.simulator.backoff import Backoff
from pulseguard.simulator.generator import SensorSimulator
from pulseguard.simulator.outbox import Outbox

log = logging.getLogger("pulseguard.sensor")


@dataclass(frozen=True)
class SensorConfig:
    """Seconds, as in the timer table of protocol.md; tests use smaller values."""

    tick_s: float = 1.0  # real time between measurements
    heartbeat_s: float = 5.0  # (P-13)
    silent_s: float = 15.0  # no frame from the monitor for this long: close and reconnect
    ack_s: float = 3.0  # (P-16)
    window: int = 50  # (P-16)
    limit: int = 1200  # (P-17)
    connect_s: float = 3.0
    backoff_scale: float = 1.0


class SensorClient:
    def __init__(
        self,
        host: str,
        port: int,
        sim: SensorSimulator,
        clock: Clock,
        seed: int = 1,
        config: SensorConfig | None = None,
        ticks: int | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.host, self.port, self.sim, self.clock = host, port, sim, clock
        self.config = config or SensorConfig()
        self.outbox = Outbox(self.config.window, self.config.ack_s, self.config.limit)
        self.backoff = Backoff(seed, sim.device, scale=self.config.backoff_scale)
        self._ticks = ticks  # how many measuring moments to run; None: for ever
        self._sleep = sleep
        self._wake = asyncio.Event()
        # What a test or a person can look at afterwards.
        self.connections = 0
        self.delays: list[float] = []  # every wait before a reconnect
        self.answers: Counter[str] = Counter()  # ACK statuses: STORED, DUPLICATE
        self.refused: list[tuple[int, str]] = []  # (seq, code) of readings refused for good
        self.discarded = 0  # readings dropped because they were measured after UNPAIRED
        self.connected = False
        self.paired = False
        self.measuring = True
        self.signal = True
        self.finished = False

    # ---- running ----------------------------------------------------------------------------

    async def run(self) -> None:
        """Measure and stay connected until the task is cancelled."""
        producer = asyncio.create_task(self._produce())
        try:
            while True:
                await self._attempt()
                delay = self.backoff.next_delay()
                self.delays.append(delay)
                log_event(log, "reconnect_in", device=self.sim.device, seconds=round(delay, 3))
                await self._sleep(delay)
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    async def wait_drained(self, timeout: float) -> None:
        """Until all measuring is done and every reading has been answered."""
        async with asyncio.timeout(timeout):
            while not (self.finished and len(self.outbox) == 0):
                await asyncio.sleep(0.01)

    async def _attempt(self) -> None:
        try:
            async with asyncio.timeout(self.config.connect_s):
                reader, writer = await asyncio.open_connection(self.host, self.port)
        except (OSError, TimeoutError) as problem:
            log_event(log, "connect_failed", logging.WARNING, error=type(problem).__name__)
            return
        self.connections += 1
        self.connected = True
        reason = "error"
        try:
            reason = await self._connection(reader, writer)
        except asyncio.CancelledError:
            reason = "stopped"
            raise
        finally:
            self.connected = self.paired = False
            self.outbox.connection_lost()  # P-20: everything unanswered goes again, oldest first
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()
            log_event(log, "disconnected", device=self.sim.device, reason=reason)

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> str:
        lock = asyncio.Lock()

        async def send(messages: list[dict[str, Any]]) -> None:
            async with lock:
                for message in messages:
                    writer.write(encode_frame(message))
                await writer.drain()

        sim = self.sim
        await send([wire.hello(sim.device, sim.device_type, sim.parameters, self.clock.now())])
        reading = asyncio.create_task(self._read(reader, send))
        writing = asyncio.create_task(self._write(send))
        try:
            await asyncio.wait({reading, writing}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            reading.cancel()
            writing.cancel()
            await asyncio.gather(reading, writing, return_exceptions=True)
        if reading.done() and not reading.cancelled() and reading.exception() is None:
            return reading.result()
        return "connection_error"

    # ---- what comes from the monitor --------------------------------------------------------

    async def _read(
        self,
        reader: asyncio.StreamReader,
        send: Callable[[list[dict[str, Any]]], Awaitable[None]],
    ) -> str:
        loop = asyncio.get_running_loop()
        decoder = FrameDecoder()
        last_frame = loop.time()
        while True:
            try:
                async with asyncio.timeout_at(last_frame + self.config.silent_s):
                    data = await reader.read(4096)
            except TimeoutError:
                return "silent"
            if not data:
                return "peer_closed"
            frames, error = decoder.feed(data)
            if error is not None:
                return "bad_length"
            for frame in frames:
                last_frame = loop.time()
                try:
                    message = decode_object(frame)
                except BadJson:
                    continue  # the monitor sent rubbish; nothing to answer
                await self._on_message(message, send)

    async def _on_message(
        self, message: dict[str, Any], send: Callable[[list[dict[str, Any]]], Awaitable[None]]
    ) -> None:
        kind, device = message.get("type"), self.sim.device
        if kind == "ACK" and message.get("ack") == "HELLO":
            self.backoff.reset()  # a connection that got through its handshake was a good one
            log_event(log, "hello_acked", device=device, version=message.get("version"))
        elif kind == "ACK" and message.get("ack") == "MEASUREMENT":
            seq = message.get("seq")
            # `True == 1` in Python: a bool must not answer reading 1 (F-05).
            if isinstance(seq, int) and not isinstance(seq, bool) and self.outbox.answered(seq):
                self.answers[str(message.get("status"))] += 1
                self._wake.set()
        elif kind == "ERROR":
            seq = message.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool):
                if self.outbox.answered(seq):  # refused for good (P-15)
                    self.refused.append((seq, str(message.get("code"))))
                    log_event(log, "refused", logging.WARNING, device=device, seq=seq,
                              code=message.get("code"))  # fmt: skip
                    self._wake.set()
            else:
                log_event(log, "error_from_monitor", logging.WARNING, device=device,
                          code=message.get("code"))  # fmt: skip
        elif kind == "PAIR":
            await self._on_pair(message, send)

    async def _on_pair(
        self, message: dict[str, Any], send: Callable[[list[dict[str, Any]]], Awaitable[None]]
    ) -> None:
        device = self.sim.device
        if message.get("status") == "PAIRED":
            self.paired, self.measuring = True, True
            log_event(log, "paired", device=device, pairing=message.get("pairing"))
        elif message.get("status") == "UNPAIRED":
            at = parse_wire_time(message.get("unpaired_at")) or self.clock.now()
            self.paired, self.measuring = False, False  # P-10: stop measuring
            self.discarded += self.outbox.discard_measured_from(at)
            log_event(log, "unpaired", device=device, discarded=self.discarded)
        else:
            return
        ack = {
            "v": wire.VERSION,
            "type": "ACK",
            "ack": "PAIR",
            "sent_at": to_wire(self.clock.now()),
        }
        await send([ack])
        self._wake.set()

    # ---- what goes to the monitor -----------------------------------------------------------

    async def _write(self, send: Callable[[list[dict[str, Any]]], Awaitable[None]]) -> None:
        loop = asyncio.get_running_loop()
        next_heartbeat = loop.time() + self.config.heartbeat_s
        while True:
            self._wake.clear()  # before looking, so a reading added meanwhile is not missed
            now = loop.time()
            frames: list[dict[str, Any]] = []
            if self.paired:  # P-09: no MEASUREMENT before PAIRED
                sent_at = self.clock.now()
                frames += [
                    wire.measurement(self.sim.device, r, sent_at) for r in self.outbox.due(now)
                ]
            if now >= next_heartbeat:
                status = "OK" if self.signal else "NO_SIGNAL"
                frames.append(wire.heartbeat(status, self.clock.now()))
                next_heartbeat = now + self.config.heartbeat_s
            if frames:
                await send(frames)
            deadline = next_heartbeat
            overdue = self.outbox.next_deadline()
            if overdue is not None and self.paired:
                deadline = min(deadline, overdue)
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(max(0.0, deadline - loop.time())):
                    await self._wake.wait()

    # ---- measuring --------------------------------------------------------------------------

    async def _produce(self) -> None:
        count = 0
        while self._ticks is None or count < self._ticks:
            await asyncio.sleep(self.config.tick_s)
            count += 1
            if not self.measuring:
                self.sim.skip()  # time passes, nothing is measured (P-10)
                continue
            tick = self.sim.tick()
            self.signal = tick.signal
            for reading in tick.readings:
                self.outbox.add(reading)
            if tick.readings:
                self._wake.set()
        self.finished = True
