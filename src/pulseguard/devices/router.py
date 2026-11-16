"""HTTP routes of the device module."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from pulseguard.devices import service
from pulseguard.devices.schemas import (
    AssignmentInput,
    AssignmentOut,
    DeviceInput,
    DeviceKind,
    DeviceOut,
    DevicePage,
    DeviceStatus,
    RegisteredDevice,
)
from pulseguard.sessions.router import ActorDep, ClockDep, DbDep

router = APIRouter()


@router.post("/devices", status_code=201, response_model=RegisteredDevice)
def register(actor: ActorDep, db: DbDep, clock: ClockDep, body: DeviceInput) -> RegisteredDevice:
    return service.register(db, actor, clock, body)


@router.get("/devices", response_model=DevicePage)
def list_devices(
    actor: ActorDep,
    db: DbDep,
    status: DeviceStatus | None = None,
    kind: DeviceKind | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    cursor: str | None = None,
) -> DevicePage:
    return service.list_devices(db, actor, status, kind, limit, cursor)


@router.post("/devices/{device_id}/retire", response_model=DeviceOut)
def retire(actor: ActorDep, db: DbDep, clock: ClockDep, device_id: uuid.UUID) -> DeviceOut:
    return service.retire(db, actor, clock, device_id)


@router.post("/sessions/{session_id}/devices", status_code=201, response_model=AssignmentOut)
def assign(
    actor: ActorDep, db: DbDep, clock: ClockDep, session_id: uuid.UUID, body: AssignmentInput
) -> AssignmentOut:
    return service.assign(db, actor, clock, session_id, body)


@router.delete("/sessions/{session_id}/devices/{device_id}", status_code=204)
def unassign(
    actor: ActorDep, db: DbDep, clock: ClockDep, session_id: uuid.UUID, device_id: uuid.UUID
) -> Response:
    service.unassign(db, actor, clock, session_id, device_id)
    return Response(status_code=204)
