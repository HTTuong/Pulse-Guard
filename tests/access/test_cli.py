"""The set up commands, run against a migrated database, then used to log in."""

import pytest
from fastapi.testclient import TestClient

from pulseguard.access import cli
from pulseguard.api.app import create_app

pytestmark = pytest.mark.db


def test_create_unit_and_user_then_log_in(
    migrated_database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PULSEGUARD_DATABASE_URL", migrated_database_url)
    monkeypatch.setenv("PULSEGUARD_NEW_PASSWORD", "a long enough fake password")
    assert cli.main(["create-unit", "W1", "Ward 1", "--beds", "3"]) == 0
    assert cli.main(["create-user", "Admin@Example.com", "Admin", "ADMIN", "--units", "W1"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "created unit W1 with 3 beds",
        "created user admin@example.com (ADMIN)",
    ]
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "a long enough fake password"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "ADMIN"


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["create-user", "a@example.com", "A", "NURSE", "--units", "W9"], "unknown units: W9"),
    ],
)
def test_create_user_refuses_unknown_units(
    migrated_database_url: str, monkeypatch: pytest.MonkeyPatch, argv: list[str], message: str
) -> None:
    monkeypatch.setenv("PULSEGUARD_DATABASE_URL", migrated_database_url)
    monkeypatch.setenv("PULSEGUARD_NEW_PASSWORD", "a long enough fake password")
    with pytest.raises(SystemExit, match=message):
        cli.main(argv)


def test_create_user_refuses_a_short_password(
    migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PULSEGUARD_DATABASE_URL", migrated_database_url)
    monkeypatch.setenv("PULSEGUARD_NEW_PASSWORD", "too short")
    with pytest.raises(SystemExit, match="at least 12"):
        cli.main(["create-user", "a@example.com", "A", "NURSE"])
