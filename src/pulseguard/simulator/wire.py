"""Messages of protocol.md v1 as bytes: a 4 byte length, then JSON (P-01).

Only what a sensor sends is here. Reading frames is the monitor's job (d248).
"""

import json
import struct
from datetime import datetime
from typing import Any

from pulseguard.clock import to_wire
from pulseguard.simulator.generator import Reading

VERSION = 1
MAX_FRAME = 4096  # P-02


def encode_frame(message: dict[str, Any]) -> bytes:
    body = json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if not 1 <= len(body) <= MAX_FRAME:
        raise ValueError(f"frame body of {len(body)} bytes; P-02 allows 1 to {MAX_FRAME}")
    return struct.pack(">I", len(body)) + body


def hello(
    device: str, device_type: str, parameters: tuple[str, ...], sent_at: datetime
) -> dict[str, Any]:
    return {
        "v": VERSION,
        "type": "HELLO",
        "device": device,
        "device_type": device_type,
        "parameters": list(parameters),
        "versions": [VERSION],
        "sent_at": to_wire(sent_at),
    }


def measurement(device: str, reading: Reading, sent_at: datetime) -> dict[str, Any]:
    return {
        "v": VERSION,
        "type": "MEASUREMENT",
        "device": device,
        "seq": reading.seq,
        "parameter": reading.parameter,
        "value": reading.value,
        "unit": reading.unit,
        "measured_at": to_wire(reading.measured_at),
        "sent_at": to_wire(sent_at),
    }


def heartbeat(status: str, sent_at: datetime) -> dict[str, Any]:
    """`status` is OK, or NO_SIGNAL for a probe that is off (P-13)."""
    return {"v": VERSION, "type": "HEARTBEAT", "status": status, "sent_at": to_wire(sent_at)}
