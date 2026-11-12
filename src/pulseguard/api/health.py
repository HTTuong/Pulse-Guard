"""GET /health: is the process up, and can it reach the database."""

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from pulseguard.clock import Clock, to_wire

router = APIRouter()


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unreachable"]
    server_time: str


@router.get("/health", response_model=Health)
def health(request: Request, response: Response) -> Health:
    engine: Engine = request.app.state.engine
    clock: Clock = request.app.state.clock
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        database: Literal["ok", "unreachable"] = "ok"
    except SQLAlchemyError:
        database = "unreachable"
    if database != "ok":
        # 503 so that a container healthcheck or load balancer sees the backend is not usable.
        response.status_code = 503
    return Health(
        status="ok" if database == "ok" else "degraded",
        database=database,
        server_time=to_wire(clock.now()),
    )
