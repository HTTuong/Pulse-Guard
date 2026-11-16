"""Request and response bodies, matching docs/openapi.yaml."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PatientInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Synthetic record numbers only, so real ones can never be stored (REQ-NFR-04).
    mrn: str = Field(pattern=r"^TEST-[0-9]{4,10}$")
    given_name: str = Field(min_length=1, max_length=100)
    family_name: str = Field(min_length=1, max_length=100)
    birth_year: int | None = Field(default=None, ge=1900, le=2100)


class PatientOut(BaseModel):
    id: uuid.UUID
    mrn: str
    given_name: str
    family_name: str
    birth_year: int | None


class AdmissionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: uuid.UUID
    bed_id: uuid.UUID


class SessionOut(BaseModel):
    id: uuid.UUID
    patient: PatientOut
    status: Literal["OPEN", "CLOSED"]
    unit_id: uuid.UUID
    bed_id: uuid.UUID | None
    admitted_at: str
    discharged_at: str | None
    # Devices assigned now; empty once the session is closed.
    devices: list[dict[str, str]] = []
