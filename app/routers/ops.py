from datetime import date

from fastapi import APIRouter
from sqlalchemy import func, select

from app.deps import AdminUser, SessionDep, SettingsDep
from app.models import AiUsage
from app.schemas import BudgetOut

router = APIRouter(tags=["ops"])


@router.get("/healthz")
async def healthz(settings: SettingsDep) -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@router.get("/v1/ops/budget", response_model=BudgetOut)
async def budget(session: SessionDep, settings: SettingsDep, user: AdminUser) -> BudgetOut:
    """Chi tiêu hôm nay theo từng tính năng — dùng để canh 5 triệu ngân sách."""
    today = date.today().isoformat()
    rows = (
        await session.execute(
            select(AiUsage.feature, func.sum(AiUsage.cost_micro_vnd))
            .where(AiUsage.day == today)
            .group_by(AiUsage.feature)
        )
    ).all()
    by_feature = {feature: (total or 0) / 1_000_000 for feature, total in rows}
    spent = sum(by_feature.values())
    return BudgetOut(
        day=today,
        spent_vnd=round(spent, 2),
        budget_vnd=settings.daily_budget_vnd,
        remaining_vnd=round(max(0.0, settings.daily_budget_vnd - spent), 2),
        by_feature={k: round(v, 2) for k, v in by_feature.items()},
    )
