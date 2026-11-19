"""Shared fixtures.

Database tests need PULSEGUARD_TEST_ADMIN_URL, a URL with rights to create databases.
Each test that asks for `fresh_database_url` gets its own empty database, dropped afterwards.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent

# Every app built in the tests signs tokens with this fixed, fake secret.
TEST_JWT_SECRET = "test-only-secret-not-for-production-0123456789"
os.environ.setdefault("PULSEGUARD_JWT_SECRET", TEST_JWT_SECRET)


class FakeClock:
    """A clock a test sets and moves by hand."""

    def __init__(self, start: datetime) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 12, 18, 52, 3, tzinfo=UTC))


def admin_url() -> str:
    url = os.environ.get("PULSEGUARD_TEST_ADMIN_URL")
    if not url:
        pytest.skip("PULSEGUARD_TEST_ADMIN_URL is not set")
    return url


@pytest.fixture
def fresh_database_url() -> Iterator[str]:
    base = make_url(admin_url())
    name = f"pulseguard_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield base.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def alembic_config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


@pytest.fixture
def migrated_database_url(fresh_database_url: str) -> str:
    command.upgrade(alembic_config(fresh_database_url), "head")
    return fresh_database_url


# Shared fixtures from other files, registered here so every test package sees them.
from tests.ward import ward  # noqa: E402, F401
