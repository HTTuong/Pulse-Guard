"""A TCP proxy between a sensor and the monitor that a test can break on purpose.

It forwards bytes both ways and records every frame it forwards, in order, so a test can see what
was on the wire. Three faults:
- `sever()` cuts every connection, which is what a crash or a pulled cable looks like.
- `stall(direction)` holds the bytes of one direction and sends them in order on `release()`:
  a network that stops for a while. This is the realistic way to delay or silence a TCP link.
- the blackhole flags drop bytes of one direction. A real TCP link never loses bytes in the middle
  of a live connection, so this is only used when the connection is cut right after (the answers
  that were in flight when it broke).
"""

import asyncio
import contextlib
from typing import Any

from pulseguard.protocol.codec import BadJson, decode_object
from pulseguard.protocol.framing import FrameDecoder


class FaultProxy:
    def __init__(self, target_port: int) -> None:
        self.target_port = target_port
        self.to_sensor_blackhole = False
        self.to_monitor_blackhole = False
        self.connections = 0
        self.timeline: list[tuple[str, dict[str, Any]]] = []  # ("to_monitor"|"to_sensor", frame)
        self._writers: list[asyncio.StreamWriter] = []
        self._gate = {"to_sensor": asyncio.Event(), "to_monitor": asyncio.Event()}
        for gate in self._gate.values():
            gate.set()
        self._server: asyncio.Server | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        port: int = self._server.sockets[0].getsockname()[1]
        return port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        await self.sever()

    def stall(self, direction: str) -> None:
        self._gate[direction].clear()

    def release(self, direction: str) -> None:
        self._gate[direction].set()

    def sent(self, direction: str, kind: str) -> list[dict[str, Any]]:
        return [f for d, f in self.timeline if d == direction and f.get("type") == kind]

    async def sever(self) -> None:
        for writer in self._writers:
            writer.close()
        self._writers.clear()

    async def _accept(self, c_reader: asyncio.StreamReader, c_writer: asyncio.StreamWriter) -> None:
        try:
            u_reader, u_writer = await asyncio.open_connection("127.0.0.1", self.target_port)
        except OSError:
            c_writer.close()
            return
        self.connections += 1
        self._writers += [c_writer, u_writer]
        pumps = [
            asyncio.create_task(self._pump(c_reader, u_writer, "to_monitor")),
            asyncio.create_task(self._pump(u_reader, c_writer, "to_sensor")),
        ]
        try:
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for writer in (c_writer, u_writer):
                writer.close()
                with contextlib.suppress(ConnectionError, OSError):
                    await writer.wait_closed()

    async def _pump(
        self, source: asyncio.StreamReader, sink: asyncio.StreamWriter, direction: str
    ) -> None:
        decoder = FrameDecoder()
        try:
            while data := await source.read(4096):
                blackhole = (
                    self.to_sensor_blackhole
                    if direction == "to_sensor"
                    else self.to_monitor_blackhole
                )
                if blackhole:
                    continue
                await self._gate[direction].wait()  # held here while the direction is stalled
                frames, _ = decoder.feed(data)
                for frame in frames:
                    with contextlib.suppress(BadJson):
                        self.timeline.append((direction, decode_object(frame)))
                sink.write(data)
                await sink.drain()
        except (ConnectionError, OSError):
            return
