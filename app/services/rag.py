import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.llm.client import LLMClient
from app.models import Chunk, Document

logger = logging.getLogger(__name__)

CANDIDATE_POOL = 40  # lấy rộng bằng vector rồi để reranker lọc lại


@dataclass
class Passage:
    chunk_id: str
    document_id: str
    document_title: str
    heading: str | None
    content: str
    score: float

    def citation(self) -> str:
        return f"{self.document_title}{f' — {self.heading}' if self.heading else ''}"


async def retrieve(
    session: AsyncSession,
    llm: LLMClient,
    settings: Settings,
    *,
    query: str,
    top_k: int = 5,
    school_code: str | None = None,
    topic: str | None = None,
) -> tuple[list[Passage], int]:
    """
    Truy xuất hai tầng: vector lấy rộng, reranker lọc tinh.
    Trả về (danh sách đoạn, số token đã dùng để nhúng câu hỏi).
    """
    vectors, tokens = await llm.embed(model=settings.model_embedding, inputs=[query])
    if not vectors:
        return [], tokens
    query_vector = vectors[0]

    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
    stmt = (
        select(Chunk, Document, distance)
        .join(Document, Chunk.document_id == Document.id)
        .where(Document.is_active.is_(True), Document.status == "indexed")
        .order_by(distance)
        .limit(CANDIDATE_POOL)
    )
    # Tài liệu không gắn trường (school_code NULL) là kiến thức dùng chung cho mọi trường.
    if school_code:
        stmt = stmt.where(
            (Document.school_code == school_code) | (Document.school_code.is_(None))
        )
    if topic:
        stmt = stmt.where(Document.topic == topic)

    rows = (await session.execute(stmt)).all()
    if not rows:
        return [], tokens

    candidates = [
        Passage(
            chunk_id=str(chunk.id),
            document_id=str(doc.id),
            document_title=doc.title,
            heading=chunk.heading,
            content=chunk.content,
            score=1.0 - float(dist),
        )
        for chunk, doc, dist in rows
    ]

    ranked = await llm.rerank(
        model=settings.model_rerank,
        query=query,
        documents=[c.content for c in candidates],
        top_n=top_k,
    )
    if ranked is None:
        return candidates[:top_k], tokens

    out: list[Passage] = []
    for index, score in ranked[:top_k]:
        if 0 <= index < len(candidates):
            passage = candidates[index]
            passage.score = score
            out.append(passage)
    return out or candidates[:top_k], tokens


def build_context(passages: list[Passage], max_chars: int = 6000) -> str:
    """
    Dựng khối ngữ cảnh có đánh số nguồn để model trích dẫn được.
    Nội dung tài liệu là DỮ LIỆU, không phải chỉ dẫn — prompt phải nói rõ điều đó.
    """
    parts: list[str] = []
    used = 0
    for i, passage in enumerate(passages, start=1):
        block = f"[{i}] {passage.citation()}\n{passage.content}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n---\n\n".join(parts)
