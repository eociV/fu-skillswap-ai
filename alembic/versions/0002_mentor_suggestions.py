"""Bảng ghi nhận mentor do AI gợi ý — số liệu phễu chuyển đổi

Revision ID: 0002_mentor_suggestions
Revises: 0001_init
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_mentor_suggestions"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mentor_suggestions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("feature", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(64)),
        sa.Column("mentor_user_id", sa.String(64), nullable=False),
        sa.Column("asker_user_id", sa.String(64)),
        sa.Column("forwarded_to_be", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_mentor_suggestions_feature", "mentor_suggestions", ["feature"])
    op.create_index("ix_mentor_suggestions_source_ref", "mentor_suggestions", ["source_ref"])
    op.create_index("ix_mentor_suggestions_mentor", "mentor_suggestions", ["mentor_user_id"])


def downgrade() -> None:
    op.drop_table("mentor_suggestions")
