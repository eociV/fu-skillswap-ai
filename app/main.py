import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.llm.client import LLMClient
from app.routers import chat, documents, forum, knowledge, moderate, ops

settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)


async def _forum_poll_loop(app: FastAPI) -> None:
    """
    Chế độ MVP khi BE chưa bắn event ForumPostCreated. Tắt hẳn khi có event —
    polling gọi BE liên tục kể cả lúc diễn đàn im lặng.
    """
    from app.db import SessionLocal
    from app.services.backend import BackendClient
    from app.services.forum_bot import scan_recent_posts

    while True:
        await asyncio.sleep(settings.forum_bot_poll_seconds)
        if not (settings.forum_bot_enabled and settings.bot_access_token):
            continue
        try:
            async with SessionLocal() as session:
                backend = BackendClient(settings, settings.bot_access_token)
                results = await scan_recent_posts(session, app.state.llm, settings, backend)
            if results:
                logger.info("Forum bot quét xong: %s", results)
        except Exception:  # noqa: BLE001 - vòng lặp nền không được chết
            logger.exception("Vòng quét forum bot lỗi")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.llm = LLMClient(settings)
    poller = (
        asyncio.create_task(_forum_poll_loop(app)) if settings.forum_bot_poll_enabled else None
    )
    logger.info(
        "AI service khởi động (env=%s, forum_bot=%s, poll=%s)",
        settings.environment, settings.forum_bot_enabled, settings.forum_bot_poll_enabled,
    )
    yield
    if poller:
        poller.cancel()
    await app.state.llm.aclose()


app = FastAPI(
    title="SkillSwap AI Service",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Internal-Token"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Gắn request id và ghi log truy cập — chỉ metadata, không ghi nội dung."""
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    logger.info(
        "%s %s -> %s (rid=%s)", request.method, request.url.path, response.status_code, request_id
    )
    return response


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Lỗi chưa xử lý tại %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "INTERNAL_ERROR"})


app.include_router(ops.router)
app.include_router(documents.router)
app.include_router(knowledge.router)
app.include_router(moderate.router)
app.include_router(forum.router)
app.include_router(chat.router)
