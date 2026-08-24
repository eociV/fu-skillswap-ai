import json
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.llm.client import LLMClient, LLMError
from app.models import MentorSuggestion
from app.prompts.chat import CHAT_PROMPT_VERSION, CHAT_SYSTEM, CHAT_TRIAGE_SYSTEM, build_user_prompt
from app.security import AuthUser
from app.services.backend import BackendClient, BackendError, mentor_id_of, mentor_name_of
from app.services.rag import build_context, retrieve
from app.services.usage import record_usage

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 2000
MAX_MENTORS = 3
MAX_BOOKINGS_CHARS = 1500

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "needsMentorSearch": {"type": "boolean"},
        "mentorQuery": {"type": "string"},
        "needsBookings": {"type": "boolean"},
        "needsKnowledge": {"type": "boolean"},
    },
    "required": ["needsMentorSearch", "mentorQuery", "needsBookings", "needsKnowledge"],
    "additionalProperties": False,
}


@dataclass
class ChatContext:
    messages: list[dict[str, str]]
    tools_used: list[str] = field(default_factory=list)
    mentor_ids: list[str] = field(default_factory=list)
    triage_tokens: tuple[int, int] = (0, 0)
    embed_tokens: int = 0


def sanitize_history(raw: list[dict[str, Any]], max_history: int) -> list[dict[str, str]]:
    """Service không giữ trạng thái — FE gửi lại lịch sử, nên phải cắt gọt tại đây."""
    cleaned = [
        {"role": m["role"], "content": str(m.get("content") or "")[:MAX_MESSAGE_CHARS]}
        for m in raw[-max_history:]
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
    ]
    return cleaned


async def prepare(
    session: AsyncSession,
    llm: LLMClient,
    settings: Settings,
    backend: BackendClient,
    history: list[dict[str, str]],
) -> ChatContext:
    """
    Quyết định cần dữ liệu gì rồi đi lấy, trước khi sinh câu trả lời.

    Dùng một lượt gọi JSON rẻ tiền thay cho tool calling, vì khả năng hỗ trợ
    `tools` của model trên FPT chưa xác minh được. Khi đã test xong có thể đổi
    sang tool calling mà không đụng tới router hay FE.
    """
    ctx = ChatContext(messages=list(history))
    last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    if not last_user:
        return ctx

    try:
        triage, t_in, t_out = await llm.json_object(
            model=settings.model_classify,
            system=CHAT_TRIAGE_SYSTEM,
            user=f"<TIN_NHAN>\n{last_user}\n</TIN_NHAN>",
            schema=TRIAGE_SCHEMA,
            schema_name="chat_triage",
            max_tokens=150,
        )
        ctx.triage_tokens = (t_in, t_out)
    except (LLMError, ValueError) as exc:
        # Không phân loại được thì vẫn trả lời, chỉ là không có dữ liệu bổ sung.
        logger.warning("Phân loại chat thất bại: %s", str(exc)[:200])
        triage = {"needsKnowledge": True}

    context_block = mentor_block = bookings_block = ""

    if triage.get("needsKnowledge", True):
        try:
            passages, ctx.embed_tokens = await retrieve(
                session, llm, settings, query=last_user, top_k=4
            )
            context_block = build_context(passages, max_chars=4000)
            if passages:
                ctx.tools_used.append("knowledge_base")
        except LLMError as exc:
            logger.warning("Truy xuất RAG cho chat thất bại: %s", str(exc)[:200])

    if triage.get("needsMentorSearch") and (query := str(triage.get("mentorQuery") or "").strip()):
        try:
            mentors = await backend.search_mentors(query, size=MAX_MENTORS)
            lines = []
            for mentor in mentors[:MAX_MENTORS]:
                if mentor_id := mentor_id_of(mentor):
                    ctx.mentor_ids.append(mentor_id)
                headline = str(mentor.get("headline") or mentor.get("bio") or "")[:150]
                lines.append(f"- {mentor_name_of(mentor)}{f': {headline}' if headline else ''}")
            mentor_block = "\n".join(lines)
            if lines:
                ctx.tools_used.append("search_mentors")
        except BackendError as exc:
            logger.warning("Tìm mentor cho chat thất bại: %s", exc)

    if triage.get("needsBookings"):
        try:
            bookings = await backend.get_my_bookings()
            bookings_block = json.dumps(bookings, ensure_ascii=False)[:MAX_BOOKINGS_CHARS]
            ctx.tools_used.append("my_bookings")
        except BackendError as exc:
            logger.warning("Lấy booking cho chat thất bại: %s", exc)

    # Ghép dữ liệu vào tin nhắn cuối, giữ nguyên lịch sử phía trước.
    if context_block or mentor_block or bookings_block:
        for i in range(len(ctx.messages) - 1, -1, -1):
            if ctx.messages[i]["role"] == "user":
                ctx.messages[i] = {
                    "role": "user",
                    "content": build_user_prompt(
                        ctx.messages[i]["content"], context_block, mentor_block, bookings_block
                    ),
                }
                break
    return ctx


async def record_chat_usage(
    session: AsyncSession,
    settings: Settings,
    user: AuthUser,
    ctx: ChatContext,
    *,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    request_id: str,
) -> None:
    if any(ctx.triage_tokens):
        await record_usage(
            session, settings,
            feature="chat_triage",
            model=settings.model_classify,
            input_tokens=ctx.triage_tokens[0],
            output_tokens=ctx.triage_tokens[1],
            user_hash=user.hash,
            prompt_version=CHAT_PROMPT_VERSION,
        )
    if ctx.embed_tokens:
        await record_usage(
            session, settings,
            feature="chat_rag",
            model=settings.model_embedding,
            input_tokens=ctx.embed_tokens,
            output_tokens=0,
            user_hash=user.hash,
        )
    await record_usage(
        session, settings,
        feature="chat",
        model=settings.model_chat,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        user_hash=user.hash,
        prompt_version=CHAT_PROMPT_VERSION,
        latency_ms=latency_ms,
        request_id=request_id,
    )
    for mentor_id in ctx.mentor_ids:
        session.add(
            MentorSuggestion(
                feature="chat",
                source_ref=request_id,
                mentor_user_id=mentor_id,
                asker_user_id=user.user_id,
            )
        )
    if ctx.mentor_ids:
        await session.commit()


async def answer_once(
    llm: LLMClient, settings: Settings, ctx: ChatContext
) -> tuple[str, int, int]:
    message, tokens_in, tokens_out = await llm.chat(
        model=settings.model_chat,
        messages=[{"role": "system", "content": CHAT_SYSTEM}, *ctx.messages],
        max_tokens=800,
        temperature=0.5,
    )
    return str(message.get("content") or "").strip(), tokens_in, tokens_out


async def answer_stream(
    llm: LLMClient, settings: Settings, ctx: ChatContext
) -> AsyncGenerator[tuple[str, int, int], None]:
    async for piece, tokens_in, tokens_out in llm.chat_stream(
        model=settings.model_chat,
        messages=[{"role": "system", "content": CHAT_SYSTEM}, *ctx.messages],
        max_tokens=800,
        temperature=0.5,
    ):
        yield piece, tokens_in, tokens_out


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
