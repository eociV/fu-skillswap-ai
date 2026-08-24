from datetime import datetime

from pydantic import BaseModel, Field


class DocumentOut(BaseModel):
    id: str
    title: str
    source_type: str
    school_code: str | None
    topic: str | None
    filename: str | None
    size_bytes: int | None
    status: str
    error: str | None
    chunk_count: int
    is_active: bool
    created_at: datetime
    indexed_at: datetime | None


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int


class TextDocumentIn(BaseModel):
    """Nạp tri thức bằng cách dán thẳng văn bản — tiện để thử nhanh, không cần file."""

    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=50)
    school_code: str | None = Field(default=None, max_length=32)
    topic: str | None = Field(default=None, max_length=128)


class PassageOut(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    heading: str | None
    content: str
    score: float


class SearchOut(BaseModel):
    query: str
    passages: list[PassageOut]


class ModerationIn(BaseModel):
    target_type: str = Field(pattern="^(post|comment)$")
    target_id: str = Field(max_length=64)
    author_user_id: str | None = None
    title: str | None = Field(default=None, max_length=500)
    content: str = Field(min_length=1)


class ModerationOut(BaseModel):
    decision: str  # allow | review | block
    max_severity: int
    categories: dict[str, int]
    reason: str | None = None
    degraded: bool = False  # True nghĩa là AI không chạy được -> mặc định cho qua


class BudgetOut(BaseModel):
    day: str
    spent_vnd: float
    budget_vnd: int
    remaining_vnd: float
    by_feature: dict[str, float]
