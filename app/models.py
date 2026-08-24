import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import get_settings

EMBEDDING_DIM = get_settings().embedding_dim


class Base(DeclarativeBase):
    pass


class Document(Base):
    """Một tài liệu trong kho tri thức đã kiểm duyệt (nguồn cho RAG)."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(500))
    # upload | blog | forum | manual
    source_type: Mapped[str] = mapped_column(String(32), default="upload")
    source_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Phạm vi trường học — chuẩn bị sẵn cho đa trường (FPTU, HCMUS, ...). NULL = dùng chung.
    school_code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    topic: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    s3_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # pending | processing | indexed | failed
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class Chunk(Base):
    """Một đoạn văn bản đã nhúng vector, đơn vị truy xuất của RAG."""

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="chunks")


class AiUsage(Base):
    """Sổ cái từng lượt gọi model — nguồn dữ liệu cho hạn mức ngân sách ngày."""

    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    feature: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(64))
    user_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_micro_vnd: Mapped[int] = mapped_column(BigInteger, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModerationLog(Base):
    """Nhật ký kiểm duyệt — phục vụ đối soát và yêu cầu gỡ nội dung khi cần."""

    __tablename__ = "moderation_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(String(16))  # post | comment
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    author_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision: Mapped[str] = mapped_column(String(16), index=True)  # allow | review | block | skipped
    max_severity: Mapped[int] = mapped_column(Integer, default=0)
    categories: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    excerpt: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ForumBotPost(Base):
    """Chống trả lời trùng: mỗi bài forum chỉ xử lý đúng một lần."""

    __tablename__ = "forum_bot_posts"

    post_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_comment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MentorSuggestion(Base):
    """
    Mỗi lần AI gợi ý một mentor — nguồn số liệu cho phễu chuyển đổi mà bản pitch
    hứa ("X% lượt đặt lịch đến từ diễn đàn do AI dẫn dắt"). Ghi tại đây thay vì
    chỉ dựa vào BE, để không mất dữ liệu trong lúc chờ BE thêm MentorFunnelSource.AI_*.
    """

    __tablename__ = "mentor_suggestions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    feature: Mapped[str] = mapped_column(String(32), index=True)  # forum_answer | chat
    source_ref: Mapped[str | None] = mapped_column(String(64), index=True)  # postId / conversation
    mentor_user_id: Mapped[str] = mapped_column(String(64), index=True)
    asker_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    forwarded_to_be: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiFeedback(Base):
    """Phản hồi 👍/👎 — nguồn bổ sung golden case cho bộ eval."""

    __tablename__ = "ai_feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    feature: Mapped[str] = mapped_column(String(32))
    user_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rating: Mapped[int] = mapped_column(Integer)
    message_excerpt: Mapped[str | None] = mapped_column(String(500), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_ai_usage_day_feature", AiUsage.day, AiUsage.feature)
Index("ix_documents_active_school", Document.is_active, Document.school_code)
