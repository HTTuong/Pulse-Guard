"""Messages of protocol.md v1 as bytes: a 4 byte length, then JSON (P-01).

Only what a sensor sends is here. Reading frames is the monitor's job (d248).
"""

from datetime import datetime
from typing import Any

from pulseguard.clock import to_wire
from pulseguard.protocol.framing import MAX_FRAME, VERSION, encode_frame
from pulseguard.simulator.generator import Reading

__all__ = ["MAX_FRAME", "VERSION", "encode_frame", "hello", "measurement", "heartbeat"]


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
