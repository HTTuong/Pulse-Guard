"""Device lifecycle (REQ-DEV-01 to REQ-DEV-04). Order of checks as in docs/api.md:
existence (404), permission (403), state (409).

    AVAILABLE --assign--> ASSIGNED --unassign--> AVAILABLE
    AVAILABLE --retire--> RETIRED (final)
"""

import base64
import binascii
import hashlib
import json
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pulseguard.access.actor import Actor, Role
from pulseguard.clock import Clock, to_wire
from pulseguard.devices import assignments
from pulseguard.devices.schemas import (
    AssignmentInput,
    AssignmentOut,
    Connection,
    DeviceInput,
    DeviceOut,
    DevicePage,
    DeviceStatus,
    RegisteredDevice,
)
from pulseguard.errors import ApiError, not_found
from pulseguard.models import Bed, Device, DeviceAssignment, DeviceConnection, MonitoringSession
from pulseguard.sessions.service import conflict_from, latest_stay


def hash_credential(credential: str) -> str:
    # The credential is 256 random bits, so a plain SHA-256 is enough; no password stretching.
    return hashlib.sha256(credential.encode()).hexdigest()


def device_out(db: Session, device: Device) -> DeviceOut:
    session_id = db.scalars(
        select(DeviceAssignment.session_id).where(
            DeviceAssignment.device_id == device.id, DeviceAssignment.unassigned_at.is_(None)
        )
    ).first()
    connection = db.get(DeviceConnection, device.id)
    return DeviceOut(
        id=device.id,
        serial=device.serial,
        kind=device.kind,  # type: ignore[arg-type]
        status=device.status,  # type: ignore[arg-type]
        session_id=session_id,
        connection=Connection(state="UNKNOWN")
        if connection is None
        else Connection(state=connection.state, since=to_wire(connection.since)),  # type: ignore[arg-type]
    )


def register(db: Session, actor: Actor, clock: Clock, data: DeviceInput) -> RegisteredDevice:
    """REQ-DEV-01, REQ-ACC-03."""
    actor.require_role(Role.TECHNICIAN)
    credential = secrets.token_urlsafe(32) if data.kind == "PATIENT_MONITOR" else None
    device = Device(
        serial=data.serial,
        kind=data.kind,
        status="AVAILABLE",
        credential_hash=hash_credential(credential) if credential else None,
        registered_at=clock.now(),
    )
    db.add(device)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise conflict_from(error) from None
    return RegisteredDevice(**device_out(db, device).model_dump(), credential=credential)


def retire(db: Session, actor: Actor, clock: Clock, device_id: uuid.UUID) -> DeviceOut:
    """REQ-DEV-04."""
    device = db.get(Device, device_id, with_for_update=True)
    if device is None:
        raise not_found("device")
    actor.require_role(Role.TECHNICIAN)
    if device.status == "RETIRED":
        raise ApiError(409, "DEVICE_RETIRED", "Device is retired")
    if device.status == "ASSIGNED":
        raise ApiError(409, "DEVICE_ASSIGNED", "Unassign the device before retiring it")
    device.status = "RETIRED"
    device.retired_at = clock.now()
    db.commit()
    return device_out(db, device)


def session_for_assignment(db: Session, actor: Actor, session_id: uuid.UUID) -> MonitoringSession:
    """Nurses of the session's unit and technicians may change its devices."""
    session = db.get(MonitoringSession, session_id, with_for_update=True)
    if session is None:
        raise not_found("session")
    actor.require_role(Role.NURSE, Role.TECHNICIAN)
    if actor.role == Role.NURSE:
        bed = db.get_one(Bed, latest_stay(db, session.id).bed_id)
        actor.require_unit(bed.unit_id)
    return session


def assign(
    db: Session, actor: Actor, clock: Clock, session_id: uuid.UUID, data: AssignmentInput
) -> AssignmentOut:
    """REQ-DEV-02. The session row is locked first, so two parallel assignments of the same kind
    to one session run one after the other; the second then sees the first (R-ISO-02)."""
    session = session_for_assignment(db, actor, session_id)
    device = db.get(Device, data.device_id, with_for_update=True)
    if device is None:
        raise not_found("device")
    if session.status != "OPEN":
        raise ApiError(409, "SESSION_CLOSED", "Session is closed")
    if device.status == "RETIRED":
        raise ApiError(409, "DEVICE_RETIRED", "Device is retired")
    if device.status == "ASSIGNED":
        raise ApiError(409, "DEVICE_NOT_AVAILABLE", "Device is assigned to a session")
    kinds = {d.kind for _, d in assignments.open_assignments(db, session.id)}
    if device.kind in kinds:
        raise ApiError(
            409, "SESSION_HAS_DEVICE_KIND", f"Session already has a {device.kind.lower()}"
        )
    now = clock.now()
    assignment = DeviceAssignment(
        device_id=device.id, session_id=session.id, assigned_at=now, assigned_by=actor.user_id
    )
    device.status = "ASSIGNED"
    db.add(assignment)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise conflict_from(error) from None
    return AssignmentOut(
        device_id=device.id,
        serial=device.serial,
        kind=device.kind,  # type: ignore[arg-type]
        session_id=session.id,
        assigned_at=to_wire(now),
    )


def unassign(
    db: Session, actor: Actor, clock: Clock, session_id: uuid.UUID, device_id: uuid.UUID
) -> None:
    """REQ-DEV-03."""
    session = session_for_assignment(db, actor, session_id)
    for assignment, device in assignments.open_assignments(db, session.id):
        if device.id == device_id:
            assignments.end_assignment(assignment, device, clock.now(), actor.user_id)
            db.commit()
            return
    raise not_found("device assigned to this session")


# Cursor pagination (docs/api.md, Pagination). The cursor carries the last serial and the
# filters it was made for, so it cannot be reused with other filters.


def encode_cursor(after: str, filters: dict[str, str | None]) -> str:
    raw = json.dumps({"after": after, "filters": filters}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, filters: dict[str, str | None]) -> str:
    invalid = ApiError(400, "INVALID_CURSOR", "Invalid cursor")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise invalid from None
    if not isinstance(data, dict) or not isinstance(data.get("after"), str):
        raise invalid
    if data.get("filters") != filters:
        raise ApiError(400, "INVALID_CURSOR", "Cursor was made for other filters")
    after: str = data["after"]
    return after


def list_devices(
    db: Session,
    actor: Actor,
    status: DeviceStatus | None,
    kind: str | None,
    limit: int,
    cursor: str | None,
) -> DevicePage:
    """Ordered by serial, which is unique, so a page boundary is stable while devices are added."""
    actor.require_role(Role.TECHNICIAN, Role.NURSE)
    filters: dict[str, str | None] = {"status": status, "kind": kind}
    query = select(Device).order_by(Device.serial)
    if status:
        query = query.where(Device.status == status)
    if kind:
        query = query.where(Device.kind == kind)
    if cursor:
        query = query.where(Device.serial > decode_cursor(cursor, filters))
    devices = list(db.scalars(query.limit(limit + 1)))
    has_more = len(devices) > limit
    devices = devices[:limit]
    next_cursor = encode_cursor(devices[-1].serial, filters) if has_more else None
    return DevicePage(items=[device_out(db, d) for d in devices], next_cursor=next_cursor)
