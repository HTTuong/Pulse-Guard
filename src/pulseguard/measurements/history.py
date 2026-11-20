"""Trends of one parameter of one session over a time range (REQ-VIEW-03, REQ-MEA-04).

Raw readings while the range holds at most max_points; otherwise equal time buckets with count,
min, max and mean. Min and max are kept so a short spike that raised an alarm is still visible on
a downsampled chart. Empty buckets are left out, so a gap in the data shows as a gap."""

import uuid
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel
from sqlalchemy import func, select, text, tuple_
from sqlalchemy.orm import Session

from pulseguard.clock import to_wire
from pulseguard.errors import ApiError
from pulseguard.models import Device, Measurement
from pulseguard.paging import Filters, decode_cursor, encode_cursor


class Reading(BaseModel):
    seq: int
    device_id: uuid.UUID
    serial: str
    parameter: str
    value: float
    measured_at: str


class Bucket(BaseModel):
    start: str
    end: str
    count: int
    min: float
    max: float
    mean: float


class TrendPage(BaseModel):
    parameter: str
    start: str
    end: str
    downsampled: bool
    total: int
    items: list[Reading | Bucket]
    next_cursor: str | None


def count_in_range(
    db: Session, session_id: uuid.UUID, parameter: str, start: datetime, end: datetime
) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Measurement)
            .where(
                Measurement.session_id == session_id,
                Measurement.parameter == parameter,
                Measurement.measured_at >= start,
                Measurement.measured_at < end,
            )
        )
        or 0
    )


def buckets(
    db: Session,
    session_id: uuid.UUID,
    parameter: str,
    start: datetime,
    end: datetime,
    max_points: int,
) -> list[Bucket]:
    """Equal buckets in microseconds, so their edges are exact and every reading falls in exactly
    one: bucket i is [start + i*width, start + (i+1)*width), the last one ends at `end`."""
    span = int((end - start) / timedelta(microseconds=1))
    width = -(-span // max_points)  # ceiling: at most max_points buckets
    rows = db.execute(
        text(
            "SELECT floor(extract(epoch FROM measured_at - :start) * 1000000 / :width)::bigint"
            "   AS bucket, count(*), min(value), max(value), avg(value)"
            " FROM measurement WHERE session_id = :session AND parameter = :parameter"
            "   AND measured_at >= :start AND measured_at < :end"
            " GROUP BY bucket ORDER BY bucket"
        ),
        {"start": start, "end": end, "width": width, "session": session_id, "parameter": parameter},
    )
    result = []
    for index, count, low, high, mean in rows:
        bucket_start = start + timedelta(microseconds=index * width)
        bucket_end = min(start + timedelta(microseconds=(index + 1) * width), end)
        rounded = Decimal(mean).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        result.append(
            Bucket(
                start=to_wire(bucket_start),
                end=to_wire(bucket_end),
                count=count,
                min=float(low),
                max=float(high),
                mean=float(rounded),
            )
        )
    return result


def trend(
    db: Session,
    session_id: uuid.UUID,
    parameter: str,
    start: datetime,
    end: datetime,
    max_points: int,
    limit: int,
    cursor: str | None,
) -> TrendPage:
    total = count_in_range(db, session_id, parameter, start, end)
    page = TrendPage(
        parameter=parameter,
        start=to_wire(start),
        end=to_wire(end),
        downsampled=total > max_points,
        total=total,
        items=[],
        next_cursor=None,
    )
    if page.downsampled:
        if cursor:
            # Buckets come in one response (at most max_points of them); a cursor makes no sense.
            raise ApiError(400, "INVALID_CURSOR", "Downsampled trends are not paginated")
        page.items = list(buckets(db, session_id, parameter, start, end, max_points))
        return page

    filters: Filters = {
        "session": str(session_id),
        "parameter": parameter,
        "from": to_wire(start),
        "to": to_wire(end),
        "max_points": max_points,
    }
    query = (
        select(Measurement, Device.serial)
        .join(Device, Device.id == Measurement.device_id)
        .where(
            Measurement.session_id == session_id,
            Measurement.parameter == parameter,
            Measurement.measured_at >= start,
            Measurement.measured_at < end,
        )
        .order_by(Measurement.measured_at, Measurement.id)
    )
    if cursor:
        after = decode_cursor(cursor, filters, ("measured_at", "id"))
        try:
            key = (datetime.fromisoformat(after["measured_at"]), int(after["id"]))
        except ValueError:
            raise ApiError(400, "INVALID_CURSOR", "Invalid cursor") from None
        query = query.where(tuple_(Measurement.measured_at, Measurement.id) > key)
    rows = list(db.execute(query.limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    page.items = [
        Reading(
            seq=m.seq,
            device_id=m.device_id,
            serial=serial,
            parameter=m.parameter,
            value=float(m.value),
            measured_at=to_wire(m.measured_at),
        )
        for m, serial in rows
    ]
    if has_more:
        last = rows[-1][0]
        page.next_cursor = encode_cursor(
            {"measured_at": last.measured_at.isoformat(), "id": str(last.id)}, filters
        )
    return page
