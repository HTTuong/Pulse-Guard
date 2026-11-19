"""The first migration builds the designed schema, and can be undone and done again."""

import re

import psycopg
import pytest
from alembic import command
from sqlalchemy.engine import make_url

from tests.conftest import ROOT, alembic_config

pytestmark = pytest.mark.db

EXPECTED_TABLES = {
    "unit", "bed", "app_user", "user_unit", "patient", "monitoring_session", "bed_stay",
    "device", "device_assignment", "device_connection", "measurement", "reading_rejection",
    "absolute_limit", "session_limit", "session_limit_change", "alarm", "alarm_event",
    "alarm_engine_state",
}  # fmt: skip


def connect(database_url: str) -> psycopg.Connection[tuple[object, ...]]:
    url = make_url(database_url).set(drivername="postgresql")
    return psycopg.connect(url.render_as_string(hide_password=False), autocommit=True)


def tables(database_url: str) -> set[str]:
    with connect(database_url) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def our_types(database_url: str) -> set[str]:
    with connect(database_url) as conn:
        rows = conn.execute(
            "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = 'public' AND t.typtype = 'e'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def test_upgrade_creates_every_table(migrated_database_url: str) -> None:
    assert tables(migrated_database_url) == EXPECTED_TABLES | {"alembic_version"}


def test_migrated_schema_passes_the_constraint_checks(migrated_database_url: str) -> None:
    checks = (ROOT / "docs" / "db" / "schema-checks.sql").read_text()
    notices: list[str] = []
    with connect(migrated_database_url) as conn:
        conn.add_notice_handler(lambda diag: notices.append(diag.message_primary or ""))
        conn.execute(checks.encode())
    results = [n for n in notices if re.match(r"(PASS|FAIL) ", n)]
    failures = [n for n in results if n.startswith("FAIL")]
    assert failures == []
    assert len(results) == 31


def test_downgrade_removes_everything_and_upgrade_works_again(fresh_database_url: str) -> None:
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    assert tables(fresh_database_url) == {"alembic_version"}
    assert our_types(fresh_database_url) == set()
    command.upgrade(config, "head")
    assert tables(fresh_database_url) == EXPECTED_TABLES | {"alembic_version"}
