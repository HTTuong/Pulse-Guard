"""One batch from one patient monitor. Every reading gets exactly one outcome: stored, duplicate
or refused with a code. The batch is one transaction."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pulseguard.alarms import service as alarms
from pulseguard.clock import Clock, to_wire
from pulseguard.devices.assignments import pairings_for_monitor, session_of_device_at
from pulseguard.errors import ApiError, forbidden
from pulseguard.ingest.schemas import (
    BatchIn,
    BatchResult,
    ItemRef,
    PairedSensor,
    PairingsOut,
    ReadingIn,
    Refused,
    UnpairedSensor,
)
from pulseguard.measurements import rules
from pulseguard.models import Device, Measurement, ReadingRejection

# How far back a monitor is told about unpaired sensors: as far back as a reading may be measured.
UNPAIRED_LOOKBACK = timedelta(hours=24)


@dataclass(frozen=True)
class Candidate:
    position: int
    reading: ReadingIn
    device_id: uuid.UUID
    session_id: uuid.UUID
    value: Decimal
    measured_at: datetime

    def content(self) -> tuple[str, Decimal, datetime]:
        return (self.reading.parameter, self.value, self.measured_at)


def judge(
    db: Session,
    monitor: Device,
    reading: ReadingIn,
    now: datetime,
    devices: dict[str, Device | None],
) -> rules.Refusal | tuple[uuid.UUID, uuid.UUID, Decimal, datetime]:
    """All checks of one reading, cheapest first. Returns a refusal or what to store."""
    if reading.device not in devices:
        devices[reading.device] = db.scalars(
            select(Device).where(Device.serial == reading.device)
        ).first()
    device = devices[reading.device]
    if device is None:
        return rules.Refusal("UNKNOWN_DEVICE", "No device with this serial")
    if device.status == "RETIRED":
        return rules.Refusal("DEVICE_RETIRED", "Device is retired")
    measured_at = rules.parse_time(reading.measured_at)
    value = rules.stored_value(reading.value)
    refusal = (
        rules.check_kind(device.kind, reading.parameter)
        or rules.check_unit(reading.parameter, reading.unit)
        or rules.check_time(measured_at, now)
        or rules.check_value(reading.parameter, value)
    )
    if refusal:
        return refusal
    if measured_at is None or value is None:  # unreachable: the checks above refuse both
        raise RuntimeError("reading passed the checks without a time or value")
    # REQ-MEA-06: the session is the one the sensor had when the value was measured, and the
    # monitor sending it must have been in that same session at that moment (REQ-DEV-05).
    session_id = session_of_device_at(db, device.id, measured_at)
    if session_id is None:
        return rules.Refusal("NO_SESSION", "Sensor was not assigned when this was measured")
    if session_of_device_at(db, monitor.id, measured_at) != session_id:
        return rules.Refusal("NOT_PAIRED", "Sensor was not paired with this monitor then")
    return (device.id, session_id, value, measured_at)


def ingest(db: Session, monitor: Device, clock: Clock, batch: BatchIn) -> BatchResult:
    if batch.monitor != monitor.serial:
        raise forbidden("The batch names another monitor than the credential")
    if batch.events:
        raise ApiError(
            400,
            "VALIDATION_FAILED",
            "Status events are not accepted yet",
            errors=[{"field": "events", "message": "must be empty for now"}],
        )
    now = clock.now()
    devices: dict[str, Device | None] = {}
    outcome: dict[int, ItemRef | Refused] = {}
    kinds: dict[int, str] = {}
    candidates: list[Candidate] = []
    seen: dict[tuple[uuid.UUID, int], Candidate] = {}

    for position, reading in enumerate(batch.readings):
        ref = ItemRef(device=reading.device, seq=reading.seq)
        verdict = judge(db, monitor, reading, now, devices)
        if isinstance(verdict, rules.Refusal):
            outcome[position] = Refused(
                **ref.model_dump(), code=verdict.code, detail=verdict.detail
            )
            kinds[position] = "refused"
            continue
        device_id, session_id, value, measured_at = verdict
        candidate = Candidate(position, reading, device_id, session_id, value, measured_at)
        earlier = seen.get((device_id, reading.seq))
        if earlier is not None:  # the same seq twice in one batch
            if earlier.content() == candidate.content():
                outcome[position], kinds[position] = ref, "duplicate"
            else:
                outcome[position] = Refused(
                    **ref.model_dump(), code="SEQ_CONFLICT", detail="Same seq with other content"
                )
                kinds[position] = "refused"
            continue
        seen[(device_id, reading.seq)] = candidate
        candidates.append(candidate)

    inserted: set[tuple[uuid.UUID, int]] = set()
    if candidates:
        statement = (
            insert(Measurement)
            .values(
                [
                    {
                        "session_id": c.session_id,
                        "device_id": c.device_id,
                        "seq": c.reading.seq,
                        "parameter": c.reading.parameter,
                        "value": c.value,
                        "measured_at": c.measured_at,
                        "received_at": now,
                    }
                    for c in candidates
                ]
            )
            .on_conflict_do_nothing(index_elements=["device_id", "seq"])
            .returning(Measurement.device_id, Measurement.seq)
        )
        inserted = {(row[0], row[1]) for row in db.execute(statement)}
    # Readings already stored, by an earlier batch or by a parallel resend of this one.
    existing_keys = [
        (c.device_id, c.reading.seq)
        for c in candidates
        if (c.device_id, c.reading.seq) not in inserted
    ]
    existing = {}
    if existing_keys:
        rows = db.scalars(
            select(Measurement).where(
                tuple_(Measurement.device_id, Measurement.seq).in_(existing_keys)
            )
        )
        existing = {(m.device_id, m.seq): (m.parameter, m.value, m.measured_at) for m in rows}
    for c in candidates:
        ref = ItemRef(device=c.reading.device, seq=c.reading.seq)
        key = (c.device_id, c.reading.seq)
        if key in inserted:
            outcome[c.position], kinds[c.position] = ref, "stored"
        elif existing.get(key) == c.content():
            outcome[c.position], kinds[c.position] = ref, "duplicate"
        else:
            # R-ORD-04: a sensor that restarted its numbering. Refused and counted, never dropped
            # in silence as a duplicate.
            outcome[c.position] = Refused(
                **ref.model_dump(), code="SEQ_CONFLICT", detail="Same seq with other content"
            )
            kinds[c.position] = "refused"

    # Only readings stored now go through the alarm engine: a duplicate was seen the first time.
    by_parameter: dict[tuple[uuid.UUID, str], list[tuple[datetime, Decimal]]] = {}
    for c in candidates:
        if (c.device_id, c.reading.seq) in inserted:
            group = (c.session_id, str(c.reading.parameter))
            by_parameter.setdefault(group, []).append((c.measured_at, c.value))
    for (session_id, parameter), readings in sorted(by_parameter.items()):
        alarms.process_readings(db, session_id, parameter, readings)

    for position in sorted(outcome):
        item = outcome[position]
        if isinstance(item, Refused):
            db.add(
                ReadingRejection(
                    device_serial=item.device,
                    seq=item.seq,
                    code=item.code,
                    detail=item.detail,
                    received_at=now,
                )
            )
    db.commit()

    ordered = [(kinds[p], outcome[p]) for p in sorted(outcome)]
    return BatchResult(
        stored=[i for k, i in ordered if k == "stored"],
        duplicates=[i for k, i in ordered if k == "duplicate"],
        refused=[i for k, i in ordered if isinstance(i, Refused)],
        events_stored=[],
    )


def pairings(db: Session, monitor: Device, clock: Clock) -> PairingsOut:
    result = pairings_for_monitor(db, monitor.id, since=clock.now() - UNPAIRED_LOOKBACK)
    return PairingsOut(
        monitor=result.monitor_serial,
        paired=[PairedSensor(serial=s) for s in result.paired],
        unpaired=[UnpairedSensor(serial=s, unpaired_at=to_wire(t)) for s, t in result.unpaired],
    )
