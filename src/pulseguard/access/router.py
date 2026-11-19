"""HTTP routes of the access module."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from pulseguard.access import service
from pulseguard.access.actor import Actor, current_actor
from pulseguard.access.schemas import LoginInput, MeOut, TokenOut, UserChange, UserInput, UserOut
from pulseguard.sessions.router import ClockDep, DbDep

router = APIRouter()
ActorDep = Annotated[Actor, Depends(current_actor)]


@router.post("/auth/login", response_model=TokenOut)
def login(request: Request, db: DbDep, clock: ClockDep, body: LoginInput) -> TokenOut:
    settings = request.app.state.settings
    return service.login(
        db, settings.jwt_secret, clock, settings.token_hours, body.email, body.password
    )


@router.get("/auth/me", response_model=MeOut)
def me(actor: ActorDep) -> MeOut:
    return service.me(actor)


@router.post("/users", status_code=201, response_model=UserOut)
def create_user(actor: ActorDep, db: DbDep, body: UserInput) -> UserOut:
    return service.create_user(db, actor, body)


@router.patch("/users/{user_id}", response_model=UserOut)
def change_user(actor: ActorDep, db: DbDep, user_id: uuid.UUID, body: UserChange) -> UserOut:
    return service.change_user(db, actor, user_id, body)
