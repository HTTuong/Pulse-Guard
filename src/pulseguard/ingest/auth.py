"""Patient monitors authenticate with the credential issued at registration (REQ-ACC-03)."""

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from pulseguard.db import get_db
from pulseguard.devices.service import hash_credential
from pulseguard.errors import ApiError
from pulseguard.models import Device


def current_monitor(
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> Device:
    """The calling monitor, or 401. A retired monitor's credential stops working at once."""
    refused = ApiError(401, "UNAUTHENTICATED", "Device credential required")
    scheme, _, credential = (authorization or "").partition(" ")
    if scheme != "Bearer" or not credential or " " in credential:
        raise refused
    device = db.scalars(
        select(Device).where(
            Device.credential_hash == hash_credential(credential),
            Device.kind == "PATIENT_MONITOR",
            Device.status != "RETIRED",
        )
    ).first()
    if device is None:
        raise refused
    return device
