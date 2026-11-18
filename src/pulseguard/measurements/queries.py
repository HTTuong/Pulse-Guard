"""Reading stored measurements (REQ-MEA-04): order is by measured time, never by arrival."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulseguard.models import Measurement


def current_values(db: Session, session_id: uuid.UUID) -> dict[str, Measurement]:
    """The latest measured reading of each parameter. Ties on measured time go to the higher id,
    so the answer is the same on every call."""
    rows = db.scalars(
        select(Measurement)
        .where(Measurement.session_id == session_id)
        .order_by(Measurement.parameter, Measurement.measured_at.desc(), Measurement.id.desc())
        .distinct(Measurement.parameter)
    )
    return {row.parameter: row for row in rows}


def history(
    db: Session, session_id: uuid.UUID, parameter: str, start: datetime, end: datetime
) -> list[Measurement]:
    """Readings of one parameter in [start, end), oldest first."""
    return list(
        db.scalars(
            select(Measurement)
            .where(
                Measurement.session_id == session_id,
                Measurement.parameter == parameter,
                Measurement.measured_at >= start,
                Measurement.measured_at < end,
            )
            .order_by(Measurement.measured_at, Measurement.id)
        )
    )
