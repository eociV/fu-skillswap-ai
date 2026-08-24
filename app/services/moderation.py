import json
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.llm.client import LLMClient, LLMError
from app.models import ModerationLog
from app.prompts.moderation import MODERATION_PROMPT_VERSION, MODERATION_SYSTEM
from app.schemas import ModerationIn, ModerationOut
from app.services.usage import record_usage, within_budget

logger = logging.getLogger(__name__)

CATEGORIES = ("sexual", "toxic", "hate", "political", "spam")

SCHEMA = {
    "type": "object",
    "properties": {
        **{c: {"type": "integer", "minimum": 0, "maximum": 3} for c in CATEGORIES},
        "reason": {"type": "string"},
    },
    "required": [*CATEGORIES, "reason"],
    "additionalProperties": False,
}

BLOCK_AT = 3   # chặn thẳng
REVIEW_AT = 2  # cho đăng nhưng đẩy vào hàng đợi admin
MAX_INPUT_CHARS = 4000


async def moderate(
    session: AsyncSession,
    llm: LLMClient,
    settings: Settings,
    payload: ModerationIn,
) -> ModerationOut:
    """
    Chấm nội dung. LUÔN fail-open: hết hạn mức, hết ngân sách hay model lỗi thì
    trả `allow` kèm degraded=True. FPT chỉ cho 50 request/phút, mà một buổi
    thảo luận sôi nổi có thể sinh hơn 100 bài và bình luận — không bao giờ
    được để AI chặn đường ghi của forum.
    """
    excerpt = (payload.content or "")[:MAX_INPUT_CHARS]
    if not await within_budget(session, settings):
        return await _log_and_return(
            session, payload, ModerationOut(
                decision="allow", max_severity=0, categories={}, degraded=True,
                reason="Chạm trần ngân sách ngày",
            ), model=None,
        )

    user_block = (
        "<NOI_DUNG_CAN_KIEM_DUYET>\n"
        f"Tiêu đề: {payload.title or '(không có)'}\n"
        f"Nội dung: {excerpt}\n"
        "</NOI_DUNG_CAN_KIEM_DUYET>"
    )

    started = time.monotonic()
    try:
        result, tokens_in, tokens_out = await llm.json_object(
            model=settings.model_classify,
            system=MODERATION_SYSTEM,
            user=user_block,
            schema=SCHEMA,
            schema_name="moderation",
            max_tokens=256,
        )
    except (LLMError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Kiểm duyệt thất bại (%s) — fail-open", str(exc)[:200])
        return await _log_and_return(
            session, payload, ModerationOut(
                decision="allow", max_severity=0, categories={}, degraded=True,
                reason=str(exc)[:200],
            ), model=settings.model_classify,
        )

    await record_usage(
        session, settings,
        feature="moderation",
        model=settings.model_classify,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        prompt_version=MODERATION_PROMPT_VERSION,
        latency_ms=int((time.monotonic() - started) * 1000),
    )

    scores = {c: int(result.get(c, 0) or 0) for c in CATEGORIES}
    worst = max(scores.values(), default=0)
    decision = "block" if worst >= BLOCK_AT else "review" if worst >= REVIEW_AT else "allow"

    return await _log_and_return(
        session, payload,
        ModerationOut(
            decision=decision,
            max_severity=worst,
            categories=scores,
            reason=str(result.get("reason", ""))[:500] or None,
        ),
        model=settings.model_classify,
    )


async def _log_and_return(
    session: AsyncSession, payload: ModerationIn, out: ModerationOut, *, model: str | None
) -> ModerationOut:
    """Ghi nhật ký mọi quyết định — cần cho đối soát và khi phải chứng minh đã kiểm duyệt."""
    try:
        session.add(
            ModerationLog(
                target_type=payload.target_type,
                target_id=payload.target_id,
                author_user_id=payload.author_user_id,
                decision="skipped" if out.degraded else out.decision,
                max_severity=out.max_severity,
                categories=json.dumps(out.categories, ensure_ascii=False),
                model=model,
                excerpt=(payload.content or "")[:500],
            )
        )
        await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Ghi nhật ký kiểm duyệt thất bại")
        await session.rollback()
    return out
