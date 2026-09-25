"""Track referral visits that are not eligible for rewards.

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "referrals",
        sa.Column("eligible", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "referrals",
        sa.Column("ineligible_reason", sa.String(length=32), nullable=True),
    )


def downgrade():
    op.drop_column("referrals", "ineligible_reason")
    op.drop_column("referrals", "eligible")
