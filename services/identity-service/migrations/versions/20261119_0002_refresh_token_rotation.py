"""sessions: refresh-token rotation and reuse detection

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("previous_refresh_token_hash", sa.LargeBinary(length=32), nullable=True))
    op.add_column("sessions", sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        op.f("ix_sessions_previous_refresh_token_hash"), "sessions", ["previous_refresh_token_hash"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sessions_previous_refresh_token_hash"), table_name="sessions")
    op.drop_column("sessions", "rotated_at")
    op.drop_column("sessions", "previous_refresh_token_hash")
