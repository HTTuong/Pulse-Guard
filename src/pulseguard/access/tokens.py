"""Staff tokens: JWT signed with HS256 (REQ-ACC-01).

The token names the user and their token_version only. Role and units are read from the database
on every request, and raising token_version (deactivation, role change) stops every older token
at once. Times are checked against the injected clock, not the system clock."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt

ALGORITHM = "HS256"
ISSUER = "pulseguard"
AUDIENCE = "pulseguard-staff"
MIN_SECRET_LENGTH = 32


@dataclass(frozen=True)
class Claims:
    user_id: uuid.UUID
    token_version: int


def issue(
    secret: str, user_id: uuid.UUID, token_version: int, now: datetime, lifetime: timedelta
) -> tuple[str, datetime]:
    expires = now + lifetime
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM), expires


def verify(secret: str, token: str, now: datetime, max_lifetime: timedelta) -> Claims | None:
    """None for anything that is not a valid, current staff token."""
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={
                "require": ["sub", "ver", "iat", "exp", "iss", "aud"],
                "verify_exp": False,
                "verify_iat": False,
            },
        )
        issued, expires = int(payload["iat"]), int(payload["exp"])
        user_id, version = uuid.UUID(str(payload["sub"])), payload["ver"]
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        return None
    moment = now.timestamp()
    # Checked here with the injected clock: expired, issued in the future, or claiming a lifetime
    # longer than the backend ever issues (a token signed with a leaked secret, say).
    if not issued <= moment < expires or expires - issued > max_lifetime.total_seconds():
        return None
    if not isinstance(version, int) or isinstance(version, bool):
        return None
    return Claims(user_id, version)
