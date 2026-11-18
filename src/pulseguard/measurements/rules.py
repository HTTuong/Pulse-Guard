"""Pure checks of one reading: no database, no clock of its own. Each returns a refusal code
from docs/openapi.yaml (IngestResult.refused) or None when the reading passes."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

UNITS = {"PR": "beats/min", "SPO2": "%", "RR": "breaths/min"}
PARAMETERS_OF_KIND = {"PULSE_OXIMETER": {"PR", "SPO2"}, "RESPIRATION": {"RR"}}
# REQ-MEA-02. Bounds are inclusive and apply to the value as stored (one decimal).
PLAUSIBLE = {
    "RR": (Decimal(0), Decimal(120)),
    "SPO2": (Decimal(0), Decimal(100)),
    "PR": (Decimal(0), Decimal(300)),
}
# REQ-MEA-05
MAX_AHEAD = timedelta(seconds=60)
MAX_BEHIND = timedelta(hours=24)


@dataclass(frozen=True)
class Refusal:
    code: str
    detail: str


def stored_value(value: float) -> Decimal | None:
    """The value as the database keeps it, numeric(5,1), rounding half away from zero like
    PostgreSQL does. Duplicates are compared on this value, so 72.04 sent again equals 72.0.
    None for NaN and infinities: they can never be a measurement."""
    try:
        decimal = Decimal(str(value))
        if not decimal.is_finite():
            return None
        return decimal.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def parse_time(text: str) -> datetime | None:
    """ISO 8601 with an offset. A time without an offset is refused, never read as local time."""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def check_kind(device_kind: str, parameter: str) -> Refusal | None:
    if parameter not in PARAMETERS_OF_KIND.get(device_kind, set()):
        return Refusal("WRONG_PARAMETER", f"A {device_kind} does not measure {parameter}")
    return None


def check_unit(parameter: str, unit: str) -> Refusal | None:
    if UNITS[parameter] != unit:
        return Refusal("BAD_UNIT", f"{parameter} is sent in {UNITS[parameter]}, not {unit}")
    return None


def check_time(measured_at: datetime | None, now: datetime) -> Refusal | None:
    if measured_at is None:
        return Refusal("BAD_TIME", "Measured time must be ISO 8601 with an offset")
    if measured_at - now > MAX_AHEAD:
        return Refusal("BAD_TIME", "Measured more than 60 s in the future")
    if now - measured_at > MAX_BEHIND:
        return Refusal("BAD_TIME", "Measured more than 24 h ago")
    return None


def check_value(parameter: str, value: Decimal | None) -> Refusal | None:
    low, high = PLAUSIBLE[parameter]
    if value is None or not low <= value <= high:
        return Refusal("OUT_OF_RANGE", f"{parameter} must be from {low} to {high}")
    if parameter == "SPO2" and value != value.to_integral_value():
        return Refusal("OUT_OF_RANGE", "SPO2 must be a whole number")
    return None
