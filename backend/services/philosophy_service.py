"""
Philosophy Service — 投资理念文档管理

管理 knowledge_base/investment_style/investment_philosophy.md 的读写。
参照 discipline_service.py 的手册管理模式。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PHILOSOPHY_FILE = Path("knowledge_base/investment_style/investment_philosophy.md")

# 内置默认内容（用于 reset 恢复）
_DEFAULT_CONTENT: Optional[str] = None


def _get_default_content() -> str:
    """读取当前文件内容作为默认内容（首次调用时缓存）。"""
    global _DEFAULT_CONTENT
    if _DEFAULT_CONTENT is None:
        if _PHILOSOPHY_FILE.exists():
            _DEFAULT_CONTENT = _PHILOSOPHY_FILE.read_text(encoding="utf-8")
        else:
            _DEFAULT_CONTENT = "---\nsource: 自己\ndate: 2026-05-13\ntime_sensitivity: permanent\ntags: [投资理念]\n---\n\n# 投资理念\n\n[在这里填写]\n"
    return _DEFAULT_CONTENT


def get_philosophy() -> dict:
    """返回投资理念文档内容。"""
    if _PHILOSOPHY_FILE.exists():
        content = _PHILOSOPHY_FILE.read_text(encoding="utf-8")
        return {"source": "current", "content": content}
    return {"source": "default", "content": _get_default_content()}


def save_philosophy(content: str) -> None:
    """保存投资理念文档（保留 YAML frontmatter 不变，或完整覆盖）。"""
    _PHILOSOPHY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PHILOSOPHY_FILE.write_text(content, encoding="utf-8")
    logger.info(f"投资理念文档已保存: {_PHILOSOPHY_FILE}")

    # 触发知识库重新索引
    _trigger_reindex()


def reset_philosophy() -> dict:
    """恢复为默认投资理念内容。"""
    default = _get_default_content()
    _PHILOSOPHY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PHILOSOPHY_FILE.write_text(default, encoding="utf-8")
    logger.info(f"投资理念文档已恢复默认: {_PHILOSOPHY_FILE}")

    _trigger_reindex()
    return {"source": "default", "content": default}


def _trigger_reindex() -> None:
    """触发知识库增量索引（失败不阻塞）。"""
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
