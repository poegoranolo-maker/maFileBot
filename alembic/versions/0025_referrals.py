"""Add referrals and allow two reward promos per purchase.

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("ix_promo_codes_generated_for_order_id", table_name="promo_codes")
    op.create_index(
        "ix_promo_codes_generated_for_order_id",
        "promo_codes",
        ["generated_for_order_id"],
        unique=False,
    )
    op.create_table(
        "referrals",
        sa.Column("referrer_id", sa.BigInteger(), nullable=False),
        sa.Column("referred_id", sa.BigInteger(), nullable=False),
        sa.Column("first_purchase_order_id", sa.String(length=32), nullable=True),
        sa.Column("rewarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["referred_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["referrer_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("referred_id"),
    )
    op.create_index("ix_referrals_referrer_id", "referrals", ["referrer_id"])


def downgrade():
    op.drop_index("ix_referrals_referrer_id", table_name="referrals")
    op.drop_table("referrals")
    op.drop_index("ix_promo_codes_generated_for_order_id", table_name="promo_codes")
    op.create_index(
        "ix_promo_codes_generated_for_order_id",
        "promo_codes",
        ["generated_for_order_id"],
        unique=True,
    )
