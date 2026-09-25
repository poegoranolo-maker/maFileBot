"""Remove the referrer account age restriction.

Revision ID: 0027
Revises: 0026
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE referrals "
        "SET eligible = true, ineligible_reason = NULL "
        "WHERE ineligible_reason = 'referrer_too_new'"
    )


def downgrade():
    pass
