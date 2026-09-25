"""Separate cart and referral reward promos.

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("promo_codes", sa.Column("reward_source", sa.String(length=16), nullable=True))
    op.create_index("ix_promo_codes_reward_source", "promo_codes", ["reward_source"])


def downgrade():
    op.drop_index("ix_promo_codes_reward_source", table_name="promo_codes")
    op.drop_column("promo_codes", "reward_source")
