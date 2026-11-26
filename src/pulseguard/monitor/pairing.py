"""Who a sensor is paired with is decided by the backend (REQ-DEV-05); the monitor only asks."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Pairing:
    id: str
    # Set when the backend has unassigned the sensor: readings measured at or after it are refused.
    unpaired_at: datetime | None = None


class PairingSource(Protocol):
    async def pairing_of(self, device: str, monitor: str) -> Pairing | None:
        """The pairing of this sensor with this monitor's session, or None when there is none."""
        ...


class StaticPairing:
    """A table a test or a person changes by hand: the stand-in for the backend."""

    def __init__(self, pairs: dict[str, str] | None = None) -> None:
        self.pairs = dict(pairs or {})
        self.unpaired: dict[str, datetime] = {}

    def pair(self, device: str, pairing_id: str) -> None:
        self.pairs[device] = pairing_id
        self.unpaired.pop(device, None)

    def unpair(self, device: str, at: datetime) -> None:
        self.unpaired[device] = at

    async def pairing_of(self, device: str, monitor: str) -> Pairing | None:
        if device not in self.pairs:
            return None
        return Pairing(self.pairs[device], self.unpaired.get(device))
