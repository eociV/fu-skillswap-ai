from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.services.forum_bot import parse_backend_time, should_skip


def _settings(**over) -> Settings:
    base = dict(
        database_url="postgresql+asyncpg://x/y",
        jwt_secret_key="c2VjcmV0",
        forum_grace_minutes=15,
        forum_bot_user_id="bot-uuid",
    )
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def _post(**over) -> dict:
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    base = {
        "postId": "p1",
        "status": "ACTIVE",
        "commentCount": 0,
        "authorUserId": "student-uuid",
        "createdAt": old.strftime("%Y-%m-%d %H:%M:%S"),
        "title": "Hỏi PRJ301",
        "content": "Em cần học Java web",
    }
    base.update(over)
    return base


class TestParseBackendTime:
    def test_local_datetime_format_from_be(self):
        # BE trả LocalDateTime không có timezone: "2026-07-08 15:40:00"
        parsed = parse_backend_time("2026-07-08 15:40:00")
        assert parsed is not None and parsed.tzinfo is not None

    def test_iso_format(self):
        assert parse_backend_time("2026-07-08T15:40:00+07:00") is not None

    def test_garbage_returns_none(self):
        assert parse_backend_time("hôm qua") is None
        assert parse_backend_time(None) is None


class TestShouldSkip:
    def test_eligible_post_is_not_skipped(self):
        assert should_skip(_post(), _settings()) is None

    def test_post_with_comments_skipped(self):
        # Người thật đã trả lời -> bot không chen vào.
        assert should_skip(_post(commentCount=3), _settings()) == "skipped_has_comments"

    def test_inactive_post_skipped(self):
        assert should_skip(_post(status="HIDDEN"), _settings()) == "skipped_inactive"

    def test_within_grace_period_skipped(self):
        fresh = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        assert should_skip(_post(createdAt=fresh), _settings()) == "skipped_within_grace"

    def test_grace_boundary_respects_config(self):
        five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        assert should_skip(_post(createdAt=five_min_ago), _settings()) == "skipped_within_grace"
        assert should_skip(_post(createdAt=five_min_ago), _settings(forum_grace_minutes=1)) is None

    def test_bot_never_answers_its_own_post(self):
        assert should_skip(_post(authorUserId="bot-uuid"), _settings()) == "skipped_own_post"

    def test_unparseable_date_does_not_block(self):
        # Không đọc được ngày thì để các bước sau quyết định, không loại oan.
        assert should_skip(_post(createdAt="???"), _settings()) is None
