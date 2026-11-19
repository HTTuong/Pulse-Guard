"""Store the alarm engine's state per session and alarm code.

d243 replaces the plan in database.md (rebuild the state by replaying the last 60 s): the state is
four timestamps per side, written in the same transaction as the readings that changed it, so a
restart in the middle of a debounce loses nothing (R-REC-04) and no replay has to deal with gaps.

Revision: 0004
Previous: 0003
Created: 2026-11-19
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE alarm_engine_state (
          session_id      uuid NOT NULL REFERENCES monitoring_session(id),
          code            text NOT NULL,
          last_at         timestamptz,
          abnormal_since  timestamptz,
          high_since      timestamptz,
          normal_since    timestamptz,
          PRIMARY KEY (session_id, code)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE alarm_engine_state")
