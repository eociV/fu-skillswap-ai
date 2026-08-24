from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.llm.client import LLMClient
from app.security import AuthError, AuthUser, verify_token

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_llm(request: Request) -> LLMClient:
    return request.app.state.llm


LLMDep = Annotated[LLMClient, Depends(get_llm)]


async def current_user(
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Thiếu Bearer token")
    try:
        return verify_token(authorization[7:], settings)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


CurrentUser = Annotated[AuthUser, Depends(current_user)]


async def admin_user(user: CurrentUser) -> AuthUser:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Chỉ admin được thao tác kho tri thức")
    return user


AdminUser = Annotated[AuthUser, Depends(admin_user)]


async def internal_caller(
    settings: SettingsDep,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    """
    Dùng cho endpoint BE gọi sang (kiểm duyệt). BE không có JWT của người dùng
    trong luồng ghi nên xác thực bằng token nội bộ dùng chung.
    """
    expected = settings.bot_access_token
    if not expected or x_internal_token != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token nội bộ không hợp lệ")
