"""Alarms of one session that overlap a time range (REQ-VIEW-03), and the rule that ties an
alarm's history to its current state.

An alarm overlaps [start, end) when it started before `end` and was not resolved before `start`:
an alarm still open at `end` counts, and one resolved exactly at `start` does too, so the chart of
[start, end) shows it ending there."""

import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import Session

from pulseguard.alarms.schemas import AlarmOut
from pulseguard.alarms.service import alarm_out
from pulseguard.clock import to_wire
from pulseguard.errors import ApiError
from pulseguard.models import Alarm, AlarmEvent
from pulseguard.paging import Filters, decode_cursor, encode_cursor

# Every step an alarm may take, as (from_state, to_state). None is "before the alarm existed".
ALLOWED_STEPS = {
    (None, "ACTIVE"),
    ("ACTIVE", "ACTIVE"),  # cleared, recurred, escalated
    ("ACTIVE", "ACKNOWLEDGED"),
    ("ACKNOWLEDGED", "ACTIVE"),  # escalated after acknowledgement
    ("ACTIVE", "RESOLVED"),
    ("ACKNOWLEDGED", "RESOLVED"),
}


class AlarmPage(BaseModel):
    start: str
    end: str
    items: list[AlarmOut]
    next_cursor: str | None


def history_problems(alarm: Alarm, events: list[AlarmEvent]) -> list[str]:
    """Why an alarm's history does not lead to its current state; empty when it does. Used by the
    tests on every alarm they create, and cheap enough to run in a health check later."""
    problems = []
    if not events:
        return [f"{alarm.id}: no history"]
    previous: str | None = None
    for event in events:
        if event.from_state != previous:
            problems.append(f"{alarm.id}: step from {event.from_state} after {previous}")
        if (event.from_state, event.to_state) not in ALLOWED_STEPS:
            problems.append(f"{alarm.id}: step {event.from_state} to {event.to_state} not allowed")
        previous = event.to_state
    last = events[-1]
    if last.to_state != alarm.state:
        problems.append(f"{alarm.id}: history ends {last.to_state}, alarm is {alarm.state}")
    if last.priority != alarm.priority:
        problems.append(f"{alarm.id}: history ends {last.priority}, alarm is {alarm.priority}")
    if events[0].at != alarm.started_at:
        problems.append(f"{alarm.id}: history starts {events[0].at}, alarm {alarm.started_at}")
    if alarm.state == "RESOLVED" and last.at != alarm.resolved_at:
        problems.append(f"{alarm.id}: resolved at {alarm.resolved_at}, history says {last.at}")
    acknowledged = [
        e
        for e in events
        if e.by_user is not None
        and e.from_state == "ACTIVE"
        and e.to_state in ("ACKNOWLEDGED", "RESOLVED")
    ]
    if alarm.acknowledged_at is not None and (
        not acknowledged or acknowledged[-1].at != alarm.acknowledged_at
    ):
        problems.append(f"{alarm.id}: acknowledged at {alarm.acknowledged_at} not in the history")
    return problems


def alarms_in_range(
    db: Session,
    session_id: uuid.UUID,
    start: datetime,
    end: datetime,
    limit: int,
    cursor: str | None,
) -> AlarmPage:
    filters: Filters = {"session": str(session_id), "from": to_wire(start), "to": to_wire(end)}
    query = (
        select(Alarm)
        .where(
            Alarm.session_id == session_id,
            Alarm.started_at < end,
            or_(Alarm.resolved_at.is_(None), Alarm.resolved_at >= start),
        )
        .order_by(Alarm.started_at, Alarm.id)
    )
    if cursor:
        after = decode_cursor(cursor, filters, ("started_at", "id"))
        try:
            key = (datetime.fromisoformat(after["started_at"]), uuid.UUID(after["id"]))
        except ValueError:
            raise ApiError(400, "INVALID_CURSOR", "Invalid cursor") from None
        query = query.where(tuple_(Alarm.started_at, Alarm.id) > key)
    rows = list(db.scalars(query.limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more:
        last = rows[-1]
        next_cursor = encode_cursor(
            {"started_at": last.started_at.isoformat(), "id": str(last.id)}, filters
        )
    return AlarmPage(
        start=to_wire(start),
        end=to_wire(end),
        items=[alarm_out(a) for a in rows],
        next_cursor=next_cursor,
    )
