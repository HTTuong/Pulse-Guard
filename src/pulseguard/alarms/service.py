"""Alarms in the database: running the engine on stored readings, acknowledging, discharge.

The engine (engine.py) decides; this module loads its inputs, stores its outputs and writes every
change to alarm_event, with the user when a person made it (REQ-ALM-03)."""

import logging
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulseguard.access.actor import Actor, Role
from pulseguard.alarms import engine
from pulseguard.alarms.limits import DEFAULT_LIMITS, alarm_code
from pulseguard.alarms.schemas import AlarmEventOut, AlarmOut, AlarmWithHistory, LimitsOut
from pulseguard.clock import Clock, to_wire
from pulseguard.errors import ApiError, not_found
from pulseguard.models import (
    Alarm,
    AlarmEngineState,
    AlarmEvent,
    Bed,
    BedStay,
    MonitoringSession,
    SessionLimit,
)

log = logging.getLogger(__name__)


def add_default_limits(db: Session, session_id: uuid.UUID, at: datetime) -> None:
    """Called by admission, in its transaction."""
    for parameter, limits in DEFAULT_LIMITS.items():
        db.add(
            SessionLimit(
                session_id=session_id,
                parameter=parameter,
                high_low=limits.high_low,
                medium_low=limits.medium_low,
                medium_high=limits.medium_high,
                high_high=limits.high_high,
                changed_at=at,
                changed_by=None,
            )
        )


def limits_of(db: Session, session_id: uuid.UUID, parameter: str) -> engine.Limits:
    row = db.get(SessionLimit, (session_id, parameter))
    if row is None:
        # A monitored patient without limits must still alarm: fall back to the defaults.
        log.warning("session %s has no %s limits; using the defaults", session_id, parameter)
        return DEFAULT_LIMITS[parameter]
    return engine.Limits(row.high_low, row.medium_low, row.medium_high, row.high_high)


def open_alarm(db: Session, session_id: uuid.UUID, code: str) -> Alarm | None:
    return db.scalars(
        select(Alarm)
        .where(Alarm.session_id == session_id, Alarm.code == code, Alarm.state != "RESOLVED")
        .with_for_update()
    ).first()


def as_engine_alarm(alarm: Alarm | None) -> engine.OpenAlarm | None:
    if alarm is None:
        return None
    return engine.OpenAlarm(alarm.priority, alarm.state, alarm.condition_cleared)  # type: ignore[arg-type]


def record(
    db: Session,
    alarm: Alarm,
    at: datetime,
    from_state: str | None,
    reason: str,
    by: uuid.UUID | None = None,
) -> None:
    db.add(
        AlarmEvent(
            alarm_id=alarm.id,
            at=at,
            from_state=from_state,
            to_state=alarm.state,
            priority=alarm.priority,
            by_user=by,
            reason=reason,
        )
    )


def apply_change(
    db: Session,
    session_id: uuid.UUID,
    parameter: str,
    code: str,
    alarm: Alarm | None,
    change: engine.Change,
) -> Alarm | None:
    if change.kind == "RAISE":
        if change.priority is None:
            raise RuntimeError("engine raised an alarm without a priority")
        alarm = Alarm(
            id=uuid.uuid4(),
            session_id=session_id,
            kind="PHYSIOLOGICAL",
            code=code,
            parameter=parameter,
            priority=change.priority,
            state="ACTIVE",
            condition_cleared=False,
            escalated=False,
            started_at=change.at,
        )
        db.add(alarm)
        db.flush()
        record(db, alarm, change.at, None, f"{parameter} in the {change.priority} band")
        return alarm
    if alarm is None:
        raise RuntimeError(f"engine changed {code} of session {session_id} with no open alarm")
    before = alarm.state
    if change.kind == "ESCALATE":
        alarm.priority, alarm.state = "HIGH", "ACTIVE"
        alarm.acknowledged_at = alarm.acknowledged_by = None
        alarm.condition_cleared = False
        record(db, alarm, change.at, before, f"{parameter} in the HIGH band: escalated")
    elif change.kind == "CLEARED":
        alarm.condition_cleared = True
        record(db, alarm, change.at, before, "Condition cleared; waits for acknowledgement")
    elif change.kind == "RECURRED":
        alarm.condition_cleared = False
        record(db, alarm, change.at, before, "Condition is back")
    elif change.kind == "RESOLVE":
        alarm.state, alarm.resolved_at, alarm.resolution = "RESOLVED", change.at, "AUTO_RESOLVED"
        record(db, alarm, change.at, before, "Condition cleared: resolved")
        return None
    return alarm


def process_readings(
    db: Session, session_id: uuid.UUID, parameter: str, readings: list[tuple[datetime, Decimal]]
) -> int:
    """Run the engine over newly stored readings of one parameter, oldest first. Returns how many
    readings were skipped as older than what the engine had already seen (R-ORD-02)."""
    session = db.get(MonitoringSession, session_id, with_for_update=True)
    if session is None or session.status != "OPEN":
        # Readings measured before a discharge are kept (F-03) but never alarm a closed session.
        return 0
    limits = limits_of(db, session_id, parameter)
    skipped = 0
    for side in engine.watched_sides(limits):
        code = alarm_code(parameter, side)
        row = db.get(AlarmEngineState, (session_id, code), with_for_update=True)
        if row is None:
            row = AlarmEngineState(session_id=session_id, code=code)
            db.add(row)
        state = engine.SideState(row.last_at, row.abnormal_since, row.high_since, row.normal_since)
        alarm = open_alarm(db, session_id, code)
        for measured_at, value in sorted(readings):
            outcome = engine.step(
                state, as_engine_alarm(alarm), engine.band(limits, side, value), measured_at
            )
            skipped += outcome.skipped
            state = outcome.state
            for change in outcome.changes:
                alarm = apply_change(db, session_id, parameter, code, alarm, change)
        row.last_at, row.abnormal_since = state.last_at, state.abnormal_since
        row.high_since, row.normal_since = state.high_since, state.normal_since
    if skipped:
        log.warning(
            "%s late %s readings of session %s not run through the engine",
            skipped,
            parameter,
            session_id,
        )
    return skipped


