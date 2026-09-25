"""Store Gmail OAuth credentials once and assign mailboxes to products.

Revision ID: 0023
Revises: 0022
"""

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gmail_mailboxes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("credentials_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gmail_mailboxes_email", "gmail_mailboxes", ["email"], unique=True)
    op.add_column("products", sa.Column("gmail_mailbox_id", sa.Integer(), nullable=True))
    op.create_index("ix_products_gmail_mailbox_id", "products", ["gmail_mailbox_id"])
    op.create_foreign_key(
        "fk_products_gmail_mailbox_id", "products", "gmail_mailboxes", ["gmail_mailbox_id"], ["id"]
    )


def downgrade():
    op.drop_constraint("fk_products_gmail_mailbox_id", "products", type_="foreignkey")
    op.drop_index("ix_products_gmail_mailbox_id", table_name="products")
    op.drop_column("products", "gmail_mailbox_id")
    op.drop_index("ix_gmail_mailboxes_email", table_name="gmail_mailboxes")
    op.drop_table("gmail_mailboxes")
