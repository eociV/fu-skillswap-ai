"""
Kiểm tra hàng rào xác thực ở cấp HTTP. Không cần database vì auth chặn trước
khi chạm tới truy vấn (SQLAlchemy async chỉ mở kết nối khi thực sự query).
"""
import base64
import time

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.main import app

RAW_SECRET = base64.b64decode(
    "c2tpbGxzd2FwLWxvY2FsLWRldi1zZWNyZXQta2V5LWZvci10ZXN0aW5nLW9ubHktMzJi"
)


def _token(roles: list[str]) -> str:
    return jwt.encode(
        {
            "userId": "11111111-2222-3333-4444-555555555555",
            "roles": roles,
            "tokenType": "ACCESS",
            "exp": int(time.time()) + 600,
        },
        RAW_SECRET,
        algorithm="HS256",
    )


@pytest.fixture(scope="module")
def client():
    # Phải dùng context manager để lifespan chạy (app.state.llm được tạo ở đó).
    with TestClient(app) as c:
        yield c


def test_healthz_is_public(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_upload_requires_token(client):
    res = client.post("/v1/documents", files={"file": ("a.txt", b"noi dung", "text/plain")})
    assert res.status_code == 401


def test_upload_rejects_non_admin(client):
    res = client.post(
        "/v1/documents",
        files={"file": ("a.txt", b"noi dung", "text/plain")},
        headers={"Authorization": f"Bearer {_token(['MENTEE'])}"},
    )
    assert res.status_code == 403


def test_knowledge_search_requires_token(client):
    assert client.get("/v1/knowledge/search", params={"q": "PRJ301"}).status_code == 401


def test_moderate_requires_internal_token(client):
    res = client.post(
        "/v1/moderate",
        json={"target_type": "post", "target_id": "p1", "content": "xin chào"},
    )
    assert res.status_code == 401


def test_moderate_rejects_wrong_internal_token(client):
    res = client.post(
        "/v1/moderate",
        json={"target_type": "post", "target_id": "p1", "content": "xin chào"},
        headers={"X-Internal-Token": "sai-token"},
    )
    assert res.status_code == 401


def test_moderate_validates_payload(client):
    res = client.post(
        "/v1/moderate",
        json={"target_type": "khong-hop-le", "target_id": "p1", "content": "x"},
        headers={"X-Internal-Token": "sai-token"},
    )
    assert res.status_code in (401, 422)


def test_budget_endpoint_is_admin_only(client):
    res = client.get(
        "/v1/ops/budget", headers={"Authorization": f"Bearer {_token(['MENTOR'])}"}
    )
    assert res.status_code == 403


def test_chat_requires_token(client):
    res = client.post("/v1/chat", json={"messages": [{"role": "user", "content": "chào"}]})
    assert res.status_code == 401


def test_chat_rejects_history_not_ending_with_user(client):
    res = client.post(
        "/v1/chat",
        json={"messages": [{"role": "assistant", "content": "chào bạn"}]},
        headers={"Authorization": f"Bearer {_token(['MENTEE'])}"},
    )
    assert res.status_code == 400


def test_feedback_requires_token(client):
    assert client.post("/v1/feedback", json={"feature": "chat", "rating": 1}).status_code == 401


def test_feedback_accepts_camel_case_from_fe(client):
    """FE gửi messageExcerpt (camelCase) — không được 422 vì lệch quy ước đặt tên."""
    from app.db import get_session
    from app.main import app as fastapi_app

    saved: list = []

    class _StubSession:
        def add(self, obj):
            saved.append(obj)

        async def commit(self):
            pass

    async def _stub():
        yield _StubSession()

    fastapi_app.dependency_overrides[get_session] = _stub
    try:
        res = client.post(
            "/v1/feedback",
            json={"feature": "chat", "rating": -1, "messageExcerpt": "câu trả lời sai"},
            headers={"Authorization": f"Bearer {_token(['MENTEE'])}"},
        )
    finally:
        fastapi_app.dependency_overrides.pop(get_session, None)

    assert res.status_code == 204
    assert saved and saved[0].message_excerpt == "câu trả lời sai"


def test_forum_endpoints_require_internal_token(client):
    assert client.post("/v1/forum/events", json={"postId": "p1"}).status_code == 401
    assert client.post("/v1/forum/scan").status_code == 401
