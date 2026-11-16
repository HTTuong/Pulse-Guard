"""Allow a bed stay that ends at the moment it starts.

Found in d240: a discharge at the same instant as the admission, or after the wall clock was set
back, broke bed_stay_order_ck (to_at > from_at) and the discharge failed with 500. Discharge now
uses max(now, from_at) and the check allows to_at = from_at.

Revision: 0002
Previous: 0001
Created: 2026-11-16
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE bed_stay DROP CONSTRAINT bed_stay_order_ck, "
        "ADD CONSTRAINT bed_stay_order_ck CHECK (to_at IS NULL OR to_at >= from_at)"
    )


def downgrade() -> None:
    # Fails while a zero length stay exists; that row must be dealt with by hand first.
    op.execute(
        "ALTER TABLE bed_stay DROP CONSTRAINT bed_stay_order_ck, "
        "ADD CONSTRAINT bed_stay_order_ck CHECK (to_at IS NULL OR to_at > from_at)"
    )
