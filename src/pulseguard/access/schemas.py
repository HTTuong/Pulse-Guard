"""Bodies of login and user management, matching docs/openapi.yaml."""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from pulseguard.access.actor import Role

MIN_PASSWORD_LENGTH = 12


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class TokenOut(BaseModel):
    access_token: str
    expires_at: str
    role: Role


class MeOut(BaseModel):
    user_id: uuid.UUID
    role: Role
    unit_ids: list[uuid.UUID]


class UserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    display_name: str = Field(min_length=1, max_length=100)
    role: Role
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=1024)
    unit_codes: list[str] = []


class UserChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool | None = None
    role: Role | None = None
    unit_codes: list[str] | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    active: bool
    unit_codes: list[str]
