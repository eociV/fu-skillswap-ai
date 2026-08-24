import json

import pytest

from app.llm.client import LLMClient, LLMError, _parse_loose_json
from app.llm.pricing import cost_micro_vnd, estimate_tokens

OPENAI_SHAPE = {
    "id": "chatcmpl-1",
    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    "usage": {"prompt_tokens": 13, "completion_tokens": 10},
}


class TestUnwrap:
    """Tài liệu FPT hiển thị response bọc trong `data`, nhưng gateway có thể trả
    thẳng shape OpenAI. Client phải nhận được cả hai mà không cần biết trước."""

    def test_plain_openai_shape(self):
        assert LLMClient._unwrap(OPENAI_SHAPE) == OPENAI_SHAPE

    def test_fpt_wrapped_shape(self):
        wrapped = {"code": 200, "message": "Chat completion successful", "data": OPENAI_SHAPE}
        assert LLMClient._unwrap(wrapped) == OPENAI_SHAPE

    def test_embeddings_shape_not_mistaken_for_envelope(self):
        # /embeddings trả `data` là LIST -> không được bóc nhầm.
        payload = {"data": [{"index": 0, "embedding": [0.1, 0.2]}], "usage": {}}
        assert LLMClient._unwrap(payload) == payload

    def test_non_dict_raises(self):
        with pytest.raises(LLMError):
            LLMClient._unwrap(["not", "a", "dict"])


class TestLooseJson:
    def test_plain(self):
        assert _parse_loose_json('{"a": 1}') == {"a": 1}

    def test_fenced(self):
        assert _parse_loose_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_with_prose_around(self):
        assert _parse_loose_json('Kết quả:\n{"toxic": 2}\nHết.') == {"toxic": 2}

    def test_no_json_raises(self):
        with pytest.raises(LLMError):
            _parse_loose_json("không có json ở đây")


class TestPricing:
    def test_haiku_equivalent_model(self):
        # gpt-oss-20b: $0.0495 vào / $0.198 ra, tỷ giá 26.300
        assert cost_micro_vnd("gpt-oss-20b", 1_000_000, 0, 26_300) == int(0.0495 * 26_300 * 1e6)

    def test_unknown_model_uses_expensive_fallback(self):
        # Model lạ phải tính theo giá cao nhất, thà ước tính thừa còn hơn thiếu.
        assert cost_micro_vnd("model-la", 1_000_000, 0, 26_300) == cost_micro_vnd(
            "GLM-5.2", 1_000_000, 0, 26_300
        )

    def test_estimate_tokens_never_zero(self):
        assert estimate_tokens("") == 1
        assert estimate_tokens("xin chào các bạn") > 1
