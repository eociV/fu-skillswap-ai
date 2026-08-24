"""Khởi tạo schema AI service: kho tri thức RAG, sổ cái, nhật ký kiểm duyệt

Revision ID: 0001_init
Revises:
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

from app.config import get_settings

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None

DIM = get_settings().embedding_dim


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="upload"),
        sa.Column("source_ref", sa.String(500)),
        sa.Column("school_code", sa.String(32)),
        sa.Column("topic", sa.String(128)),
        sa.Column("filename", sa.String(255)),
        sa.Column("content_type", sa.String(128)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("checksum", sa.String(64)),
        sa.Column("s3_key", sa.String(512)),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text()),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("uploaded_by", sa.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("indexed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_documents_school_code", "documents", ["school_code"])
    op.create_index("ix_documents_topic", "documents", ["topic"])
    op.create_index("ix_documents_checksum", "documents", ["checksum"])
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_active_school", "documents", ["is_active", "school_code"])

    op.create_table(
        "chunks",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(500)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding", Vector(DIM), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    # HNSW cho tìm kiếm cosine — nhanh hơn nhiều so với quét tuần tự khi kho lớn dần.
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    op.create_table(
        "ai_usage",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("feature", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("user_hash", sa.String(64)),
        sa.Column("prompt_version", sa.String(32)),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_micro_vnd", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("request_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ai_usage_day", "ai_usage", ["day"])
    op.create_index("ix_ai_usage_feature", "ai_usage", ["feature"])
    op.create_index("ix_ai_usage_day_feature", "ai_usage", ["day", "feature"])

    op.create_table(
        "moderation_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("author_user_id", sa.String(64)),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("max_severity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("categories", sa.Text()),
        sa.Column("model", sa.String(64)),
        sa.Column("excerpt", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_moderation_log_target_id", "moderation_log", ["target_id"])
    op.create_index("ix_moderation_log_decision", "moderation_log", ["decision"])

    op.create_table(
        "forum_bot_posts",
        sa.Column("post_id", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("answered_comment_id", sa.String(64)),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "ai_feedback",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("feature", sa.String(32), nullable=False),
        sa.Column("user_hash", sa.String(64)),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("message_excerpt", sa.String(500)),
        sa.Column("prompt_version", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("ai_feedback")
    op.drop_table("forum_bot_posts")
    op.drop_table("moderation_log")
    op.drop_table("ai_usage")
    op.drop_table("chunks")
    op.drop_table("documents")
