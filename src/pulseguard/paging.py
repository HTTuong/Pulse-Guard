"""Cursor pagination and time ranges shared by list endpoints (docs/api.md, Pagination, Times).

A cursor holds the sort key of the last item returned and the filters it was made for, as base64
JSON. A cursor from one query used with other filters is refused, so a client cannot get a page
that silently belongs to another list."""

import base64
import binascii
import json
from datetime import datetime, timedelta

from pulseguard.errors import ApiError
from pulseguard.measurements.rules import parse_time

MAX_RANGE = timedelta(days=7)
Filters = dict[str, str | int | None]


def encode_cursor(after: dict[str, str], filters: Filters) -> str:
    raw = json.dumps({"after": after, "filters": filters}, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, filters: Filters, keys: tuple[str, ...]) -> dict[str, str]:
    invalid = ApiError(400, "INVALID_CURSOR", "Invalid cursor")
    try:
        data = json.loads(base64.urlsafe_b64decode((cursor + "=" * (-len(cursor) % 4)).encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise invalid from None
    after = data.get("after") if isinstance(data, dict) else None
    if not isinstance(after, dict) or set(after) != set(keys):
        raise invalid
    if not all(isinstance(after[k], str) for k in keys):
        raise invalid
    if data.get("filters") != filters:
        raise ApiError(400, "INVALID_CURSOR", "Cursor was made for other filters")
    return {k: str(after[k]) for k in keys}


def time_range(start: str, end: str) -> tuple[datetime, datetime]:
    """[start, end), both with an offset, start before end, at most 7 days long."""
    parsed = {}
    for name, text in (("from", start), ("to", end)):
        value = parse_time(text)
        if value is None:
            raise ApiError(
                400,
                "INVALID_TIME",
                "Time must be ISO 8601 with an offset",
                errors=[{"field": name, "message": "needs an offset, such as Z"}],
            )
        parsed[name] = value
    if parsed["from"] >= parsed["to"]:
        raise ApiError(400, "INVALID_RANGE", "from must be before to")
    if parsed["to"] - parsed["from"] > MAX_RANGE:
        raise ApiError(400, "INVALID_RANGE", "A range may be at most 7 days")
    return parsed["from"], parsed["to"]
