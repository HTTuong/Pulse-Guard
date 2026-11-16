"""ORM mapping of the tables built by the migrations. The migrations own the schema; these
classes only describe it, so they never create or change a table."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Text
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
