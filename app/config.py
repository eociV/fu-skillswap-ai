from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "dev"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    database_url: str

    # FPT AI Factory
    fpt_api_key: str = ""
    fpt_base_url: str = "https://mkp-api.fptcloud.com/v1"
    model_classify: str = "gpt-oss-20b"
    model_chat: str = "DeepSeek-V4-Flash"
    model_tools: str = "gpt-oss-120b"
    model_embedding: str = "Vietnamese_Embedding"
    model_rerank: str = "bge-reranker-v2-m3"
    embedding_dim: int = 1024
    rate_limit_rpm: int = 50
    rate_limit_tpm: int = 100_000

    # Ngân sách
    daily_budget_vnd: int = 25_000
    usd_to_vnd: int = 26_300

    # Auth
    jwt_algorithm: str = "HS256"
    jwt_secret_key: str = ""
    jwt_public_key: str = ""
    jwt_issuer: str = ""
    jwt_audience: str = ""

    # Backend
    backend_url: str = "http://spring-backend:8080"
    bot_access_token: str = ""

    # Forum bot
    forum_bot_enabled: bool = False
    forum_bot_poll_enabled: bool = False       # chỉ bật khi BE chưa bắn event
    forum_bot_poll_seconds: int = 300
    forum_grace_minutes: int = 15              # chờ người thật trả lời trước
    forum_max_replies_per_hour: int = 6
    forum_bot_user_id: str = ""                # userId của tài khoản "SkillSwap AI"
    funnel_events_enabled: bool = False        # bật khi BE thêm MentorFunnelSource.AI_*

    # Chat
    chat_rate_limit_per_hour: int = 20
    chat_max_history: int = 20

    # Storage
    s3_endpoint: str = ""
    s3_region: str = "us-east-1"
    s3_bucket: str = "skillswap-ai-docs"
    s3_access_key: str = ""
    s3_secret_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
