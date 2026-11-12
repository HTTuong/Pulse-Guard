import time

import pytest
from fastapi.testclient import TestClient

from pulseguard.api.app import create_app
from pulseguard.db import make_engine
from tests.conftest import FakeClock

# Port 1 on localhost refuses connections at once, standing in for a database that is down.
UNREACHABLE_URL = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"


@pytest.mark.db
def test_health_is_ok_when_the_database_answers(
    migrated_database_url: str, fake_clock: FakeClock
) -> None:
    app = create_app(engine=make_engine(migrated_database_url, 2), clock=fake_clock)
    response = TestClient(app).get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "server_time": "2026-11-12T18:52:03.000Z",
    }


def test_health_is_503_when_the_database_is_unreachable(fake_clock: FakeClock) -> None:
    app = create_app(engine=make_engine(UNREACHABLE_URL, 1), clock=fake_clock)
    started = time.monotonic()
    response = TestClient(app).get("/api/v1/health")
    elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "unreachable"
    # The health check must answer, not hang, when the database is gone.
    assert elapsed < 5


def test_unknown_path_is_404() -> None:
    app = create_app(engine=make_engine(UNREACHABLE_URL, 1))
    assert TestClient(app).get("/api/v1/nothing-here").status_code == 404
