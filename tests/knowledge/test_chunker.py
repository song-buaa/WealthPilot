"""KnowledgeChunker 单元测试。"""
import pytest
from backend.knowledge.chunker import KnowledgeChunker


@pytest.fixture
def chunker():
    return KnowledgeChunker(chunk_size=200, chunk_overlap=20)


class TestChunker:
    def test_basic_split(self, chunker):
        content = "这是第一段内容。" * 30 + "\n\n" + "这是第二段内容。" * 30
        results = chunker.chunk(content, {"source_type": "allocation_principles"})
        assert len(results) >= 2
        for text, meta in results:
            assert len(text) > 0
            assert "chunk_index" in meta
            assert meta["source_type"] == "allocation_principles"

    def test_chunk_index_sequential(self, chunker):
        content = "长文本内容。" * 100
        results = chunker.chunk(content, {"source_type": "test"})
        indices = [meta["chunk_index"] for _, meta in results]
        assert indices == list(range(len(indices)))

    def test_html_json_stripped(self, chunker):
        content = """<!-- RULES_CONFIG
{"max_position_pct": 0.40}
-->

这是正文内容，应该被切片。""" + "更多内容。" * 30
        results = chunker.chunk(content, {"source_type": "investment_principles"})
        for text, _ in results:
            assert "RULES_CONFIG" not in text
            assert "max_position_pct" not in text

    def test_empty_content(self, chunker):
        results = chunker.chunk("", {"source_type": "test"})
        assert results == []

    def test_metadata_inherited(self, chunker):
        content = "内容。" * 50
        metadata = {
            "source_type": "allocation_principles",
            "date": "2026-05-13",
            "time_sensitivity": "permanent",
        }
        results = chunker.chunk(content, metadata)
        assert len(results) >= 1
        _, meta = results[0]
        assert meta["date"] == "2026-05-13"
        assert meta["time_sensitivity"] == "permanent"
        assert meta["source_type"] == "allocation_principles"

    def test_short_content_single_chunk(self, chunker):
        content = "短内容。"
        results = chunker.chunk(content, {"source_type": "investment_style"})
        assert len(results) == 1
        text, meta = results[0]
        assert text == "短内容。"
        assert meta["chunk_index"] == 0
