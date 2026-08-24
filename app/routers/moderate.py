from fastapi import APIRouter, Depends

from app.deps import LLMDep, SessionDep, SettingsDep, internal_caller
from app.schemas import ModerationIn, ModerationOut
from app.services.moderation import moderate

router = APIRouter(prefix="/v1/moderate", tags=["moderation"])


@router.post("", response_model=ModerationOut, dependencies=[Depends(internal_caller)])
async def moderate_content(
    payload: ModerationIn, session: SessionDep, settings: SettingsDep, llm: LLMDep
) -> ModerationOut:
    """
    BE gọi endpoint này trong luồng ghi forum.

    Hợp đồng với BE: gọi với timeout 2 giây và LUÔN fail-open — timeout, 5xx,
    hay `degraded=true` đều phải cho đăng bài. Endpoint này không bao giờ
    trả lỗi để chặn người dùng.
    """
    return await moderate(session, llm, settings, payload)
