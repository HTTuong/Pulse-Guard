"""The TCP server of the patient monitor: one task per sensor connection (labs/asyncio_echo).

This file moves bytes and runs the timers of protocol.md; every rule that decides ACK or ERROR
is in session.py. Timers use the event loop's clock, which is monotonic, so a change of the wall
clock does not fire or stop them (P-18).
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

from pulseguard.clock import Clock
from pulseguard.monitor.buffer import MemoryBuffer
from pulseguard.monitor.logs import log_event
from pulseguard.monitor.pairing import PairingSource
from pulseguard.monitor.session import Session
from pulseguard.protocol.framing import FrameDecoder, encode_frame

log = logging.getLogger("pulseguard.monitor")
READ_SIZE = 4096


@dataclass(frozen=True)
class MonitorConfig:
    """Seconds, as in the timer table of protocol.md; tests use smaller values."""

    serial: str = "MON-0001"
    hello_s: float = 5.0  # HELLO must arrive this long after connecting (P-08)
    frame_s: float = 5.0  # a frame that has started must complete in this time (P-03)
    heartbeat_s: float = 5.0  # (P-13)
    silent_s: float = 15.0  # no frame at all for this long closes the connection
    pair_check_s: float = 5.0  # ask the backend again while not paired (P-09)


@dataclass
class MonitorServer:
    clock: Clock
    pairing: PairingSource
    buffer: MemoryBuffer = field(default_factory=MemoryBuffer)
    config: MonitorConfig = field(default_factory=MonitorConfig)
    handlers: set[asyncio.Task[None]] = field(default_factory=set)
    closed: dict[str, int] = field(default_factory=dict)  # connections by the reason they ended
    server: asyncio.Server | None = None

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self.server = await asyncio.start_server(self._accept, host, port)
        bound: int = self.server.sockets[0].getsockname()[1]
        log_event(log, "listening", host=host, port=bound, monitor=self.config.serial)
        return bound

    async def stop(self) -> None:
        """Stop listening, then cancel every connection and wait until each has cleaned up."""
        if self.server is not None:
            self.server.close()
        tasks = list(self.handlers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.server is not None:
            await self.server.wait_closed()

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is None:  # always set inside a running task; this keeps the type checker sure
            raise RuntimeError("no current task")
        self.handlers.add(task)
        try:
            await _Connection(self, reader, writer).run()
        finally:
            self.handlers.discard(task)


class _Connection:
    def __init__(
        self, owner: MonitorServer, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.owner = owner
        self.config = owner.config
        self.reader = reader
        self.writer = writer
        self.session = Session(owner.config.serial, owner.clock, owner.buffer, owner.pairing, log)
        self.write_lock = asyncio.Lock()
        self.background: list[asyncio.Task[None]] = []
        self.peer = str(writer.get_extra_info("peername"))

    async def send(self, messages: list[dict[str, Any]]) -> None:
        if not messages:
            return
        async with self.write_lock:
            for message in messages:
                self.writer.write(encode_frame(message))
            await self.writer.drain()

    async def run(self) -> None:
        reason = "peer_closed"
        log_event(log, "connection_open", peer=self.peer)
        try:
            reason = await self._read_loop()
        except asyncio.CancelledError:
            reason = "shutdown"
            raise  # the one who cancelled us is waiting for it (labs/README.md)
        except (ConnectionError, OSError) as problem:
            reason = "connection_error"
            log_event(log, "connection_error", logging.WARNING, error=type(problem).__name__)
        except Exception as problem:  # a bug in one connection must not stop the others
            reason = "internal_error"
            log_event(log, "internal_error", logging.ERROR, error=type(problem).__name__)
        finally:
            await self._cleanup(reason)

    async def _cleanup(self, reason: str) -> None:
        for task in self.background:
            task.cancel()
        await asyncio.gather(*self.background, return_exceptions=True)
        self.writer.close()
        with contextlib.suppress(ConnectionError, OSError):
            await self.writer.wait_closed()
        self.owner.closed[reason] = self.owner.closed.get(reason, 0) + 1
        log_event(
            log, "connection_closed", peer=self.peer, device=self.session.device, reason=reason
        )

    async def _read_loop(self) -> str:
        loop = asyncio.get_running_loop()
        decoder = FrameDecoder()
        opened = last_frame = loop.time()
        partial_since: float | None = None
        while True:
            # Several timers run at once; the nearest one decides how long to wait for bytes.
            deadlines = [(last_frame + self.config.silent_s, "silent")]
            if not self.session.hello_done:
                deadlines.append((opened + self.config.hello_s, "hello_timeout"))
            if partial_since is not None:
                deadlines.append((partial_since + self.config.frame_s, "incomplete_frame"))
            deadline, why = min(deadlines)
            try:
                async with asyncio.timeout_at(deadline):
                    data = await self.reader.read(READ_SIZE)
            except TimeoutError:
                log_event(log, "timeout", logging.WARNING, device=self.session.device, timer=why)
                return why
            if not data:
                return "peer_closed"

            now = loop.time()
            frames, error = decoder.feed(data)
            if decoder.pending:
                # A new partial frame starts now, unless it is the rest of one we already wait for.
                if partial_since is None or frames:
                    partial_since = now
            else:
                partial_since = None

            for frame in frames:
                last_frame = now
                reply = await self.session.handle(frame)
                await self.send(reply.messages)
                if reply.close:
                    return reply.close_reason
                if self.session.hello_done and not self.background:
                    self._start_background()
            if error is not None:
                log_event(
                    log, "frame_refused", logging.WARNING, code="BAD_LENGTH", detail=str(error)
                )
                await self.send([self.session.error("BAD_LENGTH", str(error))])
                return "bad_length"

    def _start_background(self) -> None:
        self.background = [
            asyncio.create_task(self._heartbeats()),
            asyncio.create_task(self._pairing()),
        ]

    async def _heartbeats(self) -> None:
        while True:
            await asyncio.sleep(self.config.heartbeat_s)
            await self.send([self.session.heartbeat()])

    async def _pairing(self) -> None:
        while not self.session.paired:
            await self.send(await self.session.check_pairing())
            if self.session.paired:
                return
            await asyncio.sleep(self.config.pair_check_s)
