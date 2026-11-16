"""A ward with fixed fake data, and a client that acts as a chosen user."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pulseguard.access.actor import Actor, Role, current_actor
from pulseguard.api.app import create_app
from pulseguard.db import make_engine
from tests.conftest import FakeClock

W1 = uuid.UUID("10000000-0000-0000-0000-000000000001")
W2 = uuid.UUID("10000000-0000-0000-0000-000000000002")
NURSE_W1 = Actor(uuid.UUID("30000000-0000-0000-0000-000000000001"), Role.NURSE, frozenset({W1}))
NURSE_W2 = Actor(uuid.UUID("30000000-0000-0000-0000-000000000002"), Role.NURSE, frozenset({W2}))
PHYSICIAN_W1 = Actor(
    uuid.UUID("30000000-0000-0000-0000-000000000003"), Role.PHYSICIAN, frozenset({W1})
)
TECHNICIAN = Actor(
    uuid.UUID("30000000-0000-0000-0000-000000000004"), Role.TECHNICIAN, frozenset({W1, W2})
)


def bed(unit: int, number: int) -> uuid.UUID:
    return uuid.UUID(f"2000000{unit}-0000-0000-0000-{number:012d}")


SEED = """
INSERT INTO unit (id, code, name) VALUES
  ('10000000-0000-0000-0000-000000000001', 'W1', 'Ward 1'),
  ('10000000-0000-0000-0000-000000000002', 'W2', 'Ward 2');
INSERT INTO bed (id, unit_id, label)
  SELECT ('2000000' || u || '-0000-0000-0000-' || lpad(n::text, 12, '0'))::uuid,
         ('10000000-0000-0000-0000-00000000000' || u)::uuid, 'B' || n
  FROM generate_series(1, 2) u, generate_series(1, 12) n;
INSERT INTO app_user (id, email, display_name, role, password_hash) VALUES
  ('30000000-0000-0000-0000-000000000001', 'nurse1@example.com', 'Nurse One', 'NURSE', 'x'),
  ('30000000-0000-0000-0000-000000000002', 'nurse2@example.com', 'Nurse Two', 'NURSE', 'x'),
  ('30000000-0000-0000-0000-000000000003', 'doc1@example.com', 'Doctor One', 'PHYSICIAN', 'x'),
  ('30000000-0000-0000-0000-000000000004', 'tech1@example.com', 'Tech One', 'TECHNICIAN', 'x');
"""


@dataclass
class Ward:
    app: FastAPI
    engine: Engine
    clock: FakeClock

    def client(self, actor: Actor | None) -> TestClient:
        """A client acting as `actor`; None means no authenticated user."""
        if actor is None:
            self.app.dependency_overrides.pop(current_actor, None)
        else:
            self.app.dependency_overrides[current_actor] = lambda: actor
        return TestClient(self.app, raise_server_exceptions=False)

    def scalar(self, sql: str, **params: object) -> object:
        """One value from the database, with bound parameters (never formatted into the SQL)."""
        with self.engine.connect() as connection:
            return connection.execute(text(sql), params).scalar()

    def new_patient(self, number: int) -> str:
        response = self.client(NURSE_W1).post(
            "/api/v1/patients",
            json={"mrn": f"TEST-{number:04d}", "given_name": "Aino", "family_name": "Example"},
        )
        assert response.status_code == 201, response.text
        return str(response.json()["id"])


@pytest.fixture
def ward(migrated_database_url: str, fake_clock: FakeClock) -> Iterator[Ward]:
    engine = make_engine(migrated_database_url, 2)
    with engine.begin() as connection:
        connection.execute(text(SEED))
    yield Ward(create_app(engine=engine, clock=fake_clock), engine, fake_clock)
    engine.dispose()
