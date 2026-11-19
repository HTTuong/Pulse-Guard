"""ORM mapping of the tables built by the migrations. The migrations own the schema; these
classes only describe it, so they never create or change a table."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import ENUM, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Timestamp = TIMESTAMP(timezone=True)


class Base(DeclarativeBase):
    pass


class Unit(Base):
    __tablename__ = "unit"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    time_zone: Mapped[str] = mapped_column(Text)


class Bed(Base):
    __tablename__ = "bed"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("unit.id"))
    label: Mapped[str] = mapped_column(Text)


class AppUser(Base):
    __tablename__ = "app_user"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(
        ENUM("NURSE", "PHYSICIAN", "TECHNICIAN", "ADMIN", name="user_role", create_type=False)
    )
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool]
    token_version: Mapped[int]


class Patient(Base):
    __tablename__ = "patient"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    mrn: Mapped[str] = mapped_column(Text)
    given_name: Mapped[str] = mapped_column(Text)
    family_name: Mapped[str] = mapped_column(Text)
    birth_year: Mapped[int | None]


class MonitoringSession(Base):
    __tablename__ = "monitoring_session"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"))
    status: Mapped[str] = mapped_column(
        ENUM("OPEN", "CLOSED", name="session_status", create_type=False)
    )
    admitted_at: Mapped[datetime] = mapped_column(Timestamp)
    admitted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    discharged_at: Mapped[datetime | None] = mapped_column(Timestamp)
    discharged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class BedStay(Base):
    __tablename__ = "bed_stay"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitoring_session.id"))
    bed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bed.id"))
    from_at: Mapped[datetime] = mapped_column(Timestamp)
    to_at: Mapped[datetime | None] = mapped_column(Timestamp)


DEVICE_KINDS = ("PATIENT_MONITOR", "PULSE_OXIMETER", "RESPIRATION")
DEVICE_STATUSES = ("AVAILABLE", "ASSIGNED", "RETIRED")


class Device(Base):
    __tablename__ = "device"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    serial: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(ENUM(*DEVICE_KINDS, name="device_kind", create_type=False))
    status: Mapped[str] = mapped_column(
        ENUM(*DEVICE_STATUSES, name="device_status", create_type=False)
    )
    credential_hash: Mapped[str | None] = mapped_column(Text)
    registered_at: Mapped[datetime] = mapped_column(Timestamp)
    retired_at: Mapped[datetime | None] = mapped_column(Timestamp)


class DeviceAssignment(Base):
    __tablename__ = "device_assignment"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device.id"))
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitoring_session.id"))
    assigned_at: Mapped[datetime] = mapped_column(Timestamp)
    assigned_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    unassigned_at: Mapped[datetime | None] = mapped_column(Timestamp)
    unassigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


class DeviceConnection(Base):
    __tablename__ = "device_connection"
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device.id"), primary_key=True)
    state: Mapped[str] = mapped_column(
        ENUM("CONNECTED", "LOST", name="connection_state", create_type=False)
    )
    since: Mapped[datetime] = mapped_column(Timestamp)
    last_seen_at: Mapped[datetime] = mapped_column(Timestamp)


PARAMETERS = ("PR", "SPO2", "RR")


class Measurement(Base):
    __tablename__ = "measurement"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitoring_session.id"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device.id"))
    seq: Mapped[int]
    parameter: Mapped[str] = mapped_column(ENUM(*PARAMETERS, name="parameter", create_type=False))
    value: Mapped[Decimal] = mapped_column(Numeric(5, 1))
    measured_at: Mapped[datetime] = mapped_column(Timestamp)
    received_at: Mapped[datetime] = mapped_column(Timestamp)


class ReadingRejection(Base):
    __tablename__ = "reading_rejection"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_serial: Mapped[str] = mapped_column(Text)
    seq: Mapped[int | None]
    code: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(Timestamp)


class SessionLimit(Base):
    __tablename__ = "session_limit"
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monitoring_session.id"), primary_key=True
    )
    parameter: Mapped[str] = mapped_column(
        ENUM(*PARAMETERS, name="parameter", create_type=False), primary_key=True
    )
    high_low: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    medium_low: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    medium_high: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    high_high: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    changed_at: Mapped[datetime] = mapped_column(Timestamp)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))


ALARM_STATES = ("ACTIVE", "ACKNOWLEDGED", "RESOLVED")
PRIORITIES = ("MEDIUM", "HIGH")


class Alarm(Base):
    __tablename__ = "alarm"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitoring_session.id"))
    kind: Mapped[str] = mapped_column(
        ENUM("PHYSIOLOGICAL", "TECHNICAL", name="alarm_kind", create_type=False)
    )
    code: Mapped[str] = mapped_column(Text)
    parameter: Mapped[str | None] = mapped_column(
        ENUM(*PARAMETERS, name="parameter", create_type=False)
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device.id"))
    priority: Mapped[str] = mapped_column(
        ENUM(*PRIORITIES, name="alarm_priority", create_type=False)
    )
    state: Mapped[str] = mapped_column(ENUM(*ALARM_STATES, name="alarm_state", create_type=False))
    condition_cleared: Mapped[bool]
    escalated: Mapped[bool]
    started_at: Mapped[datetime] = mapped_column(Timestamp)
    acknowledged_at: Mapped[datetime | None] = mapped_column(Timestamp)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(Timestamp)
    resolution: Mapped[str | None] = mapped_column(Text)


class AlarmEvent(Base):
    __tablename__ = "alarm_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    alarm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alarm.id"))
    at: Mapped[datetime] = mapped_column(Timestamp)
    from_state: Mapped[str | None] = mapped_column(
        ENUM(*ALARM_STATES, name="alarm_state", create_type=False)
    )
    to_state: Mapped[str] = mapped_column(
        ENUM(*ALARM_STATES, name="alarm_state", create_type=False)
    )
    priority: Mapped[str] = mapped_column(
        ENUM(*PRIORITIES, name="alarm_priority", create_type=False)
    )
    by_user: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    reason: Mapped[str] = mapped_column(Text)


class AlarmEngineState(Base):
    __tablename__ = "alarm_engine_state"
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monitoring_session.id"), primary_key=True
    )
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    last_at: Mapped[datetime | None] = mapped_column(Timestamp)
    abnormal_since: Mapped[datetime | None] = mapped_column(Timestamp)
    high_since: Mapped[datetime | None] = mapped_column(Timestamp)
    normal_since: Mapped[datetime | None] = mapped_column(Timestamp)
