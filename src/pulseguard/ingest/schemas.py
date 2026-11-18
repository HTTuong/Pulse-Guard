"""Ingest bodies, matching docs/openapi.yaml (IngestBatch, IngestResult)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Parameter = Literal["PR", "SPO2", "RR"]

MAX_READINGS = 1000
MAX_BODY_BYTES = 1_000_000


class ReadingIn(BaseModel):
    # Fields that cannot be read at all make the whole batch 400. Fields that can be read but
    # break a rule (unit, time, range) refuse only that reading, so one bad sensor never loses
    # the readings of the others (docs/api.md, Ingest).
    model_config = ConfigDict(extra="ignore")
    device: str = Field(min_length=1, max_length=40)
    seq: int = Field(ge=1)
    parameter: Parameter
    value: float
    unit: str
    measured_at: str


class BatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monitor: str
    sent_at: str
    readings: list[ReadingIn]
    # Status events (SENSOR_LOST and others) are handled with technical alarms; until then a
    # batch that carries any is refused as a whole rather than dropping them silently.
    events: list[dict[str, object]] = []


class ItemRef(BaseModel):
    device: str
    seq: int


class Refused(ItemRef):
    code: str
    detail: str


class BatchResult(BaseModel):
    stored: list[ItemRef]
    duplicates: list[ItemRef]
    refused: list[Refused]
    events_stored: list[int]


class PairedSensor(BaseModel):
    serial: str


class UnpairedSensor(BaseModel):
    serial: str
    unpaired_at: str


class PairingsOut(BaseModel):
    monitor: str
    paired: list[PairedSensor]
    unpaired: list[UnpairedSensor]
