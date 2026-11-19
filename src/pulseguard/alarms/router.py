"""HTTP routes of the alarm module."""

import uuid

from fastapi import APIRouter

from pulseguard.alarms import service
from pulseguard.alarms.schemas import AlarmOut, AlarmWithHistory, LimitsOut
from pulseguard.sessions.router import ActorDep, ClockDep, DbDep

router = APIRouter()


@router.get("/alarms/{alarm_id}", response_model=AlarmWithHistory)
def get_alarm(actor: ActorDep, db: DbDep, alarm_id: uuid.UUID) -> AlarmWithHistory:
    return service.get_alarm(db, actor, alarm_id)


@router.post("/alarms/{alarm_id}/acknowledge", response_model=AlarmOut)
def acknowledge(actor: ActorDep, db: DbDep, clock: ClockDep, alarm_id: uuid.UUID) -> AlarmOut:
    return service.acknowledge(db, actor, clock, alarm_id)


@router.get("/sessions/{session_id}/limits", response_model=list[LimitsOut])
def session_limits(actor: ActorDep, db: DbDep, session_id: uuid.UUID) -> list[LimitsOut]:
    return service.session_limits(db, actor, session_id)
