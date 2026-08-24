import base64
import time

import pytest
from jose import jwt

from app.config import Settings
from app.security import AuthError, verify_token

RAW_SECRET = b"skillswap-local-dev-secret-key-32bytes!!"
B64_SECRET = base64.b64encode(RAW_SECRET).decode()


def _settings(**over) -> Settings:
    base = dict(
        database_url="postgresql+asyncpg://x/y",
        jwt_algorithm="HS256",
        jwt_secret_key=B64_SECRET,
    )
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def _token(**claims) -> str:
    payload = {
        "userId": "11111111-2222-3333-4444-555555555555",
        "email": "vinh@fpt.edu.vn",
        "roles": ["MENTEE"],
        "tokenType": "ACCESS",
        "sub": "11111111-2222-3333-4444-555555555555",
        "exp": int(time.time()) + 600,
    }
    payload.update(claims)
    # BE ký bằng khóa đã giải base64 (JwtTokenProvider) — test phải làm y hệt.
    return jwt.encode(payload, RAW_SECRET, algorithm="HS256")


class TestVerifyToken:
    def test_valid_token(self):
        user = verify_token(_token(), _settings())
        assert user.user_id == "11111111-2222-3333-4444-555555555555"
        assert user.roles == ["MENTEE"]
        assert not user.is_admin

    def test_admin_roles_recognised(self):
        assert verify_token(_token(roles=["ADMIN"]), _settings()).is_admin
        assert verify_token(_token(roles=["SYSTEM_ADMIN"]), _settings()).is_admin

    def test_refresh_token_rejected(self):
        with pytest.raises(AuthError, match="access token"):
            verify_token(_token(tokenType="REFRESH"), _settings())

    def test_expired_token_rejected(self):
        with pytest.raises(AuthError):
            verify_token(_token(exp=int(time.time()) - 10), _settings())

    def test_wrong_secret_rejected(self):
        other = base64.b64encode(b"a-completely-different-secret-key!!!!").decode()
        with pytest.raises(AuthError):
            verify_token(_token(), _settings(jwt_secret_key=other))

    def test_missing_user_id_rejected(self):
        bad = jwt.encode({"email": "x@y.z", "exp": int(time.time()) + 60}, RAW_SECRET, "HS256")
        with pytest.raises(AuthError, match="userId"):
            verify_token(bad, _settings())

    def test_audience_enforced_when_configured(self):
        settings = _settings(jwt_audience="skillswap-app")
        with pytest.raises(AuthError):
            verify_token(_token(), settings)  # token không có aud
        ok = verify_token(_token(aud="skillswap-app"), settings)
        assert ok.email == "vinh@fpt.edu.vn"

    def test_user_hash_is_stable_and_not_raw_id(self):
        user = verify_token(_token(), _settings())
        assert user.user_id not in user.hash
        assert user.hash == verify_token(_token(), _settings()).hash

    def test_missing_secret_raises(self):
        with pytest.raises(AuthError, match="JWT_SECRET_KEY"):
            verify_token(_token(), _settings(jwt_secret_key=""))


def test_issuer_enforced_when_configured():
    """jose không tự bắt lỗi khi token thiếu claim iss — phải tự kiểm."""
    settings = _settings(jwt_issuer="skillswap-be")
    with pytest.raises(AuthError, match="issuer"):
        verify_token(_token(), settings)
    assert verify_token(_token(iss="skillswap-be"), settings).roles == ["MENTEE"]
