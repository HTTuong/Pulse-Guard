"""Builders shared by the monitor tests: valid messages, and a session to send them to."""

import asyncio
import logging
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

from pulseguard.clock import to_wire
from pulseguard.monitor.buffer import MemoryBuffer
from pulseguard.monitor.pairing import StaticPairing
from pulseguard.monitor.session import Reply, Session
from pulseguard.protocol.framing import encode_frame
from tests.conftest import FakeClock

NOW = datetime(2026, 11, 12, 18, 52, 3, tzinfo=UTC)
DEVICE = "SN-PO-0001"
MONITOR = "MON-0001"
PAIRING = "pr-7f3a"


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def at(offset_s: float = 0.0) -> str:
    return to_wire(NOW + timedelta(seconds=offset_s))


def hello(**changes: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "v": 1,
        "type": "HELLO",
        "device": DEVICE,
        "device_type": "PULSE_OXIMETER",
        "parameters": ["PR", "SPO2"],
        "versions": [1],
        "sent_at": at(),
    }
    message.update(changes)
    return {k: v for k, v in message.items() if v is not ...}  # `...` removes a field


def measurement(seq: int = 1, **changes: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "v": 1,
        "type": "MEASUREMENT",
        "device": DEVICE,
        "seq": seq,
        "parameter": "PR",
        "value": 72,
        "unit": "beats/min",
        "measured_at": at(),
        "sent_at": at(),
    }
    message.update(changes)
    return {k: v for k, v in message.items() if v is not ...}


def heartbeat(**changes: Any) -> dict[str, Any]:
    message: dict[str, Any] = {"v": 1, "type": "HEARTBEAT", "status": "OK", "sent_at": at()}
    message.update(changes)
    return {k: v for k, v in message.items() if v is not ...}


def raw(message: dict[str, Any]) -> bytes:
    """The body of a frame, without its length."""
    return encode_frame(message)[4:]


class Rig:
    """A session with a fake clock, a memory buffer and a pairing table the test can change."""

    def __init__(self, paired: bool = True) -> None:
        self.clock = FakeClock(NOW)
        self.buffer = MemoryBuffer()
        self.pairing = StaticPairing({DEVICE: PAIRING} if paired else {})
        self.session = Session(
            MONITOR, self.clock, self.buffer, self.pairing, logging.getLogger("t")
        )

    def send(self, message: dict[str, Any] | bytes) -> Reply:
        body = message if isinstance(message, bytes) else raw(message)
        return run(self.session.handle(body))

    def connect(self) -> None:
        """HELLO and PAIR done: the state in which readings are accepted."""
        assert self.send(hello()).messages[0]["ack"] == "HELLO"
        run(self.session.check_pairing())
        assert self.session.paired


def code_of(reply: Reply) -> str | None:
    errors = [m for m in reply.messages if m["type"] == "ERROR"]
    return str(errors[0]["code"]) if errors else None
