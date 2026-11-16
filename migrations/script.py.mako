"""${message}

Revision: ${up_revision}
Previous: ${down_revision | comma,n}
Created: ${create_date}
"""

from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = None
depends_on = None


def upgrade() -> None:
    ${upgrades if upgrades else 'op.execute("")'}


def downgrade() -> None:
    ${downgrades if downgrades else 'op.execute("")'}