def resolve_on_discharge(db: Session, session_id: uuid.UUID, at: datetime, by: uuid.UUID) -> None:
    """REQ-SES-03, REQ-ALM-08: in the discharge transaction."""
    for alarm in db.scalars(
        select(Alarm).where(Alarm.session_id == session_id, Alarm.state != "RESOLVED")
    ):
        before = alarm.state
        alarm.state, alarm.resolved_at, alarm.resolution = "RESOLVED", at, "DISCHARGED"
        record(db, alarm, at, before, "Patient discharged", by)


# Staff actions


def unit_of_session(db: Session, session_id: uuid.UUID) -> uuid.UUID:
    stay = db.scalars(
        select(BedStay).where(BedStay.session_id == session_id).order_by(BedStay.from_at.desc())
    ).first()
    if stay is None:
        raise RuntimeError(f"session {session_id} has no bed stay")
    return db.get_one(Bed, stay.bed_id).unit_id


def wire(at: datetime | None) -> str | None:
    return to_wire(at) if at else None


def alarm_out(alarm: Alarm) -> AlarmOut:
    return AlarmOut(
        id=alarm.id,
        session_id=alarm.session_id,
        kind=alarm.kind,
        code=alarm.code,
        parameter=alarm.parameter,
        device_id=alarm.device_id,
        priority=alarm.priority,
        state=alarm.state,
        condition_cleared=alarm.condition_cleared,
        escalated=alarm.escalated,
        started_at=to_wire(alarm.started_at),
        acknowledged_at=wire(alarm.acknowledged_at),
        acknowledged_by=alarm.acknowledged_by,
        resolved_at=wire(alarm.resolved_at),
        resolution=alarm.resolution,
    )


def load_for_staff(db: Session, actor: Actor, alarm_id: uuid.UUID, lock: bool = False) -> Alarm:
    alarm = db.get(Alarm, alarm_id, with_for_update=lock)
    if alarm is None:
        raise not_found("alarm")
    actor.require_role(Role.NURSE, Role.PHYSICIAN)
    actor.require_unit(unit_of_session(db, alarm.session_id))
    return alarm


def get_alarm(db: Session, actor: Actor, alarm_id: uuid.UUID) -> AlarmWithHistory:
    alarm = load_for_staff(db, actor, alarm_id)
    events = db.scalars(
        select(AlarmEvent).where(AlarmEvent.alarm_id == alarm.id).order_by(AlarmEvent.id)
    )
    history = [
        AlarmEventOut(
            at=to_wire(e.at),
            from_state=e.from_state,
            to_state=e.to_state,
            priority=e.priority,
            by_user=e.by_user,
            reason=e.reason,
        )
        for e in events
    ]
    return AlarmWithHistory(**alarm_out(alarm).model_dump(), history=history)


def acknowledge(db: Session, actor: Actor, clock: Clock, alarm_id: uuid.UUID) -> AlarmOut:
    """REQ-ALM-03, REQ-ALM-04. The row is locked, so two nurses pressing at once get one 200 and
    one 409."""
    alarm = load_for_staff(db, actor, alarm_id, lock=True)
    if alarm.state != "ACTIVE":
        raise ApiError(409, "ALARM_NOT_ACTIVE", f"Alarm is {alarm.state.lower()}")
    now = clock.now()
    alarm.acknowledged_at, alarm.acknowledged_by = now, actor.user_id
    if alarm.condition_cleared:
        alarm.state, alarm.resolved_at = "RESOLVED", now
        alarm.resolution = "ACKNOWLEDGED_AFTER_CLEAR"
        record(db, alarm, now, "ACTIVE", "Acknowledged after the condition cleared", actor.user_id)
    else:
        alarm.state = "ACKNOWLEDGED"
        record(db, alarm, now, "ACTIVE", "Acknowledged", actor.user_id)
    db.commit()
    return alarm_out(alarm)


def session_limits(db: Session, actor: Actor, session_id: uuid.UUID) -> list[LimitsOut]:
    if db.get(MonitoringSession, session_id) is None:
        raise not_found("session")
    actor.require_role(Role.NURSE, Role.PHYSICIAN)
    actor.require_unit(unit_of_session(db, session_id))
    rows = db.scalars(
        select(SessionLimit)
        .where(SessionLimit.session_id == session_id)
        .order_by(SessionLimit.parameter)
    )

    def num(value: Decimal | None) -> float | None:
        return float(value) if value is not None else None

    return [
        LimitsOut(
            parameter=r.parameter,
            high_low=num(r.high_low),
            medium_low=num(r.medium_low),
            medium_high=num(r.medium_high),
            high_high=num(r.high_high),
            changed_at=to_wire(r.changed_at),
            changed_by=r.changed_by,
        )
        for r in rows
    ]
