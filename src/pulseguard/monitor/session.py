"""The rules of protocol.md for one connection, without any I/O.

`Session.handle` gets the bytes of one frame and answers with the frames to send back and
whether to close. The server (server.py) only moves bytes and runs timers; every rule that
decides ACK or ERROR is here, so it is tested with plain function calls.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pulseguard.clock import Clock, to_wire
from pulseguard.measurements import rules
from pulseguard.monitor.buffer import Entry, MemoryBuffer, StoreResult
from pulseguard.monitor.logs import log_event
from pulseguard.monitor.pairing import PairingSource
from pulseguard.protocol.codec import BadJson, decode_object, parse_wire_time
from pulseguard.protocol.framing import VERSION

SUPPORTED_VERSIONS = (VERSION,)
KNOWN_TYPES = {"HELLO", "PAIR", "MEASUREMENT", "HEARTBEAT", "ACK", "ERROR"}
HEARTBEAT_STATUSES = {"OK", "NO_SIGNAL"}
PARAMETERS_OF_TYPE = {"PULSE_OXIMETER": ["PR", "SPO2"], "RESPIRATION": ["RR"]}


class Refused(Exception):
    """A frame breaks a rule: it is answered with ERROR `code`."""

    def __init__(self, code: str, detail: str, seq: int | None = None, close: bool = False) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.seq = seq
        self.close = close


@dataclass
class Reply:
    messages: list[dict[str, Any]] = field(default_factory=list)
    close: bool = False
    close_reason: str = ""


def _require(message: dict[str, Any], name: str, seq: int | None = None) -> Any:
    if name not in message:
        raise Refused("MISSING_FIELD", f"{name} is missing", seq)
    return message[name]


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


class Session:
    def __init__(
        self,
        monitor: str,
        clock: Clock,
        buffer: MemoryBuffer,
        pairing: PairingSource,
        log: logging.Logger,
    ) -> None:
        self.monitor = monitor
        self.clock = clock
        self.buffer = buffer
        self.pairing = pairing
        self.log = log
        self.device: str | None = None
        self.device_type: str | None = None
        self.parameters: list[str] = []
        self.version: int | None = None
        self.pairing_id: str | None = None
        self.unpaired_at: datetime | None = None  # P-10: set after UNPAIRED
        self._not_paired_said = False

    @property
    def hello_done(self) -> bool:
        return self.device is not None

    @property
    def paired(self) -> bool:
        return self.pairing_id is not None

    # ---- messages the monitor builds --------------------------------------------------------

    def _stamp(self, message: dict[str, Any]) -> dict[str, Any]:
        return {"v": self.version or VERSION, **message, "sent_at": to_wire(self.clock.now())}

    def error(self, code: str, detail: str, seq: int | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"type": "ERROR", "code": code, "detail": detail}
        if seq is not None:
            body["seq"] = seq
        return self._stamp(body)

    def heartbeat(self) -> dict[str, Any]:
        return self._stamp({"type": "HEARTBEAT", "status": "OK"})

    # ---- pairing (P-09, P-10) ---------------------------------------------------------------

    async def check_pairing(self) -> list[dict[str, Any]]:
        """Ask the backend and say what changed: PAIRED, UNPAIRED, or NOT_PAIRED (the first time).

        Called now and then for as long as the connection lives, so an unassignment that happens
        while the sensor is connected reaches it (P-10).
        """
        if self.device is None:
            return []
        state = await self.pairing.pairing_of(self.device, self.monitor)
        if state is not None and state.unpaired_at is None:
            if self.pairing_id == state.id:
                return []
            self.pairing_id, self.unpaired_at = state.id, None
            self._not_paired_said = False
            log_event(self.log, "paired", device=self.device, pairing=state.id)
            return [self._stamp({"type": "PAIR", "status": "PAIRED", "pairing": state.id})]
        if self.paired:
            # Unassigned by the backend. Its time if it gave one, else the moment we noticed.
            at = state.unpaired_at if state and state.unpaired_at else self.clock.now()
            self.pairing_id, self.unpaired_at = None, at
            self._not_paired_said = True  # this sensor knows; no NOT_PAIRED on top of UNPAIRED
            log_event(self.log, "unpaired", device=self.device, unpaired_at=to_wire(at))
            return [self._stamp({"type": "PAIR", "status": "UNPAIRED", "unpaired_at": to_wire(at)})]
        if self._not_paired_said:
            return []
        self._not_paired_said = True
        log_event(self.log, "not_paired", device=self.device)
        return [self.error("NOT_PAIRED", "the backend has not paired this sensor")]

    # ---- one frame --------------------------------------------------------------------------

    async def handle(self, frame: bytes) -> Reply:
        try:
            return self._dispatch(decode_object(frame))
        except BadJson as problem:
            return self._refuse(Refused("BAD_JSON", str(problem)))
        except Refused as problem:
            return self._refuse(problem)

    def _refuse(self, problem: Refused) -> Reply:
        log_event(
            self.log,
            "frame_refused",
            logging.WARNING,
            device=self.device,
            code=problem.code,
            detail=problem.detail,
            seq=problem.seq,
        )
        return Reply(
            [self.error(problem.code, problem.detail, problem.seq)],
            close=problem.close,
            close_reason=problem.code.lower() if problem.close else "",
        )

    def _dispatch(self, message: dict[str, Any]) -> Reply:
        _require(message, "type")
        _require(message, "v")
        kind = message["type"]
        if kind not in KNOWN_TYPES:
            raise Refused("UNKNOWN_TYPE", "unknown message type")
        if self._sent_at(message) is None:
            raise Refused("BAD_TIME", "sent_at must be like 2026-11-12T18:52:03.000Z")

        if kind == "HELLO":
            return self._hello(message)
        if self.device is None:
            raise Refused("HELLO_FIRST", "the first frame must be HELLO")
        # `True == 1` and `1.0 == 1` in Python: the version must be the integer itself.
        if type(message["v"]) is not int or message["v"] != self.version:
            raise Refused("UNSUPPORTED_VERSION", f"this connection speaks v{self.version}")
        if kind == "MEASUREMENT":
            return self._measurement(message)
        if kind == "HEARTBEAT":
            if message.get("status") not in HEARTBEAT_STATUSES:
                raise Refused("BAD_FIELD", "status must be OK or NO_SIGNAL")
            return Reply()
        if kind == "ERROR":
            log_event(
                self.log,
                "sensor_error",
                logging.WARNING,
                device=self.device,
                code=message.get("code"),
                seq=message.get("seq"),
            )
            return Reply()
        if kind == "ACK":
            return Reply()
        raise Refused("UNKNOWN_TYPE", f"{kind} is not accepted from a sensor")  # PAIR

    def _sent_at(self, message: dict[str, Any]) -> object:
        # A common field (protocol.md): missing is MISSING_FIELD, malformed is BAD_TIME.
        value = _require(message, "sent_at")
        return parse_wire_time(value)

    # ---- HELLO (P-07, P-08) -----------------------------------------------------------------

    def _hello(self, message: dict[str, Any]) -> Reply:
        if self.device is not None:
            raise Refused("BAD_FIELD", "HELLO was already received on this connection")
        device = _require(message, "device")
        device_type = _require(message, "device_type")
        parameters = _require(message, "parameters")
        versions = _require(message, "versions")
        if not isinstance(device, str) or not device.strip():
            raise Refused("BAD_FIELD", "device must be a non-empty string")
        if device_type not in PARAMETERS_OF_TYPE:
            raise Refused("BAD_FIELD", "device_type must be PULSE_OXIMETER or RESPIRATION")
        if not isinstance(parameters, list) or sorted(parameters) != sorted(
            PARAMETERS_OF_TYPE[device_type]
        ):
            raise Refused("BAD_FIELD", f"a {device_type} sends {PARAMETERS_OF_TYPE[device_type]}")
        if not isinstance(versions, list) or not all(
            isinstance(v, int) and not isinstance(v, bool) for v in versions
        ):
            raise Refused("BAD_FIELD", "versions must be a list of integers")
        common = sorted(set(versions) & set(SUPPORTED_VERSIONS))
        if not common:
            raise Refused(
                "UNSUPPORTED_VERSION", f"this monitor speaks {list(SUPPORTED_VERSIONS)}", close=True
            )
        self.device, self.device_type, self.parameters = device, device_type, list(parameters)
        self.version = common[-1]
        log_event(self.log, "hello", device=device, device_type=device_type, version=self.version)
        return Reply(
            [
                self._stamp(
                    {
                        "type": "ACK",
                        "ack": "HELLO",
                        "version": self.version,
                        "monitor": self.monitor,
                    }
                )
            ]
        )

    # ---- MEASUREMENT (P-11, P-12, P-14) -----------------------------------------------------

    def _measurement(self, message: dict[str, Any]) -> Reply:
        seq = message.get("seq")
        known_seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else None
        if not self.paired and self.unpaired_at is None:
            raise Refused("NOT_PAIRED", "no readings before PAIR", known_seq)
        device = _require(message, "device", known_seq)
        seq = _require(message, "seq")
        parameter = _require(message, "parameter", known_seq)
        value = _require(message, "value", known_seq)
        unit = _require(message, "unit", known_seq)
        measured_at_text = _require(message, "measured_at", known_seq)

        if device != self.device:
            raise Refused("WRONG_DEVICE", "device differs from the one in HELLO", known_seq)
        if known_seq is None or known_seq < 1:
            raise Refused("BAD_FIELD", "seq must be an integer from 1", None)
        if parameter not in self.parameters:
            raise Refused("BAD_FIELD", f"{parameter!r} is not a parameter of this sensor", seq)
        if not isinstance(unit, str):
            raise Refused("BAD_UNIT", "unit must be text", seq)
        if refusal := rules.check_unit(parameter, unit):
            raise Refused("BAD_UNIT", refusal.detail, seq)
        if not _is_number(value):
            raise Refused("BAD_FIELD", "value must be a number", seq)
        measured_at = parse_wire_time(measured_at_text)
        if measured_at is None:
            raise Refused("BAD_TIME", "measured_at must be like 2026-11-12T18:52:03.000Z", seq)
        if refusal := rules.check_time(measured_at, self.clock.now()):
            raise Refused("BAD_TIME", refusal.detail, seq)
        if self.unpaired_at is not None and not self.paired and measured_at >= self.unpaired_at:
            raise Refused("NOT_PAIRED", "measured after the sensor was unpaired", seq)
        stored = rules.stored_value(value)
        refusal = rules.check_value(parameter, stored)
        if refusal is not None or stored is None:
            raise Refused("OUT_OF_RANGE", refusal.detail if refusal else "not finite", seq)

        result, gap = self.buffer.store(device, seq, Entry(parameter, stored, measured_at))
        if result is StoreResult.CONFLICT:
            log_event(self.log, "seq_conflict", logging.ERROR, device=device, seq=seq)
            raise Refused("SEQ_CONFLICT", "this seq was used for a different reading", seq)
        if gap:
            log_event(self.log, "seq_gap", logging.WARNING, device=device, seq=seq)
        log_event(self.log, "measurement", device=device, seq=seq, result=result.value)
        return Reply(
            [self._stamp({"type": "ACK", "ack": "MEASUREMENT", "seq": seq, "status": result.value})]
        )
