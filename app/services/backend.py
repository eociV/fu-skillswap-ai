import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class BackendError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.status_code = status_code
        self.code = code


class BackendClient:
    """
    Gọi API của BE SkillSwap.

    Hai loại danh tính: JWT của chính người dùng (chatbot gọi hộ họ, nên chỉ thấy
    đúng dữ liệu họ được phép thấy) và token tài khoản bot (forum bot đăng bình luận).
    """

    def __init__(self, settings: Settings, token: str, timeout: float = 8.0) -> None:
        self._base = settings.backend_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            res = await client.request(
                method,
                f"{self._base}{path}",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                **kwargs,
            )
        try:
            body = res.json()
        except ValueError:
            raise BackendError(res.status_code, "NON_JSON", res.text[:200]) from None
        if res.status_code >= 400:
            raise BackendError(
                res.status_code,
                str(body.get("code", "UNKNOWN")),
                str(body.get("message", "Backend error")),
            )
        # BE bọc mọi response trong ApiResponse<T>.
        return body.get("data", body) if isinstance(body, dict) else body

    async def get(self, path: str) -> Any:
        return await self._request("GET", path)

    async def post(self, path: str, payload: dict[str, Any]) -> Any:
        return await self._request("POST", path, json=payload)

    # ------------------------------------------------------------------ forum

    async def list_forum_posts(self, cursor: str | None = None) -> dict[str, Any]:
        query = f"?cursor={cursor}" if cursor else ""
        data = await self.get(f"/api/forum/posts{query}")
        return data if isinstance(data, dict) else {"items": [], "nextCursor": None}

    async def get_forum_post(self, post_id: str) -> dict[str, Any]:
        return await self.get(f"/api/forum/posts/{post_id}")

    async def create_forum_comment(self, post_id: str, content: str) -> dict[str, Any]:
        data = await self.post(
            f"/api/forum/posts/{post_id}/comments",
            {"content": content, "imageUrls": [], "replyToCommentId": None},
        )
        return data if isinstance(data, dict) else {}

    # ---------------------------------------------------------------- mentors

    async def search_mentors(self, query: str, size: int = 5) -> list[dict[str, Any]]:
        from urllib.parse import urlencode

        params = urlencode({"q": query, "size": size})
        data = await self.get(f"/api/mentors?{params}")
        if isinstance(data, dict):
            items = data.get("items") or data.get("content") or []
            return items if isinstance(items, list) else []
        return data if isinstance(data, list) else []

    async def get_recommendations(self) -> list[dict[str, Any]]:
        data = await self.get("/api/mentors/recommendations")
        if isinstance(data, dict):
            items = data.get("items") or data.get("content") or []
            return items if isinstance(items, list) else []
        return data if isinstance(data, list) else []

    async def get_matching_profile(self) -> dict[str, Any]:
        data = await self.get("/api/me/matching-profile")
        return data if isinstance(data, dict) else {}

    async def get_my_bookings(self) -> Any:
        return await self.get("/api/me/bookings")

    async def get_help_topics(self) -> Any:
        return await self.get("/api/catalog/help-topics")

    async def send_funnel_event(
        self, *, mentor_user_id: str, event_type: str, source: str
    ) -> None:
        """
        Ghi nhận phễu chuyển đổi. Chỉ gọi khi BE đã thêm các giá trị AI_* vào
        `MentorFunnelSource`, nếu không sẽ 400. Best-effort, không được làm hỏng
        luồng chính.
        """
        try:
            await self.post(
                "/api/mentor-discovery/funnel-events",
                {"eventType": event_type, "mentorUserId": mentor_user_id, "source": source},
            )
        except BackendError as exc:
            logger.warning("Gửi funnel event thất bại: %s", exc)


def mentor_id_of(mentor: dict[str, Any]) -> str | None:
    for key in ("mentorUserId", "userId", "id"):
        if value := mentor.get(key):
            return str(value)
    return None


def mentor_name_of(mentor: dict[str, Any]) -> str:
    for key in ("fullName", "displayName", "name"):
        if value := mentor.get(key):
            return str(value)
    return "Mentor"
