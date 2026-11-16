"""Error responses follow docs/api.md and never leak internals."""

from fastapi.testclient import TestClient

from pulseguard.access.actor import current_actor
from pulseguard.api.app import create_app
from pulseguard.db import make_engine
from tests.ward import NURSE_W1

UNREACHABLE_URL = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"
SESSION = "/api/v1/sessions/00000000-0000-0000-0000-000000000001"


def test_unknown_path_is_a_problem_document() -> None:
    response = TestClient(create_app(engine=make_engine(UNREACHABLE_URL, 1))).get("/api/v1/nope")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "NOT_FOUND"


def test_request_without_a_user_is_401_before_any_database_work() -> None:
    app = create_app(engine=make_engine(UNREACHABLE_URL, 1))
    response = TestClient(app, raise_server_exceptions=False).get(SESSION)
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_unexpected_error_is_500_without_internals() -> None:
    """The database goes away in the middle of a request."""
    app = create_app(engine=make_engine(UNREACHABLE_URL, 1))
    app.dependency_overrides[current_actor] = lambda: NURSE_W1
    response = TestClient(app, raise_server_exceptions=False).get(SESSION)
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "/problems/internal",
        "title": "Internal error",
        "status": 500,
        "code": "INTERNAL",
    }
    for leak in ("psycopg", "Traceback", "127.0.0.1", "SELECT"):
        assert leak not in response.text
