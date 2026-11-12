"""Application factory. Tests build the app with their own engine and clock."""

from fastapi import FastAPI
from sqlalchemy import Engine

from pulseguard.api import health
from pulseguard.clock import Clock, SystemClock
from pulseguard.config import Settings
from pulseguard.db import make_engine

API_PREFIX = "/api/v1"


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(
        title="PulseGuard API",
        version="0.1.0",
        summary="Simulated ward monitoring system for practising testing. Not a medical device.",
    )
    app.state.engine = engine or make_engine(settings.database_url, settings.db_check_timeout)
    app.state.clock = clock or SystemClock()
    app.include_router(health.router, prefix=API_PREFIX)
    return app
