import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import text

from app.db import SessionLocal
from app.deps import CurrentUser, LLMDep, SessionDep, SettingsDep
from app.llm.client import LLMError, RateLimitedError
from app.models import AiFeedback
from app.services.backend import BackendClient
from app.services.chat import (
    answer_once,
    answer_stream,
    prepare,
    record_chat_usage,
    sanitize_history,
    sse,
)
from app.services.usage import within_budget

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    """Service không giữ trạng thái — FE gửi lại toàn bộ lịch sử mỗi lượt."""

    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = True


class ChatResponse(BaseModel):
    """Trả camelCase cho FE — phía JS không phải đổi quy ước đặt tên."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    content: str
    request_id: str
    sources_used: list[str] = []


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    feature: str = Field(pattern="^(chat|forum_answer|rerank)$")
    rating: int = Field(ge=-1, le=1)
    message_excerpt: str | None = Field(default=None, max_length=500)
    prompt_version: str | None = None


async def _check_rate_limit(session: SessionDep, settings: SettingsDep, user_hash: str) -> None:
    """Hạn mức theo giờ cho mỗi người dùng, đếm ngay từ sổ cái nên không cần store riêng."""
    used = await session.scalar(
        text(
            "SELECT COUNT(*) FROM ai_usage WHERE feature = 'chat' AND user_hash = :h "
            "AND created_at >= now() - interval '1 hour'"
        ),
        {"h": user_hash},
    )
    if (used or 0) >= settings.chat_rate_limit_per_hour:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Bạn đã hỏi khá nhiều trong một giờ qua, thử lại sau nhé.",
        )


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    llm: LLMDep,
    user: CurrentUser,
):
    """
    Chatbot. Mặc định trả SSE; gửi `"stream": false` để nhận JSON một lần như
    API thường (đường dễ hơn cho FE lúc mới tích hợp).
    """
    history = sanitize_history([m.model_dump() for m in payload.messages], settings.chat_max_history)
    if not history or history[-1]["role"] != "user":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tin nhắn cuối phải là của người dùng")

    await _check_rate_limit(session, settings, user.hash)
    if not await within_budget(session, settings):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Trợ lý AI đang tạm nghỉ, bạn quay lại sau nhé."
        )

    request_id = getattr(request.state, "request_id", "")
    backend = BackendClient(settings, user.raw_token)
    started = time.monotonic()

    try:
        ctx = await prepare(session, llm, settings, backend, history)
    except RateLimitedError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc

    if not payload.stream:
        try:
            content, tokens_in, tokens_out = await answer_once(llm, settings, ctx)
        except RateLimitedError as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
        except LLMError as exc:
            logger.warning("Chat thất bại (rid=%s): %s", request_id, str(exc)[:200])
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Trợ lý AI đang gặp sự cố.") from exc

        await record_chat_usage(
            session, settings, user, ctx,
            tokens_in=tokens_in, tokens_out=tokens_out,
            latency_ms=int((time.monotonic() - started) * 1000), request_id=request_id,
        )
        return ChatResponse(
            content=content, request_id=request_id, sources_used=ctx.tools_used
        ).model_dump(by_alias=True)

    async def event_stream() -> AsyncGenerator[str, None]:
        tokens_in = tokens_out = 0
        try:
            for source in ctx.tools_used:
                yield sse("tool", {"name": source})
            async for piece, t_in, t_out in answer_stream(llm, settings, ctx):
                if piece:
                    yield sse("text", {"text": piece})
                if t_in or t_out:
                    tokens_in, tokens_out = t_in, t_out
            yield sse("done", {"requestId": request_id})
        except RateLimitedError:
            yield sse("error", {"error": "RATE_LIMITED", "requestId": request_id})
        except Exception as exc:  # noqa: BLE001 - stream không được ném lỗi ra ngoài
            logger.warning("Stream chat lỗi (rid=%s): %s", request_id, str(exc)[:200])
            yield sse("error", {"error": "AI_ERROR", "requestId": request_id})
        finally:
            # Session của request đã đóng khi stream còn chạy -> mở session mới để ghi sổ.
            async with SessionLocal() as bg:
                await record_chat_usage(
                    bg, settings, user, ctx,
                    tokens_in=tokens_in, tokens_out=tokens_out,
                    latency_ms=int((time.monotonic() - started) * 1000), request_id=request_id,
                )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def feedback(
    payload: FeedbackRequest, session: SessionDep, user: CurrentUser
) -> None:
    """Nút 👍/👎 — nguồn bổ sung golden case cho bộ eval."""
    if payload.rating == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "rating phải là 1 hoặc -1")
    session.add(
        AiFeedback(
            feature=payload.feature,
            user_hash=user.hash,
            rating=payload.rating,
            message_excerpt=payload.message_excerpt,
            prompt_version=payload.prompt_version,
        )
    )
    await session.commit()
