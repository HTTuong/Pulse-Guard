"""Reading the JSON inside a frame the strict way (P-04) and times (P-05).

Python's json module accepts NaN, Infinity and repeated keys, and turns 1e999 into infinity
(checked on 2026-11-25). A record must not change silently, so each is refused.
"""

import json
import re
from datetime import UTC, datetime
from typing import Any

# [0-9], not \d: \d also matches digits of other scripts, which fromisoformat would reject late.
_WIRE_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z")


class BadJson(ValueError):
    pass


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise BadJson("a key is repeated")
    return dict(pairs)


def _no_constant(name: str) -> Any:
    raise BadJson(f"{name} is not allowed")


def _finite_float(text: str) -> float:
    value = float(text)
    if value in (float("inf"), float("-inf")):
        raise BadJson("number too large")
    return value


def decode_object(body: bytes) -> dict[str, Any]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise BadJson("not valid UTF-8") from None
    try:
        value = json.loads(
            text,
            object_pairs_hook=_no_duplicates,
            parse_constant=_no_constant,
            parse_float=_finite_float,
        )
    except BadJson:
        raise
    except json.JSONDecodeError:
        raise BadJson("not valid JSON") from None
    except RecursionError:
        raise BadJson("nested too deep") from None
    if not isinstance(value, dict):
        raise BadJson("not a JSON object")
    return value


def parse_wire_time(text: object) -> datetime | None:
    """`2026-11-12T18:52:03.000Z` exactly: UTC, milliseconds, Z. Anything else is None (P-05)."""
    if not isinstance(text, str) or not _WIRE_TIME.fullmatch(text):
        return None
    try:
        return datetime.fromisoformat(text).astimezone(UTC)
    except ValueError:  # a date or time that does not exist
        return None
