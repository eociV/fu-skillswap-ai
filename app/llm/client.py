import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from app.config import Settings
from app.llm.pricing import estimate_tokens
from app.llm.ratelimit import ModelRateLimiter

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class RateLimitedError(LLMError):
    pass


class LLMClient:
    """
    Adapter cho FPT AI Factory (API tương thích OpenAI).

    Hai chỗ phòng thủ có chủ đích, vì tài liệu FPT chưa khẳng định rõ:

    1. Vỏ response — tài liệu hiển thị `{"code":200,"data":{...}}` nhưng gateway
       có thể trả thẳng shape OpenAI. `_unwrap` nhận cả hai nên không phải chờ
       xác minh mới chạy được.
    2. `response_format=json_schema` — không có trong tài liệu. Client thử dùng
       trước; nếu model từ chối thì tự nhớ và chuyển sang ép JSON bằng prompt.

    Đổi nhà cung cấp (Bedrock, Anthropic, ...) chỉ cần thay lớp này.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.fpt_base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {settings.fpt_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
        self._limiter = ModelRateLimiter(settings.rate_limit_rpm, settings.rate_limit_tpm)
        # model -> có hỗ trợ response_format json_schema hay không (None = chưa thử)
        self._json_schema_support: dict[str, bool | None] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _unwrap(payload: Any) -> dict[str, Any]:
        """Bóc vỏ `{"code":..,"data":{..}}` của FPT; giữ nguyên nếu đã là shape OpenAI."""
        if not isinstance(payload, dict):
            raise LLMError(f"Response không phải JSON object: {type(payload)!r}")
        if "choices" in payload or "data" not in payload:
            return payload
        inner = payload.get("data")
        return inner if isinstance(inner, dict) else payload

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            res = await self._client.post(path, json=body)
        except httpx.TimeoutException as exc:
            raise LLMError(f"Timeout khi gọi {path}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Lỗi mạng khi gọi {path}: {exc}") from exc

        if res.status_code == 429:
            raise RateLimitedError("FPT trả 429 — vượt hạn mức")
        if res.status_code >= 400:
            raise LLMError(f"{path} trả {res.status_code}: {res.text[:300]}")
        return self._unwrap(res.json())

    @staticmethod
    def _first_text(payload: dict[str, Any]) -> str:
        choices = payload.get("choices") or []
        if not choices:
            raise LLMError(f"Response không có choices: {json.dumps(payload)[:300]}")
        message = choices[0].get("message") or {}
        return (message.get("content") or "").strip()

    @staticmethod
    def _usage(payload: dict[str, Any]) -> tuple[int, int]:
        usage = payload.get("usage") or {}
        return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))

    # ------------------------------------------------------------------- chat

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
        temperature: float = 0.3,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], int, int]:
        """Trả về (message của assistant, input_tokens, output_tokens)."""
        estimated = sum(estimate_tokens(str(m.get("content", ""))) for m in messages) + max_tokens
        if not await self._limiter.acquire(model, estimated):
            raise RateLimitedError(f"Hết hạn mức cục bộ cho model {model}")

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        payload = await self._post("/chat/completions", body)
        choices = payload.get("choices") or []
        if not choices:
            raise LLMError("Response không có choices")
        tokens_in, tokens_out = self._usage(payload)
        return choices[0].get("message") or {}, tokens_in, tokens_out

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> AsyncGenerator[tuple[str, int, int], None]:
        """
        Sinh ra từng mảnh chữ. Mảnh cuối cùng mang usage (nếu gateway trả về);
        nếu không có thì ước lượng để sổ cái không bị rỗng.
        """
        estimated = sum(estimate_tokens(str(m.get("content", ""))) for m in messages) + max_tokens
        if not await self._limiter.acquire(model, estimated):
            raise RateLimitedError(f"Hết hạn mức cục bộ cho model {model}")

        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        tokens_in = tokens_out = 0
        produced = 0

        async with self._client.stream("POST", "/chat/completions", json=body) as res:
            if res.status_code == 429:
                raise RateLimitedError("FPT trả 429 — vượt hạn mức")
            if res.status_code >= 400:
                detail = (await res.aread()).decode("utf-8", "replace")[:300]
                raise LLMError(f"/chat/completions trả {res.status_code}: {detail}")

            async for line in res.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = self._unwrap(json.loads(data))
                except (json.JSONDecodeError, LLMError):
                    continue
                if usage := chunk.get("usage"):
                    tokens_in = int(usage.get("prompt_tokens", tokens_in))
                    tokens_out = int(usage.get("completion_tokens", tokens_out))
                for choice in chunk.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        produced += len(piece)
                        yield piece, 0, 0

        if not tokens_in:
            tokens_in = estimated - max_tokens
        if not tokens_out:
            tokens_out = max(1, produced // 3)
        yield "", tokens_in, tokens_out

    # ------------------------------------------------- structured JSON output

    async def json_object(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str = "result",
        max_tokens: int = 512,
    ) -> tuple[dict[str, Any], int, int]:
        """
        Lấy JSON đúng schema. Ưu tiên `response_format` gốc; model nào không
        hỗ trợ thì tự động chuyển sang ép bằng prompt (và nhớ để lần sau khỏi thử lại).
        """
        supports = self._json_schema_support.get(model)

        if supports is not False:
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.0,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": schema, "strict": True},
                },
            }
            estimated = estimate_tokens(system + user) + max_tokens
            if not await self._limiter.acquire(model, estimated):
                raise RateLimitedError(f"Hết hạn mức cục bộ cho model {model}")
            try:
                payload = await self._post("/chat/completions", body)
                self._json_schema_support[model] = True
                tokens_in, tokens_out = self._usage(payload)
                return json.loads(self._first_text(payload)), tokens_in, tokens_out
            except (LLMError, json.JSONDecodeError) as exc:
                if isinstance(exc, RateLimitedError):
                    raise
                logger.warning(
                    "Model %s không dùng được response_format json_schema (%s) — chuyển sang ép bằng prompt",
                    model,
                    str(exc)[:160],
                )
                self._json_schema_support[model] = False

        # Phương án dự phòng: yêu cầu JSON trong prompt rồi tự parse.
        guided_system = (
            f"{system}\n\n"
            "Chỉ trả về DUY NHẤT một JSON object hợp lệ theo schema dưới đây, "
            "không kèm giải thích, không bọc trong khối mã.\n"
            f"Schema: {json.dumps(schema, ensure_ascii=False)}"
        )
        message, tokens_in, tokens_out = await self.chat(
            model=model,
            messages=[
                {"role": "system", "content": guided_system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.0,
        )
        return _parse_loose_json(message.get("content") or ""), tokens_in, tokens_out

    # ------------------------------------------------------------- embeddings

    async def embed(self, *, model: str, inputs: list[str]) -> tuple[list[list[float]], int]:
        """Nhúng theo lô. Gọi từng câu một sẽ đụng trần 50 request/phút rất nhanh."""
        if not inputs:
            return [], 0
        estimated = sum(estimate_tokens(text) for text in inputs)
        if not await self._limiter.acquire(model, estimated):
            raise RateLimitedError(f"Hết hạn mức cục bộ cho model {model}")

        payload = await self._post("/embeddings", {"model": model, "input": inputs})
        rows = payload.get("data") or []
        if not isinstance(rows, list) or not rows:
            raise LLMError(f"Response embeddings rỗng: {json.dumps(payload)[:300]}")
        ordered = sorted(rows, key=lambda r: r.get("index", 0))
        vectors = [r["embedding"] for r in ordered]
        usage = payload.get("usage") or {}
        tokens = int(usage.get("prompt_tokens") or usage.get("total_tokens") or estimated)
        return vectors, tokens

    # ----------------------------------------------------------------- rerank

    async def rerank(
        self, *, model: str, query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]] | None:
        """
        Xếp lại thứ tự các đoạn đã truy xuất. Trả None nếu gateway không có
        endpoint rerank — khi đó gọi bên ngoài giữ nguyên thứ tự theo vector.
        """
        if not documents:
            return []
        estimated = estimate_tokens(query) + sum(estimate_tokens(d) for d in documents)
        if not await self._limiter.acquire(model, estimated):
            return None
        try:
            payload = await self._post(
                "/rerank",
                {"model": model, "query": query, "documents": documents, "top_n": top_n},
            )
        except LLMError as exc:
            logger.warning("Rerank không dùng được (%s) — giữ thứ tự theo vector", str(exc)[:160])
            return None
        results = payload.get("results") or payload.get("data") or []
        out: list[tuple[int, float]] = []
        for item in results:
            if not isinstance(item, dict) or "index" not in item:
                continue
            score = item.get("relevance_score", item.get("score", 0.0))
            out.append((int(item["index"]), float(score)))
        return out or None


def _parse_loose_json(text: str) -> dict[str, Any]:
    """Bóc JSON khỏi output có thể kèm ```json hoặc chữ thừa."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        cleaned = cleaned.removeprefix("json").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise LLMError(f"Không tìm thấy JSON trong output: {text[:200]}")
    return json.loads(cleaned[start : end + 1])
