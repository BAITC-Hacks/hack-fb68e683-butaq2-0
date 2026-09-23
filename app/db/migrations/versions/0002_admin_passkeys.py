"""Persistent passkeys, single-use challenges and admin sessions.

Revision ID: 0002_admin_passkeys
Revises: 0001_router_config
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_admin_passkeys"
down_revision = "0001_router_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "router_admin_credentials",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "router_admin_challenges",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("challenge", sa.LargeBinary(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "router_admin_sessions",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("credential_id", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("router_admin_sessions")
    op.drop_table("router_admin_challenges")
    op.drop_table("router_admin_credentials")
