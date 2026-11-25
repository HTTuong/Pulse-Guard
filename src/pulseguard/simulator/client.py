"""A minimal sensor client: connect, HELLO, wait for PAIR, stream the simulated readings.

It has no resend and no reconnect: those are d249. It exists so the monitor can be run against
the simulator by hand and in tests, and it returns every frame the monitor sent back.
"""

import asyncio
import contextlib
from typing import Any

from pulseguard.clock import Clock
from pulseguard.protocol.codec import decode_object
from pulseguard.protocol.framing import FrameDecoder, encode_frame
from pulseguard.simulator import wire
from pulseguard.simulator.generator import SensorSimulator


async def stream(
    host: str,
    port: int,
    sim: SensorSimulator,
    seconds: int,
    clock: Clock,
    pace_s: float = 0.0,
    pair_wait_s: float = 5.0,
    settle_s: float = 2.0,
) -> list[dict[str, Any]]:
    """Send `seconds` ticks of the simulator and return what the monitor answered."""
    reader, writer = await asyncio.open_connection(host, port)
    received: list[dict[str, Any]] = []
    decoder = FrameDecoder()

    async def listen() -> None:
        while data := await reader.read(4096):
            frames, _ = decoder.feed(data)
            received.extend(decode_object(f) for f in frames)

    listener = asyncio.create_task(listen())

    async def wait_for(predicate: Any, limit: float) -> bool:
        try:
            async with asyncio.timeout(limit):
                while not any(predicate(m) for m in received):
                    await asyncio.sleep(0.01)
            return True
        except TimeoutError:
            return False

    try:
        writer.write(
            encode_frame(wire.hello(sim.device, sim.device_type, sim.parameters, clock.now()))
        )
        await writer.drain()
        if not await wait_for(lambda m: m["type"] == "PAIR", pair_wait_s):
            return received
        sent = 0
        for tick in sim.run(seconds):
            if not tick.signal:
                writer.write(encode_frame(wire.heartbeat("NO_SIGNAL", clock.now())))
            for reading in tick.readings:
                writer.write(encode_frame(wire.measurement(sim.device, reading, clock.now())))
                sent += 1
            await writer.drain()
            if pace_s:
                await asyncio.sleep(pace_s)
        # Wait until every reading has an answer, or give up: this client does not resend.
        await wait_for(
            lambda m: (
                m["type"] == "ACK" and m.get("ack") == "MEASUREMENT" and m["seq"] == sim.last_seq
            ),
            settle_s,
        )
        return received
    finally:
        writer.close()
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError, ConnectionError, OSError):
            await listener
        with contextlib.suppress(ConnectionError, OSError):
            await writer.wait_closed()
