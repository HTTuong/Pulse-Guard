"""History routes: trends of a parameter and alarms of a session over a time range."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy.orm import Session

from pulseguard.access.actor import Actor, Role
from pulseguard.alarms.history import AlarmPage, alarms_in_range
from pulseguard.alarms.service import unit_of_session
from pulseguard.errors import not_found
from pulseguard.measurements.history import TrendPage, trend
from pulseguard.models import MonitoringSession
from pulseguard.paging import time_range
from pulseguard.sessions.router import ActorDep, DbDep

router = APIRouter()
Limit = Annotated[int, Query(ge=1, le=500)]
# Required strings, parsed by time_range: a time without an offset is 400, never local time.
From = Annotated[str, Query(alias="from")]
To = Annotated[str, Query(alias="to")]


def check_access(db: Session, actor: Actor, session_id: uuid.UUID) -> None:
    """Nurses and physicians of the session's unit, open or closed sessions alike."""
    if db.get(MonitoringSession, session_id) is None:
        raise not_found("session")
    actor.require_role(Role.NURSE, Role.PHYSICIAN)
    actor.require_unit(unit_of_session(db, session_id))


@router.get("/sessions/{session_id}/measurements", response_model=TrendPage)
def measurements(
    actor: ActorDep,
    db: DbDep,
    session_id: uuid.UUID,
    parameter: Literal["PR", "SPO2", "RR"],
    start: From,
    end: To,
    max_points: Annotated[int, Query(ge=10, le=2000)] = 500,
    limit: Limit = 50,
    cursor: str | None = None,
) -> TrendPage:
    begin, finish = time_range(start, end)
    check_access(db, actor, session_id)
    return trend(db, session_id, parameter, begin, finish, max_points, limit, cursor)


@router.get("/sessions/{session_id}/alarms", response_model=AlarmPage)
def session_alarms(
    actor: ActorDep,
    db: DbDep,
    session_id: uuid.UUID,
    start: From,
    end: To,
    limit: Limit = 50,
    cursor: str | None = None,
) -> AlarmPage:
    begin, finish = time_range(start, end)
    check_access(db, actor, session_id)
    return alarms_in_range(db, session_id, begin, finish, limit, cursor)
