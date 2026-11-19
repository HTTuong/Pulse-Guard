"""Login and user management (REQ-ACC-01, REQ-ACC-02)."""

import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pulseguard.access import tokens
from pulseguard.access.actor import Actor, Role
from pulseguard.access.passwords import DUMMY_HASH, hash_password, verify_password
from pulseguard.access.schemas import MeOut, TokenOut, UserChange, UserInput, UserOut
from pulseguard.clock import Clock, to_wire
from pulseguard.errors import ApiError, not_found
from pulseguard.models import AppUser, Unit, UserUnit
from pulseguard.sessions.service import conflict_from


def login(
    db: Session, secret: str, clock: Clock, hours: int, email: str, password: str
) -> TokenOut:
    """The same 401 for an unknown email, a wrong password and a deactivated user, and the same
    work for each, so neither the answer nor its timing tells which emails exist."""
    user = db.scalars(select(AppUser).where(AppUser.email == email.strip().lower())).first()
    password_ok = verify_password(password, user.password_hash if user else DUMMY_HASH)
    if user is None or not password_ok or not user.active:
        raise ApiError(401, "UNAUTHENTICATED", "Wrong email or password")
    token, expires = tokens.issue(
        secret, user.id, user.token_version, clock.now(), timedelta(hours=hours)
    )
    return TokenOut(access_token=token, expires_at=to_wire(expires), role=Role(user.role))


def me(actor: Actor) -> MeOut:
    return MeOut(user_id=actor.user_id, role=actor.role, unit_ids=sorted(actor.unit_ids))


def unit_ids(db: Session, codes: list[str]) -> list[uuid.UUID]:
    found = {u.code: u.id for u in db.scalars(select(Unit).where(Unit.code.in_(codes)))}
    missing = sorted(set(codes) - set(found))
    if missing:
        raise ApiError(
            400,
            "VALIDATION_FAILED",
            "Unknown unit",
            errors=[{"field": "unit_codes", "message": f"unknown: {', '.join(missing)}"}],
        )
    return [found[code] for code in codes]


def user_out(db: Session, user: AppUser) -> UserOut:
    codes = db.scalars(
        select(Unit.code)
        .join(UserUnit, UserUnit.unit_id == Unit.id)
        .where(UserUnit.user_id == user.id)
        .order_by(Unit.code)
    )
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=Role(user.role),
        active=user.active,
        unit_codes=list(codes),
    )


def create_user(db: Session, actor: Actor, data: UserInput) -> UserOut:
    actor.require_role(Role.ADMIN)
    user = AppUser(
        email=data.email.strip().lower(),
        display_name=data.display_name,
        role=data.role.value,
        password_hash=hash_password(data.password),
        active=True,
        token_version=0,
    )
    db.add(user)
    try:
        db.flush()
        for unit_id in unit_ids(db, data.unit_codes):
            db.add(UserUnit(user_id=user.id, unit_id=unit_id))
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise conflict_from(error) from None
    return user_out(db, user)


def change_user(db: Session, actor: Actor, user_id: uuid.UUID, data: UserChange) -> UserOut:
    """Deactivating a user or changing their role raises token_version, so their tokens stop
    working at the next request. A change of units needs no new token: units are read from the
    database on every request."""
    user = db.get(AppUser, user_id, with_for_update=True)
    if user is None:
        raise not_found("user")
    actor.require_role(Role.ADMIN)
    if user.id == actor.user_id and (data.active is False or data.role not in (None, Role.ADMIN)):
        raise ApiError(409, "SELF_LOCKOUT", "An admin may not deactivate or demote themselves")
    if data.active is not None and data.active != user.active:
        user.active = data.active
        user.token_version += 1
    if data.role is not None and data.role.value != user.role:
        user.role = data.role.value
        user.token_version += 1
    if data.unit_codes is not None:
        ids = unit_ids(db, data.unit_codes)
        db.execute(delete(UserUnit).where(UserUnit.user_id == user.id))
        for unit_id in ids:
            db.add(UserUnit(user_id=user.id, unit_id=unit_id))
    db.commit()
    return user_out(db, user)
