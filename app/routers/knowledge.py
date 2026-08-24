from fastapi import APIRouter, Query

from app.deps import CurrentUser, LLMDep, SessionDep, SettingsDep
from app.schemas import PassageOut, SearchOut
from app.services.rag import retrieve

# Router riêng: nếu đặt /search dưới prefix /v1/documents thì sẽ bị route
# /v1/documents/{document_id} nuốt mất khi dùng method GET.
router = APIRouter(prefix="/v1/knowledge", tags=["knowledge-base"])


@router.get("/search", response_model=SearchOut)
async def search_knowledge_base(
    session: SessionDep,
    settings: SettingsDep,
    llm: LLMDep,
    user: CurrentUser,
    q: str = Query(min_length=2, max_length=500),
    top_k: int = Query(default=5, ge=1, le=20),
    school_code: str | None = None,
) -> SearchOut:
    """
    Thử truy xuất trực tiếp, không sinh câu trả lời — dùng để kiểm tra chất lượng
    RAG (đoạn nào được lấy ra, điểm bao nhiêu) tách biệt khỏi chất lượng model.
    """
    passages, _ = await retrieve(
        session, llm, settings, query=q, top_k=top_k, school_code=school_code
    )
    return SearchOut(
        query=q,
        passages=[
            PassageOut(
                chunk_id=p.chunk_id,
                document_id=p.document_id,
                document_title=p.document_title,
                heading=p.heading,
                content=p.content,
                score=round(p.score, 4),
            )
            for p in passages
        ],
    )
