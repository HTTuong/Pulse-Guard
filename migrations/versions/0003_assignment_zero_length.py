"""Allow a device assignment that ends at the moment it starts.

Found in d241 (F-02): unassigning a device at the same instant it was assigned, or after the wall
clock was set back, broke assignment_order_ck (unassigned_at > assigned_at) and failed with 500.
The same defect as F-01 for bed stays. Unassign now uses max(now, assigned_at).

Revision: 0003
Previous: 0002
Created: 2026-11-16
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE device_assignment DROP CONSTRAINT assignment_order_ck, "
        "ADD CONSTRAINT assignment_order_ck "
        "CHECK (unassigned_at IS NULL OR unassigned_at >= assigned_at)"
    )


def downgrade() -> None:
    # Fails while a zero length assignment exists; that row must be dealt with by hand first.
    op.execute(
        "ALTER TABLE device_assignment DROP CONSTRAINT assignment_order_ck, "
        "ADD CONSTRAINT assignment_order_ck "
        "CHECK (unassigned_at IS NULL OR unassigned_at > assigned_at)"
    )
