"""
Philosophy Service — 投资理念文档管理

管理 knowledge_base/investment_style/investment_philosophy.md 的读写。
参照 discipline_service.py 的手册管理模式。

防复发设计（v3.8 修复）：
- GET 读不到文件时返回占位符但不写盘（"读"和"写默认值"解耦）
- reset（DELETE）覆盖前先备份当前内容到 .bak
- reindex 不索引占位符内容
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_PHILOSOPHY_FILE = Path("knowledge_base/investment_style/investment_philosophy.md")
_BACKUP_DIR = Path("knowledge_base/investment_style/_backups")

# 硬编码占位符（仅用于 GET fallback 和 reset，不会被自动持久化）
_PLACEHOLDER = (
    "---\n"
    "source: 自己\n"
    "date: 2026-05-13\n"
    "time_sensitivity: permanent\n"
    "tags: [投资理念]\n"
    "---\n\n"
    "# 投资理念\n\n"
    "[在这里填写]\n"
)

_PLACEHOLDER_MARKER = "[在这里填写]"


def get_philosophy() -> dict:
    """返回投资理念文档内容。文件不存在时返回占位符但不写盘。"""
    if _PHILOSOPHY_FILE.exists():
        content = _PHILOSOPHY_FILE.read_text(encoding="utf-8")
        return {"source": "current", "content": content}
    return {"source": "default", "content": _PLACEHOLDER}


def save_philosophy(content: str) -> None:
    """保存投资理念文档。"""
    _PHILOSOPHY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PHILOSOPHY_FILE.write_text(content, encoding="utf-8")
    logger.info(f"投资理念文档已保存: {_PHILOSOPHY_FILE}")

    _trigger_reindex()


def reset_philosophy() -> dict:
    """恢复为占位符内容。覆盖前先备份当前磁盘内容。"""
    _backup_current()
    _PHILOSOPHY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PHILOSOPHY_FILE.write_text(_PLACEHOLDER, encoding="utf-8")
    logger.info(f"投资理念文档已恢复默认: {_PHILOSOPHY_FILE}")

    _trigger_reindex()
    return {"source": "default", "content": _PLACEHOLDER}


def _backup_current() -> None:
    """把当前磁盘文件备份到 _backups/ 目录（带时间戳），内容为占位符时跳过。"""
    if not _PHILOSOPHY_FILE.exists():
        return
    content = _PHILOSOPHY_FILE.read_text(encoding="utf-8")
    if _PLACEHOLDER_MARKER in content:
        logger.info("当前内容为占位符，跳过备份")
        return
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = _BACKUP_DIR / f"investment_philosophy_{ts}.bak"
    bak_path.write_text(content, encoding="utf-8")
    logger.info(f"投资理念已备份: {bak_path}")


def _trigger_reindex() -> None:
    """触发知识库增量索引（失败不阻塞）。占位符内容不索引。"""
    if _PHILOSOPHY_FILE.exists():
        content = _PHILOSOPHY_FILE.read_text(encoding="utf-8")
        if _PLACEHOLDER_MARKER in content:
            logger.info("投资理念为占位符，跳过索引")
            return
    try:
        from backend.knowledge.store import KnowledgeStore
        store = KnowledgeStore.get_instance()
        if store.is_ready():
            from backend.knowledge.indexer import KnowledgeIndexer
            from backend.knowledge.chunker import KnowledgeChunker
            from backend.knowledge.status_tracker import StatusTracker

            kb_root = Path("knowledge_base")
            tracker = StatusTracker(kb_root / "_index" / "file_index.json")
            indexer = KnowledgeIndexer(kb_root, store, KnowledgeChunker(), tracker)
            indexer.on_file_write(_PHILOSOPHY_FILE)
            logger.info("投资理念索引更新成功")
    except Exception as e:
        logger.warning(f"投资理念索引更新失败（不阻塞）: {e}")
