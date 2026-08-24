import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.llm.client import LLMClient, LLMError
from app.models import ForumBotPost, MentorSuggestion
from app.prompts.forum import (
    FORUM_ANSWER_SYSTEM,
    FORUM_PROMPT_VERSION,
    FORUM_TRIAGE_SYSTEM,
    build_answer_prompt,
)
from app.services.backend import BackendClient, BackendError, mentor_id_of, mentor_name_of
from app.services.rag import build_context, retrieve
from app.services.usage import record_usage, within_budget

logger = logging.getLogger(__name__)

MAX_POST_CHARS = 4000
MAX_COMMENT_CHARS = 2000
MAX_MENTORS = 2

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "isQuestion": {"type": "boolean"},
        "needsMentor": {"type": "boolean"},
        "mentorQuery": {"type": "string"},
        "topic": {"type": "string"},
    },
    "required": ["isQuestion", "needsMentor", "mentorQuery", "topic"],
    "additionalProperties": False,
}


@dataclass
class BotOutcome:
    status: str  # answered | skipped_* | error
    detail: str | None = None
    comment_id: str | None = None
    mentors: list[str] | None = None


def parse_backend_time(value: str | None) -> datetime | None:
    """BE trả LocalDateTime dạng "2026-07-08 15:40:00" (không timezone) hoặc ISO."""
    if not value:
        return None
    text = value if "T" in value else value.replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def replies_last_hour(session: AsyncSession) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    return (
        await session.scalar(
            select(func.count(ForumBotPost.post_id)).where(
                ForumBotPost.status == "answered", ForumBotPost.processed_at >= cutoff
            )
        )
    ) or 0


def should_skip(post: dict[str, Any], settings: Settings) -> str | None:
    """Các điều kiện loại trừ rẻ tiền, chạy trước khi tốn một lượt gọi model."""
    if post.get("status") not in (None, "ACTIVE"):
        return "skipped_inactive"
    if (post.get("commentCount") or 0) > 0:
        return "skipped_has_comments"
    if settings.forum_bot_user_id and str(post.get("authorUserId")) == settings.forum_bot_user_id:
        return "skipped_own_post"
    created = parse_backend_time(post.get("createdAt"))
    if created is None:
        return None
    age = datetime.now(timezone.utc) - created
    # Chừa thời gian cho người thật trả lời trước — bot chỉ vào khi cộng đồng im lặng.
    if age < timedelta(minutes=settings.forum_grace_minutes):
        return "skipped_within_grace"
    return None


async def process_post(
    session: AsyncSession,
    llm: LLMClient,
    settings: Settings,
    backend: BackendClient,
    post: dict[str, Any],
) -> BotOutcome:
    """
    Một bài forum -> phân loại -> RAG -> gợi ý mentor -> đăng bình luận.

    Đây là phễu chuyển đổi của bản pitch: trả lời sơ bộ để tạo giá trị miễn phí,
    nhận diện nhu cầu chiều sâu, rồi dẫn tới mentor có trả phí.
    """
    post_id = str(post.get("postId") or post.get("id") or "")
    if not post_id:
        return BotOutcome("error", "Thiếu postId")

    if await session.get(ForumBotPost, post_id):
        return BotOutcome("skipped_already_processed")

    if reason := should_skip(post, settings):
        return await _record(session, post_id, BotOutcome(reason))

    if not await within_budget(session, settings):
        return BotOutcome("skipped_budget")  # không ghi sổ -> lần sau thử lại

    if await replies_last_hour(session) >= settings.forum_max_replies_per_hour:
        return BotOutcome("skipped_hourly_limit")

    title = str(post.get("title") or "")[:300]
    content = str(post.get("content") or "")[:MAX_POST_CHARS]
    program = ((post.get("authorProgram") or {}) or {}).get("name")

    post_block = (
        f"<BAI_VIET>\nTiêu đề: {title}\nNội dung: {content}\n</BAI_VIET>"
    )

    # 1) Phân loại + nhận diện nhu cầu chiều sâu trong một lượt gọi.
    started = time.monotonic()
    try:
        triage, tokens_in, tokens_out = await llm.json_object(
            model=settings.model_classify,
            system=FORUM_TRIAGE_SYSTEM,
            user=post_block,
            schema=TRIAGE_SCHEMA,
            schema_name="forum_triage",
            max_tokens=200,
        )
    except (LLMError, ValueError) as exc:
        logger.warning("Phân loại bài %s thất bại: %s", post_id, str(exc)[:200])
        return BotOutcome("error", str(exc)[:300])

    await record_usage(
        session, settings,
        feature="forum_triage",
        model=settings.model_classify,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        prompt_version=FORUM_PROMPT_VERSION,
        latency_ms=int((time.monotonic() - started) * 1000),
    )

    if not triage.get("isQuestion"):
        return await _record(session, post_id, BotOutcome("skipped_not_question"))

    topic = str(triage.get("topic") or "") or None

    # 2) Truy xuất kho tri thức đã kiểm duyệt.
    context_block = ""
    try:
        passages, embed_tokens = await retrieve(
            session, llm, settings, query=f"{title}\n{content}"[:1000], top_k=4
        )
        context_block = build_context(passages)
        if embed_tokens:
            await record_usage(
                session, settings,
                feature="forum_rag",
                model=settings.model_embedding,
                input_tokens=embed_tokens,
                output_tokens=0,
            )
    except LLMError as exc:
        # Không có ngữ cảnh vẫn trả lời được, chỉ kém chính xác hơn.
        logger.warning("Truy xuất RAG cho bài %s thất bại: %s", post_id, str(exc)[:200])

    # 3) Tìm mentor khi câu hỏi thật sự cần người kèm.
    mentor_block, mentor_ids = "", []
    if triage.get("needsMentor") and (query := str(triage.get("mentorQuery") or "").strip()):
        mentor_block, mentor_ids = await _find_mentors(backend, query)

    # 4) Sinh câu trả lời.
    started = time.monotonic()
    try:
        message, tokens_in, tokens_out = await llm.chat(
            model=settings.model_chat,
            messages=[
                {"role": "system", "content": FORUM_ANSWER_SYSTEM},
                {
                    "role": "user",
                    "content": build_answer_prompt(
                        title=title, content=content, topic=topic, program=program,
                        context_block=context_block, mentor_block=mentor_block,
                    ),
                },
            ],
            max_tokens=700,
            temperature=0.4,
        )
    except LLMError as exc:
        logger.warning("Sinh câu trả lời cho bài %s thất bại: %s", post_id, str(exc)[:200])
        return BotOutcome("error", str(exc)[:300])

    await record_usage(
        session, settings,
        feature="forum_answer",
        model=settings.model_chat,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        prompt_version=FORUM_PROMPT_VERSION,
        latency_ms=int((time.monotonic() - started) * 1000),
    )

    answer = str(message.get("content") or "").strip()
    if not answer:
        return await _record(session, post_id, BotOutcome("error", "Model trả về rỗng"))

    # 5) Đăng bình luận. Tôn trọng bộ lọc từ cấm của BE.
    try:
        created = await backend.create_forum_comment(post_id, answer[:MAX_COMMENT_CHARS])
    except BackendError as exc:
        if "PROHIBITED" in exc.code:
            return await _record(session, post_id, BotOutcome("skipped_prohibited", exc.code))
        logger.warning("Đăng bình luận cho bài %s thất bại: %s", post_id, exc)
        return await _record(session, post_id, BotOutcome("error", str(exc)[:300]))

    comment_id = str(created.get("commentId") or created.get("id") or "") or None

    # 6) Ghi nhận mentor đã gợi ý — số liệu phễu chuyển đổi.
    await _record_suggestions(session, settings, backend, post_id, post, mentor_ids)

    logger.info("Đã trả lời bài %s (mentor gợi ý: %d)", post_id, len(mentor_ids))
    return await _record(
        session, post_id, BotOutcome("answered", comment_id=comment_id, mentors=mentor_ids)
    )


