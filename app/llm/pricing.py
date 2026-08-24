"""Giá niêm yết FPT AI Marketplace (USD / 1M token), chốt ngày 09/08/2026."""

PRICES: dict[str, tuple[float, float]] = {
    "gpt-oss-20b": (0.0495, 0.198),
    "gpt-oss-120b": (0.143, 0.605),
    "DeepSeek-V4-Flash": (0.14, 0.28),
    "GLM-5.2": (1.4, 4.4),
    "Qwen3.6-27B": (0.3, 3.25),
    "Llama-3.3-70B-Instruct": (0.209, 0.451),
    "gemma-4-31B-it": (0.15, 0.45),
    "gemma-4-26B-A4B-it": (0.14, 0.4),
    "gemma-3-27b-it": (0.11, 0.165),
    "Vietnamese_Embedding": (0.011, 0.0),
    "multilingual-e5-large": (0.022, 0.0),
    "bge-reranker-v2-m3": (0.022, 0.0),
}

# Model lạ -> lấy giá cao nhất trong bảng để ước tính an toàn (thà tính thừa còn hơn thiếu).
_FALLBACK = (1.4, 4.4)


def cost_micro_vnd(model: str, input_tokens: int, output_tokens: int, usd_to_vnd: int) -> int:
    price_in, price_out = PRICES.get(model, _FALLBACK)
    usd = (input_tokens / 1_000_000) * price_in + (output_tokens / 1_000_000) * price_out
    return int(round(usd * usd_to_vnd * 1_000_000))


def estimate_tokens(text: str) -> int:
    """Ước lượng thô: tiếng Việt khoảng 3 ký tự/token. Chỉ dùng cho hạn mức, không để tính tiền."""
    return max(1, len(text) // 3)
