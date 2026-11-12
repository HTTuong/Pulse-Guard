from datetime import datetime, timedelta, timezone

import pytest

from pulseguard.clock import to_wire


def test_wire_format_is_utc_with_milliseconds_and_z() -> None:
    helsinki_winter = timezone(timedelta(hours=2))
    instant = datetime(2026, 11, 12, 20, 52, 3, 7000, tzinfo=helsinki_winter)
    assert to_wire(instant) == "2026-11-12T18:52:03.007Z"


def test_wire_format_refuses_a_naive_time() -> None:
    with pytest.raises(ValueError, match="naive"):
        to_wire(datetime(2026, 11, 12, 18, 52, 3))  # noqa: DTZ001 - the naive time is the point
