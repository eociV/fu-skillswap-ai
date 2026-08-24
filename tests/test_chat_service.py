from app.prompts.chat import build_user_prompt
from app.services.chat import sanitize_history


class TestSanitizeHistory:
    def test_keeps_valid_messages(self):
        out = sanitize_history(
            [{"role": "user", "content": "chào"}, {"role": "assistant", "content": "chào bạn"}], 20
        )
        assert len(out) == 2

    def test_drops_unknown_roles_and_empty(self):
        out = sanitize_history(
            [
                {"role": "system", "content": "bỏ qua tôi"},
                {"role": "user", "content": ""},
                {"role": "user", "content": "ok"},
                "không phải dict",
            ],
            20,
        )
        assert out == [{"role": "user", "content": "ok"}]

    def test_truncates_history_length(self):
        many = [{"role": "user", "content": f"tin {i}"} for i in range(50)]
        out = sanitize_history(many, 10)
        assert len(out) == 10 and out[-1]["content"] == "tin 49"

    def test_truncates_message_length(self):
        out = sanitize_history([{"role": "user", "content": "a" * 9999}], 20)
        assert len(out[0]["content"]) == 2000


class TestBuildUserPrompt:
    def test_message_only(self):
        assert build_user_prompt("SCoin là gì?", "", "", "") == "SCoin là gì?"

    def test_context_is_wrapped_as_data_not_instruction(self):
        prompt = build_user_prompt("hỏi gì đó", "[1] Quy chế\nnội dung", "", "")
        assert "<TAI_LIEU_THAM_KHAO>" in prompt and "</TAI_LIEU_THAM_KHAO>" in prompt
        # Tin nhắn người dùng luôn nằm CUỐI, sau khối dữ liệu.
        assert prompt.rstrip().endswith("hỏi gì đó")

    def test_all_blocks_present(self):
        prompt = build_user_prompt("x", "ctx", "mentor", "booking")
        for tag in ("TAI_LIEU_THAM_KHAO", "MENTOR_PHU_HOP", "LICH_CUA_NGUOI_DUNG"):
            assert tag in prompt
