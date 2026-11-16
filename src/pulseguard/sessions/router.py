"""HTTP routes of the sessions module."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from pulseguard.access.actor import Actor, current_actor
from pulseguard.clock import Clock
from pulseguard.db import get_db
from pulseguard.sessions import service
from pulseguard.sessions.schemas import AdmissionInput, PatientInput, PatientOut, SessionOut

router = APIRouter()

# Authentication is resolved before the body is read, so a request without a valid token
# gets 401 even when its body is also wrong (docs/api.md, order of checks).
ActorDep = Annotated[Actor, Depends(current_actor)]
DbDep = Annotated[Session, Depends(get_db)]


def clock_of(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


ClockDep = Annotated[Clock, Depends(clock_of)]


@router.post("/patients", status_code=201, response_model=PatientOut)
def create_patient(actor: ActorDep, db: DbDep, body: PatientInput) -> PatientOut:
    return service.create_patient(db, actor, body)


@router.post("/sessions", status_code=201, response_model=SessionOut)
def admit(actor: ActorDep, db: DbDep, clock: ClockDep, body: AdmissionInput) -> SessionOut:
    return service.admit(db, actor, clock, body)


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(actor: ActorDep, db: DbDep, session_id: uuid.UUID) -> SessionOut:
    return service.get_session(db, actor, session_id)


@router.post("/sessions/{session_id}/discharge", response_model=SessionOut)
def discharge(actor: ActorDep, db: DbDep, clock: ClockDep, session_id: uuid.UUID) -> SessionOut:
    return service.discharge(db, actor, clock, session_id)
