from app.services.ingest import MIN_CHARS, TARGET_CHARS, detect_kind, extract_text, split_chunks
from app.services.ingest import UnsupportedDocument
import pytest


class TestSplitChunks:
    def test_empty(self):
        assert split_chunks("") == []
        assert split_chunks("   \n\n  ") == []

    def test_short_document_becomes_one_chunk(self):
        chunks = split_chunks("Sinh viên FPT học môn PRJ301 vào kỳ 5.")
        assert len(chunks) == 1
        assert "PRJ301" in chunks[0][1]

    def test_heading_is_captured(self):
        text = "# Quy chế thi\n\n" + ("Nội dung quy chế thi cử. " * 20)
        chunks = split_chunks(text)
        assert chunks[0][0] == "Quy chế thi"

    def test_long_document_splits_and_respects_target(self):
        text = "\n\n".join(f"Đoạn số {i}. " * 40 for i in range(30))
        chunks = split_chunks(text)
        assert len(chunks) > 1
        # Cho phép vượt một đoạn văn vì chỉ cắt ở ranh giới đoạn.
        assert all(len(body) < TARGET_CHARS * 2 for _, body in chunks)

    def test_chunks_overlap_for_context_continuity(self):
        text = "\n\n".join(f"Câu {i} nói về nội dung học thuật quan trọng. " * 15 for i in range(10))
        chunks = split_chunks(text)
        assert len(chunks) >= 2
        tail = chunks[0][1][-100:]
        assert any(tail[:40] in body for _, body in chunks[1:]), "đoạn sau phải chồng lấn đoạn trước"

    def test_vietnamese_diacritics_preserved(self):
        text = "Đại học FPT — kỳ thi chuẩn đầu ra tiếng Anh. " * 20
        assert "chuẩn đầu ra" in split_chunks(text)[0][1]


class TestDetectKind:
    def test_by_content_type(self):
        assert detect_kind("a.bin", "application/pdf") == "pdf"

    def test_by_extension_fallback(self):
        assert detect_kind("quy-che.docx", None) == "docx"
        assert detect_kind("note.md", "application/octet-stream") == "text"

    def test_unsupported(self):
        with pytest.raises(UnsupportedDocument):
            detect_kind("anh.png", "image/png")


class TestExtractText:
    def test_utf8_text(self):
        assert extract_text("Xin chào".encode(), "text") == "Xin chào"

    def test_broken_bytes_do_not_crash(self):
        assert extract_text(b"\xff\xfe abc", "text")
