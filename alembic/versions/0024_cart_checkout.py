"""Add persistent carts and grouped checkouts.

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cart_items",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity = 1"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "product_id"),
    )
    op.add_column("orders", sa.Column("checkout_id", sa.String(length=32), nullable=True))
    op.add_column("orders", sa.Column("payment_total_snapshot", sa.Integer(), nullable=True))
    op.create_index("ix_orders_checkout_id", "orders", ["checkout_id"])


def downgrade():
    op.drop_index("ix_orders_checkout_id", table_name="orders")
    op.drop_column("orders", "payment_total_snapshot")
    op.drop_column("orders", "checkout_id")
    op.drop_table("cart_items")
