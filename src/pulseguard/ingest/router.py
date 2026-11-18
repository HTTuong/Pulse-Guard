"""HTTP routes for patient monitors. They never accept a user token (docs/api.md)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from pulseguard.errors import ApiError
from pulseguard.ingest import service
from pulseguard.ingest.auth import current_monitor
from pulseguard.ingest.schemas import (
    MAX_BODY_BYTES,
    MAX_READINGS,
    BatchIn,
    BatchResult,
    PairingsOut,
)
from pulseguard.models import Device
from pulseguard.sessions.router import ClockDep, DbDep

router = APIRouter(prefix="/ingest")
MonitorDep = Annotated[Device, Depends(current_monitor)]


def limit_body(request: Request) -> None:
    """413 before the body is parsed, so a huge batch costs no work (docs/api.md, Errors)."""
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise ApiError(413, "BATCH_TOO_LARGE", "Batch over 1 MB")


@router.post("/batches", response_model=BatchResult, dependencies=[Depends(limit_body)])
def post_batch(monitor: MonitorDep, db: DbDep, clock: ClockDep, body: BatchIn) -> BatchResult:
    if len(body.readings) > MAX_READINGS:
        raise ApiError(413, "BATCH_TOO_LARGE", f"Batch over {MAX_READINGS} readings")
    return service.ingest(db, monitor, clock, body)


@router.get("/pairings", response_model=PairingsOut)
def get_pairings(monitor: MonitorDep, db: DbDep, clock: ClockDep) -> PairingsOut:
    return service.pairings(db, monitor, clock)
