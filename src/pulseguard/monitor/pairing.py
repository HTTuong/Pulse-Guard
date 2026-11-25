"""Who a sensor is paired with is decided by the backend (REQ-DEV-05); the monitor only asks."""

from typing import Protocol


class PairingSource(Protocol):
    async def pairing_of(self, device: str, monitor: str) -> str | None:
        """The pairing id of this sensor with this monitor's session, or None when not paired."""
        ...


class StaticPairing:
    """A fixed table, for tests and for running the monitor by hand."""

    def __init__(self, pairs: dict[str, str] | None = None) -> None:
        self.pairs = dict(pairs or {})

    async def pairing_of(self, device: str, monitor: str) -> str | None:
        return self.pairs.get(device)
