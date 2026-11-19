"""Application factory. Tests build the app with their own engine and clock."""

from fastapi import FastAPI
from sqlalchemy import Engine

from pulseguard import errors
from pulseguard.alarms.router import router as alarms_router
from pulseguard.api import health
from pulseguard.clock import Clock, SystemClock
from pulseguard.config import Settings
from pulseguard.db import make_engine, make_sessionmaker
from pulseguard.devices.router import router as devices_router
from pulseguard.ingest.router import router as ingest_router
from pulseguard.sessions.router import router as sessions_router

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
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    app.state.clock = clock or SystemClock()
    errors.install(app)
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(sessions_router, prefix=API_PREFIX)
    app.include_router(devices_router, prefix=API_PREFIX)
    app.include_router(ingest_router, prefix=API_PREFIX)
    app.include_router(alarms_router, prefix=API_PREFIX)
    return app
