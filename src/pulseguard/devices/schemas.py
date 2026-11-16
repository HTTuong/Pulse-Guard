"""Request and response bodies of the device module, matching docs/openapi.yaml."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DeviceKind = Literal["PATIENT_MONITOR", "PULSE_OXIMETER", "RESPIRATION"]
DeviceStatus = Literal["AVAILABLE", "ASSIGNED", "RETIRED"]


class DeviceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serial: str = Field(pattern=r"^[A-Z0-9-]{3,40}$")
    kind: DeviceKind


class Connection(BaseModel):
    state: Literal["CONNECTED", "LOST", "UNKNOWN"]
    since: str | None = None


class DeviceOut(BaseModel):
    id: uuid.UUID
    serial: str
    kind: DeviceKind
    status: DeviceStatus
    session_id: uuid.UUID | None
    connection: Connection


class RegisteredDevice(DeviceOut):
    # Only for a patient monitor, and only in this response: it is never shown again.
    credential: str | None = None


class AssignmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: uuid.UUID


class AssignmentOut(BaseModel):
    device_id: uuid.UUID
    serial: str
    kind: DeviceKind
    session_id: uuid.UUID
    assigned_at: str


class DevicePage(BaseModel):
    items: list[DeviceOut]
    next_cursor: str | None
