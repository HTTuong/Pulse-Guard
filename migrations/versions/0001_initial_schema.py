"""Initial schema: units, users, sessions, devices, measurements, limits and alarms.

The SQL is in 0001_initial_schema.sql, copied from docs/db/schema-draft.sql as checked in d238.
From here on, migrations are the source of truth.

Revision: 0001
Previous: none
Created: 2026-11-12
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE_SQL = Path(__file__).with_suffix(".sql")

TABLES = [
    "alarm_event",
    "alarm",
    "session_limit_change",
    "session_limit",
    "absolute_limit",
    "reading_rejection",
    "measurement",
    "device_connection",
    "device_assignment",
    "device",
    "bed_stay",
    "monitoring_session",
    "patient",
    "user_unit",
    "app_user",
    "bed",
    "unit",
]

TYPES = [
    "connection_state",
    "alarm_state",
    "alarm_priority",
    "alarm_kind",
    "parameter",
    "device_status",
    "device_kind",
    "session_status",
    "user_role",
]


def upgrade() -> None:
    op.execute(UPGRADE_SQL.read_text())


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE {table}")
    for type_name in TYPES:
        op.execute(f"DROP TYPE {type_name}")
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
