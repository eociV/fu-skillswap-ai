import base64
import hashlib
from dataclasses import dataclass
from typing import Any

from jose import JWTError, jwt

from app.config import Settings


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    email: str | None
    roles: list[str]
    raw_token: str

    @property
    def is_admin(self) -> bool:
        return any(r in ("ADMIN", "SYSTEM_ADMIN") for r in self.roles)

    @property
    def hash(self) -> str:
        """Băm userId để ghi log/sổ cái mà không lưu định danh thô."""
        return hashlib.sha256(self.user_id.encode()).hexdigest()


class AuthError(Exception):
    pass


def _signing_key(settings: Settings) -> Any:
    if settings.jwt_algorithm.upper().startswith("HS"):
        if not settings.jwt_secret_key:
            raise AuthError("Thiếu JWT_SECRET_KEY")
        # BE giải base64 secret rồi mới dựng khóa HMAC (JwtTokenProvider), phải làm y hệt.
        return base64.b64decode(settings.jwt_secret_key)
    if not settings.jwt_public_key:
        raise AuthError("Thiếu JWT_PUBLIC_KEY")
    return settings.jwt_public_key.replace("\\n", "\n")


def verify_token(token: str, settings: Settings) -> AuthUser:
    options = {
        "verify_aud": bool(settings.jwt_audience),
        "verify_iss": bool(settings.jwt_issuer),
    }
    try:
        claims = jwt.decode(
            token,
            _signing_key(settings),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience or None,
            issuer=settings.jwt_issuer or None,
            options=options,
        )
    except JWTError as exc:
        raise AuthError(f"Token không hợp lệ: {exc}") from exc

    # python-jose BỎ QUA audience/issuer khi token thiếu hẳn claim đó, kể cả khi
    # bật verify_aud — nên phải kiểm tra tường minh, nếu không cấu hình chỉ là trang trí.
    if settings.jwt_audience:
        aud = claims.get("aud")
        allowed = aud if isinstance(aud, list) else [aud]
        if settings.jwt_audience not in allowed:
            raise AuthError("Token sai audience")
    if settings.jwt_issuer and claims.get("iss") != settings.jwt_issuer:
        raise AuthError("Token sai issuer")

    # BE gắn tokenType để phân biệt access và refresh — chỉ nhận ACCESS.
    if claims.get("tokenType") not in (None, "ACCESS"):
        raise AuthError("Token không phải access token")

    user_id = claims.get("userId") or claims.get("sub")
    if not user_id:
        raise AuthError("Token thiếu userId")

    roles = claims.get("roles") or []
    if isinstance(roles, str):
        roles = [roles]

    return AuthUser(
        user_id=str(user_id),
        email=claims.get("email"),
        roles=[str(r) for r in roles],
        raw_token=token,
    )
