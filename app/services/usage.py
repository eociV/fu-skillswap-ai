import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.llm.pricing import cost_micro_vnd
from app.models import AiUsage

logger = logging.getLogger(__name__)


async def record_usage(
    session: AsyncSession,
    settings: Settings,
    *,
    feature: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    user_hash: str | None = None,
    prompt_version: str | None = None,
    latency_ms: int | None = None,
    request_id: str | None = None,
) -> None:
    """Ghi sổ cái. Lỗi ở đây không được làm hỏng request chính."""
    try:
        session.add(
            AiUsage(
                day=date.today().isoformat(),
                feature=feature,
                model=model,
                user_hash=user_hash,
                prompt_version=prompt_version,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_micro_vnd=cost_micro_vnd(
                    model, input_tokens, output_tokens, settings.usd_to_vnd
                ),
                latency_ms=latency_ms,
                request_id=request_id,
            )
        )
        await session.commit()
    except Exception:  # noqa: BLE001 - sổ cái không bao giờ được chặn luồng chính
        logger.exception("Ghi sổ cái thất bại")
        await session.rollback()


async def spent_today_vnd(session: AsyncSession) -> float:
    total = await session.scalar(
        select(func.coalesce(func.sum(AiUsage.cost_micro_vnd), 0)).where(
            AiUsage.day == date.today().isoformat()
        )
    )
    return (total or 0) / 1_000_000


async def within_budget(session: AsyncSession, settings: Settings) -> bool:
    """
    Ngắt mạch theo ngày. Đây là thứ bảo vệ 5 triệu ngân sách: hạn mức 50 req/phút
    của FPT chỉ giới hạn tốc độ, một vòng lặp lỗi vẫn có thể đốt sạch trong vài ngày.
    Fail-open khi DB lỗi — không để sự cố phụ trợ làm chết tính năng.
    """
    try:
        spent = await spent_today_vnd(session)
    except Exception:  # noqa: BLE001
        logger.exception("Không kiểm tra được ngân sách — tạm cho qua")
        return True
    if spent >= settings.daily_budget_vnd:
        logger.warning("Chạm trần ngân sách ngày: %.0fđ / %dđ", spent, settings.daily_budget_vnd)
        return False
    return True
