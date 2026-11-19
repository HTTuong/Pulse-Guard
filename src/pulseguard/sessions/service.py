"""Rules of admission and discharge. Checks run in the order of docs/api.md:
existence (404), then permission (403), then state (409)."""

import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pulseguard.access.actor import Actor, Role
from pulseguard.alarms import service as alarms
from pulseguard.clock import Clock, to_wire
from pulseguard.devices import assignments
from pulseguard.errors import CONSTRAINT_CODES, ApiError, not_found
from pulseguard.models import Bed, BedStay, MonitoringSession, Patient
from pulseguard.sessions.schemas import (
    AdmissionInput,
    PatientInput,
    PatientOut,
    SessionOut,
)

log = logging.getLogger(__name__)


def conflict_from(error: IntegrityError) -> ApiError:
    """Turn a refused write into its 409, by the name of the constraint that refused it."""
    name = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    if name in CONSTRAINT_CODES:
        status, code, title = CONSTRAINT_CODES[name]
        return ApiError(status, code, title)
    raise error


def patient_out(patient: Patient) -> PatientOut:
    return PatientOut(
        id=patient.id,
        mrn=patient.mrn,
        given_name=patient.given_name,
        family_name=patient.family_name,
        birth_year=patient.birth_year,
    )


def latest_stay(db: Session, session_id: uuid.UUID) -> BedStay:
    stay = db.scalars(
        select(BedStay).where(BedStay.session_id == session_id).order_by(BedStay.from_at.desc())
    ).first()
    if stay is None:  # every session is created together with its first stay
        raise RuntimeError(f"session {session_id} has no bed stay")
    return stay


def session_out(db: Session, session: MonitoringSession) -> SessionOut:
    stay = latest_stay(db, session.id)
    bed = db.get_one(Bed, stay.bed_id)
    patient = db.get_one(Patient, session.patient_id)
    is_open = session.status == "OPEN"
    return SessionOut(
        id=session.id,
        patient=patient_out(patient),
        status="OPEN" if is_open else "CLOSED",
        unit_id=bed.unit_id,
        bed_id=bed.id if is_open else None,
        admitted_at=to_wire(session.admitted_at),
        discharged_at=to_wire(session.discharged_at) if session.discharged_at else None,
        devices=[
            {
                "device_id": str(device.id),
                "serial": device.serial,
                "kind": device.kind,
                "session_id": str(session.id),
                "assigned_at": to_wire(assignment.assigned_at),
            }
            for assignment, device in assignments.open_assignments(db, session.id)
        ],
    )


def create_patient(db: Session, actor: Actor, data: PatientInput) -> PatientOut:
    actor.require_role(Role.NURSE)
    patient = Patient(**data.model_dump())
    db.add(patient)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise conflict_from(error) from None
    return patient_out(patient)


def admit(db: Session, actor: Actor, clock: Clock, data: AdmissionInput) -> SessionOut:
    """REQ-SES-01, REQ-SES-02."""
    patient = db.get(Patient, data.patient_id)
    if patient is None:
        raise not_found("patient")
    bed = db.get(Bed, data.bed_id)
    if bed is None:
        raise not_found("bed")
    actor.require_role(Role.NURSE)
    actor.require_unit(bed.unit_id)

    open_session = db.scalars(
        select(MonitoringSession).where(
            MonitoringSession.patient_id == patient.id, MonitoringSession.status == "OPEN"
        )
    ).first()
    if open_session is not None:
        raise ApiError(409, "PATIENT_HAS_OPEN_SESSION", "Patient already has an open session")
    occupied = db.scalars(
        select(BedStay).where(BedStay.bed_id == bed.id, BedStay.to_at.is_(None))
    ).first()
    if occupied is not None:
        raise ApiError(409, "BED_OCCUPIED", "Bed is occupied")

    now = clock.now()
    session = MonitoringSession(
        patient_id=patient.id, status="OPEN", admitted_at=now, admitted_by=actor.user_id
    )
    db.add(session)
    try:
        db.flush()
        db.add(BedStay(session_id=session.id, bed_id=bed.id, from_at=now))
        alarms.add_default_limits(db, session.id, now)
        db.commit()
    except IntegrityError as error:
        # A parallel admission got there first; the database refused this one (R-ISO-02).
        db.rollback()
        raise conflict_from(error) from None
    return session_out(db, session)


def load_for_staff(db: Session, actor: Actor, session_id: uuid.UUID) -> MonitoringSession:
    session = db.get(MonitoringSession, session_id)
    if session is None:
        raise not_found("session")
    actor.require_role(Role.NURSE, Role.PHYSICIAN)
    bed = db.get_one(Bed, latest_stay(db, session.id).bed_id)
    actor.require_unit(bed.unit_id)
    return session


def get_session(db: Session, actor: Actor, session_id: uuid.UUID) -> SessionOut:
    return session_out(db, load_for_staff(db, actor, session_id))


def discharge(db: Session, actor: Actor, clock: Clock, session_id: uuid.UUID) -> SessionOut:
    """REQ-SES-03: closes the session, frees the bed, unassigns every device and resolves every
    open alarm, in one transaction."""
    session = load_for_staff(db, actor, session_id)
    actor.require_role(Role.NURSE)
    if session.status != "OPEN":
        raise ApiError(409, "SESSION_CLOSED", "Session is already closed")
    stay = latest_stay(db, session.id)
    # Never before the current stay began: the wall clock can be set back, and a discharge must
    # still succeed (found in d240, migration 0002).
    now: datetime = clock.now()
    ended = max(now, stay.from_at)
    if ended != now:
        log.warning("clock before the bed stay of session %s; discharge time clamped", session.id)
    session.status = "CLOSED"
    session.discharged_at = ended
    session.discharged_by = actor.user_id
    stay.to_at = ended
    assignments.unassign_all(db, session.id, ended, actor.user_id)
    alarms.resolve_on_discharge(db, session.id, ended, actor.user_id)
    db.commit()
    return session_out(db, session)
