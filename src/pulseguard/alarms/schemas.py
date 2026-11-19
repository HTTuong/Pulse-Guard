"""Alarm bodies, matching docs/openapi.yaml (Alarm, AlarmEvent, Limits)."""

import uuid

from pydantic import BaseModel


class AlarmEventOut(BaseModel):
    at: str
    from_state: str | None
    to_state: str
    priority: str
    by_user: uuid.UUID | None
    reason: str


class AlarmOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    kind: str
    code: str
    parameter: str | None
    device_id: uuid.UUID | None
    priority: str
    state: str
    condition_cleared: bool
    escalated: bool
    started_at: str
    acknowledged_at: str | None
    acknowledged_by: uuid.UUID | None
    resolved_at: str | None
    resolution: str | None


class AlarmWithHistory(AlarmOut):
    history: list[AlarmEventOut]


class LimitsOut(BaseModel):
    parameter: str
    high_low: float | None
    medium_low: float | None
    medium_high: float | None
    high_high: float | None
    changed_at: str
    changed_by: uuid.UUID | None
