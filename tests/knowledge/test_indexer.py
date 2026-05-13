"""
KnowledgeIndexer 单元测试。

测试索引管理器的核心逻辑：文件发现、hash 对比、状态管理。
不依赖真实 Embedding（使用 mock store）。
"""
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.knowledge.chunker import KnowledgeChunker
from backend.knowledge.indexer import KnowledgeIndexer
from backend.knowledge.status_tracker import StatusTracker


@pytest.fixture
def temp_kb():
    """创建临时 knowledge_base 目录结构。"""
    root = Path(tempfile.mkdtemp()) / "knowledge_base"
    root.mkdir()
    (root / "_index").mkdir()
    (root / "allocation_principles").mkdir()
    yield root
    shutil.rmtree(root.parent, ignore_errors=True)


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.is_ready.return_value = True
    store.add_chunks.return_value = 3
    store.delete_by_parent_doc.return_value = 0
    return store


@pytest.fixture
def indexer(temp_kb, mock_store):
    tracker = StatusTracker(temp_kb / "_index" / "file_index.json")
    chunker = KnowledgeChunker(chunk_size=200, chunk_overlap=20)
    return KnowledgeIndexer(
        knowledge_root=temp_kb,
        store=mock_store,
        chunker=chunker,
        status_tracker=tracker,
    )


def _write_md(kb_root: Path, subdir: str, filename: str, content: str) -> Path:
    d = kb_root / subdir
    d.mkdir(exist_ok=True)
    f = d / filename
    f.write_text(content, encoding="utf-8")
    return f


class TestFileDiscovery:
    def test_discovers_md_files(self, temp_kb, indexer):
        _write_md(temp_kb, "allocation_principles", "test.md",
                   "---\nsource: test\n---\n\n# Test\n\nContent here.")
        report = indexer.full_scan_and_sync()
        assert len(report.added_files) == 1

    def test_skips_underscore_dirs(self, temp_kb, indexer):
        _write_md(temp_kb, "_index", "should_skip.md", "# Skip")
        report = indexer.full_scan_and_sync()
        assert len(report.added_files) == 0

    def test_skips_underscore_files(self, temp_kb, indexer):
        _write_md(temp_kb, "allocation_principles", "_template.md", "# Template")
        report = indexer.full_scan_and_sync()
        assert len(report.added_files) == 0


class TestIncrementalSync:
    def test_new_file_added(self, temp_kb, indexer, mock_store):
        _write_md(temp_kb, "allocation_principles", "new.md",
                   "---\nsource: test\n---\n\n# New\n\nNew content.")
        report = indexer.full_scan_and_sync()
        assert "new.md" in report.added_files[0]
        mock_store.add_chunks.assert_called()

    def test_unchanged_file_skipped(self, temp_kb, indexer, mock_store):
        _write_md(temp_kb, "allocation_principles", "stable.md",
                   "---\nsource: test\n---\n\n# Stable")
        indexer.full_scan_and_sync()
        mock_store.add_chunks.reset_mock()

        # 再次扫描，文件未变
        report = indexer.full_scan_and_sync()
        assert len(report.added_files) == 0
        assert len(report.updated_files) == 0
        mock_store.add_chunks.assert_not_called()

    def test_modified_file_updated(self, temp_kb, indexer, mock_store):
        f = _write_md(temp_kb, "allocation_principles", "modify.md",
                       "---\nsource: test\n---\n\n# V1")
        indexer.full_scan_and_sync()
        mock_store.add_chunks.reset_mock()

        # 修改文件
        f.write_text("---\nsource: test\n---\n\n# V2 Updated", encoding="utf-8")
        report = indexer.full_scan_and_sync()
        assert len(report.updated_files) == 1
        mock_store.delete_by_parent_doc.assert_called()
        mock_store.add_chunks.assert_called()

    def test_deleted_file_cleaned(self, temp_kb, indexer, mock_store):
        f = _write_md(temp_kb, "allocation_principles", "to_delete.md",
                       "---\nsource: test\n---\n\n# Delete me")
        indexer.full_scan_and_sync()

        # 删除文件
        f.unlink()
        report = indexer.full_scan_and_sync()
        assert len(report.deleted_files) == 1
        mock_store.delete_by_parent_doc.assert_called()


class TestOnFileWrite:
    def test_incremental_index(self, temp_kb, indexer, mock_store):
        f = _write_md(temp_kb, "allocation_principles", "written.md",
                       "---\nsource: test\n---\n\n# Written")
        result = indexer.on_file_write(f)
        assert result is True
        mock_store.add_chunks.assert_called()

    def test_failure_marks_failed(self, temp_kb, indexer, mock_store):
        from backend.knowledge.store import KnowledgeStoreError
        mock_store.add_chunks.side_effect = KnowledgeStoreError("API down")
        f = _write_md(temp_kb, "allocation_principles", "fail.md",
                       "---\nsource: test\n---\n\n# Fail")
        result = indexer.on_file_write(f)
        assert result is False
