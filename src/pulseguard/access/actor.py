"""Who is making a request, and what they may do (REQ-ACC-02)."""

import uuid
from dataclasses import dataclass
from enum import StrEnum

from pulseguard.errors import ApiError, forbidden


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


def current_actor() -> Actor:
    """Resolves the caller from the token. Until token login exists (d244) every request is
    refused, so no endpoint is ever open without authentication. Tests override this."""
    raise ApiError(401, "UNAUTHENTICATED", "Authentication required")
