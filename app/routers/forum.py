import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.deps import LLMDep, SessionDep, SettingsDep, internal_caller
from app.services.backend import BackendClient, BackendError
from app.services.forum_bot import process_post, scan_recent_posts

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/forum", tags=["forum-bot"], dependencies=[Depends(internal_caller)])


class ForumPostEvent(BaseModel):
    """BE bắn sự kiện ForumPostCreated qua outbox. Gửi kèm cả bài thì bot khỏi
    phải gọi ngược lại BE để lấy — bớt một request cho BE."""

    post_id: str = Field(alias="postId")
    post: dict | None = None

    model_config = {"populate_by_name": True}


class BotResult(BaseModel):
    post_id: str
    status: str
    comment_id: str | None = None
    mentors_suggested: int = 0


@router.post("/events", response_model=BotResult)
async def handle_post_created(
    event: ForumPostEvent, session: SessionDep, settings: SettingsDep, llm: LLMDep
) -> BotResult:
    """Xử lý một bài forum mới. Đây là đường chính khi BE đã có event."""
    if not settings.forum_bot_enabled:
        return BotResult(post_id=event.post_id, status="disabled")
    if not settings.bot_access_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Chưa cấu hình BOT_ACCESS_TOKEN")

    backend = BackendClient(settings, settings.bot_access_token)
    post = event.post
    if not post:
        try:
            post = await backend.get_forum_post(event.post_id)
        except BackendError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Không lấy được bài viết: {exc}") from exc
    post.setdefault("postId", event.post_id)

    outcome = await process_post(session, llm, settings, backend, post)
    return BotResult(
        post_id=event.post_id,
        status=outcome.status,
        comment_id=outcome.comment_id,
        mentors_suggested=len(outcome.mentors or []),
    )


@router.post("/scan")
async def scan(session: SessionDep, settings: SettingsDep, llm: LLMDep) -> dict:
    """
    Quét thủ công (chế độ MVP khi BE chưa bắn event, và để test bằng tay).
    Khi BE có event rồi thì tắt hẳn đường này đi.
    """
    if not settings.forum_bot_enabled:
        return {"status": "disabled", "results": []}
    if not settings.bot_access_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Chưa cấu hình BOT_ACCESS_TOKEN")

    backend = BackendClient(settings, settings.bot_access_token)
    return {"status": "ok", "results": await scan_recent_posts(session, llm, settings, backend)}
