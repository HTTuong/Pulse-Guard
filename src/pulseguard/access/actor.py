"""Who is making a request, and what they may do (REQ-ACC-01, REQ-ACC-02)."""

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from pulseguard.access import tokens
from pulseguard.access.credentials import hash_credential
from pulseguard.db import get_db
from pulseguard.errors import ApiError, forbidden
from pulseguard.models import AppUser, Device, UserUnit


class Role(StrEnum):
    NURSE = "NURSE"
    PHYSICIAN = "PHYSICIAN"
    TECHNICIAN = "TECHNICIAN"
    ADMIN = "ADMIN"


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    role: Role
    unit_ids: frozenset[uuid.UUID]

    def require_role(self, *roles: Role) -> None:
        if self.role not in roles:
            raise forbidden(f"Role {self.role} may not do this")

    def require_unit(self, unit_id: uuid.UUID) -> None:
        if unit_id not in self.unit_ids:
            raise forbidden("This belongs to a unit you do not work on")


# Exactly "Bearer", one space, a token without spaces. Project 2 accepted a second word after the
# token (F-15 there); here anything else is refused.
BEARER = re.compile(r"^Bearer ([^\s]+)$")


def bearer_token(authorization: str | None) -> str | None:
    match = BEARER.match(authorization or "")
    return match.group(1) if match else None


def unauthenticated() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "Authentication required")


def is_device_credential(db: Session, token: str) -> bool:
    return (
        db.scalars(
            select(Device.id).where(Device.credential_hash == hash_credential(token))
        ).first()
        is not None
    )


def current_actor(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> Actor:
    """The calling staff member, from their token. Role and units come from the database, so a
    change applies on the next request; a deactivated user or a role change raises token_version
    and stops every older token."""
    token = bearer_token(authorization)
    if token is None:
        raise unauthenticated()
    lifetime = timedelta(hours=request.app.state.settings.token_hours)
    claims = tokens.verify(
        request.app.state.settings.jwt_secret, token, request.app.state.clock.now(), lifetime
    )
    if claims is None:
        if is_device_credential(db, token):
            raise forbidden("A device credential may only call /ingest")
        raise unauthenticated()
    user = db.get(AppUser, claims.user_id)
    if user is None or not user.active or user.token_version != claims.token_version:
        raise unauthenticated()
    units = db.scalars(select(UserUnit.unit_id).where(UserUnit.user_id == user.id))
    return Actor(user.id, Role(user.role), frozenset(units))
