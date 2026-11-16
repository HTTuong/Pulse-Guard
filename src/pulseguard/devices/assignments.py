"""Queries on assignment history, shared with the sessions and ingest modules.
This file imports no other module, so any module may call it."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from pulseguard.models import Device, DeviceAssignment


def open_assignments(db: Session, session_id: uuid.UUID) -> list[tuple[DeviceAssignment, Device]]:
    rows = db.execute(
        select(DeviceAssignment, Device)
        .join(Device, Device.id == DeviceAssignment.device_id)
        .where(DeviceAssignment.session_id == session_id, DeviceAssignment.unassigned_at.is_(None))
        .order_by(Device.kind, Device.serial)
    ).all()
    return [(row[0], row[1]) for row in rows]


def end_assignment(
    assignment: DeviceAssignment, device: Device, at: datetime, by: uuid.UUID
) -> None:
    """Close an assignment; never before it began, because the wall clock can be set back
    (F-02 in docs/findings.md, migration 0003)."""
    assignment.unassigned_at = max(at, assignment.assigned_at)
    assignment.unassigned_by = by
    device.status = "AVAILABLE"


def unassign_all(db: Session, session_id: uuid.UUID, at: datetime, by: uuid.UUID) -> int:
    """Used by discharge, in its transaction (REQ-SES-03, REQ-DEV-03)."""
    rows = open_assignments(db, session_id)
    for assignment, device in rows:
        end_assignment(assignment, device, at, by)
    return len(rows)


def session_of_device_at(db: Session, device_id: uuid.UUID, at: datetime) -> uuid.UUID | None:
    """The session a device was assigned to at a moment, or None (REQ-MEA-06, R-ISO-01).
    Ranges are half open: at the moment of a handover the device belongs to the new session."""
    return db.scalars(
        select(DeviceAssignment.session_id).where(
            DeviceAssignment.device_id == device_id,
            DeviceAssignment.assigned_at <= at,
            or_(DeviceAssignment.unassigned_at.is_(None), DeviceAssignment.unassigned_at > at),
        )
    ).first()


@dataclass(frozen=True)
class Pairings:
    """What a patient monitor may accept (protocol.md, P-09 and P-10)."""

    monitor_serial: str
    paired: list[str]
    unpaired: list[tuple[str, datetime]]


def pairings_for_monitor(db: Session, monitor_id: uuid.UUID, since: datetime) -> Pairings:
    """Sensors in the same open session as the monitor, and sensors unpaired from that session
    since `since`, so the monitor can still accept their readings measured before unpairing."""
    monitor = db.get_one(Device, monitor_id)
    session_id = db.scalars(
        select(DeviceAssignment.session_id).where(
            DeviceAssignment.device_id == monitor_id, DeviceAssignment.unassigned_at.is_(None)
        )
    ).first()
    if session_id is None:
        return Pairings(monitor.serial, [], [])
    rows = db.execute(
        select(Device.serial, DeviceAssignment.unassigned_at)
        .join(Device, Device.id == DeviceAssignment.device_id)
        .where(
            DeviceAssignment.session_id == session_id,
            Device.kind != "PATIENT_MONITOR",
            or_(
                DeviceAssignment.unassigned_at.is_(None),
                and_(DeviceAssignment.unassigned_at >= since),
            ),
        )
        .order_by(Device.serial, DeviceAssignment.assigned_at)
    ).all()
    paired = [serial for serial, ended in rows if ended is None]
    unpaired = [(serial, ended) for serial, ended in rows if ended is not None]
    return Pairings(monitor.serial, paired, unpaired)
