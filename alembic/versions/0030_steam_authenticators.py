"""Store reusable Steam Guard authenticators.

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "steam_authenticators",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_name", sa.String(length=256), nullable=False),
        sa.Column("steam_id", sa.String(length=32), nullable=True),
        sa.Column("shared_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("steam_id"),
    )
    op.create_index("ix_steam_authenticators_account_name", "steam_authenticators", ["account_name"])
    op.add_column("products", sa.Column("steam_authenticator_id", sa.Integer(), nullable=True))
    op.create_index("ix_products_steam_authenticator_id", "products", ["steam_authenticator_id"])
    op.create_foreign_key(
        "fk_products_steam_authenticator_id",
        "products",
        "steam_authenticators",
        ["steam_authenticator_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint("fk_products_steam_authenticator_id", "products", type_="foreignkey")
    op.drop_index("ix_products_steam_authenticator_id", table_name="products")
    op.drop_column("products", "steam_authenticator_id")
    op.drop_index("ix_steam_authenticators_account_name", table_name="steam_authenticators")
    op.drop_table("steam_authenticators")