async def _find_mentors(backend: BackendClient, query: str) -> tuple[str, list[str]]:
    try:
        mentors = await backend.search_mentors(query, size=MAX_MENTORS)
    except BackendError as exc:
        logger.warning("Tìm mentor thất bại: %s", exc)
        return "", []

    lines, ids = [], []
    for mentor in mentors[:MAX_MENTORS]:
        mentor_id = mentor_id_of(mentor)
        if not mentor_id:
            continue
        ids.append(mentor_id)
        headline = mentor.get("headline") or mentor.get("bio") or ""
        subjects = mentor.get("subjectResults") or []
        subject_text = ", ".join(
            str(s.get("subjectCode") or s.get("code") or "") for s in subjects[:3] if isinstance(s, dict)
        )
        detail = " · ".join(x for x in (str(headline)[:120], subject_text) if x)
        lines.append(f"- {mentor_name_of(mentor)}{f': {detail}' if detail else ''}")
    return "\n".join(lines), ids


async def _record_suggestions(
    session: AsyncSession,
    settings: Settings,
    backend: BackendClient,
    post_id: str,
    post: dict[str, Any],
    mentor_ids: list[str],
) -> None:
    if not mentor_ids:
        return
    asker = str(post.get("authorUserId") or "") or None
    for mentor_id in mentor_ids:
        session.add(
            MentorSuggestion(
                feature="forum_answer",
                source_ref=post_id,
                mentor_user_id=mentor_id,
                asker_user_id=asker,
                forwarded_to_be=settings.funnel_events_enabled,
            )
        )
    await session.commit()

    # Chỉ bật khi BE đã thêm MentorFunnelSource.AI_FORUM_ANSWER, nếu không sẽ 400.
    if settings.funnel_events_enabled:
        for mentor_id in mentor_ids:
            await backend.send_funnel_event(
                mentor_user_id=mentor_id,
                event_type="SERVICE_VIEWED",
                source="AI_FORUM_ANSWER",
            )


async def _record(session: AsyncSession, post_id: str, outcome: BotOutcome) -> BotOutcome:
    """Ghi kết quả để không bao giờ xử lý lại cùng một bài."""
    session.add(
        ForumBotPost(
            post_id=post_id,
            status=outcome.status,
            detail=outcome.detail,
            answered_comment_id=outcome.comment_id,
        )
    )
    await session.commit()
    return outcome


async def scan_recent_posts(
    session: AsyncSession, llm: LLMClient, settings: Settings, backend: BackendClient, limit: int = 10
) -> list[dict[str, Any]]:
    """
    Quét bài mới (chế độ MVP khi BE chưa bắn event). Mỗi vòng chỉ xử lý vài bài
    để không đốt hạn mức 50 request/phút.
    """
    try:
        page = await backend.list_forum_posts()
    except BackendError as exc:
        logger.error("Không lấy được danh sách bài forum: %s", exc)
        return []

    results = []
    for post in (page.get("items") or [])[:limit]:
        outcome = await process_post(session, llm, settings, backend, post)
        results.append({"postId": post.get("postId"), "status": outcome.status})
        if outcome.status == "skipped_hourly_limit":
            break
    return results
