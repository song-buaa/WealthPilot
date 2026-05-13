"""
StatusTracker - 索引状态管理器。

维护 _index/file_index.json 中每个文件的状态：
- indexed: 已成功索引，向量库可用
- pending: 文件已写入，等待索引（Embedding 暂时失败等）
- failed: 索引失败，需排查
- stale: 文件 hash 已变化，需重新索引
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.knowledge.schemas import FileStatus

logger = logging.getLogger(__name__)


class StatusTracker:
    """索引状态管理器。操作 _index/file_index.json。"""

    def __init__(self, index_file: Path):
        self._index_file = index_file
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        """从文件加载状态数据。"""
        if self._index_file.exists():
            try:
                raw = self._index_file.read_text(encoding="utf-8")
                self._data = json.loads(raw) if raw.strip() else {}
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"file_index.json 加载失败，重置为空: {e}")
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        """持久化状态到文件。"""
        self._index_file.parent.mkdir(parents=True, exist_ok=True)
        self._index_file.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def set_status(
        self,
        file_path: str,
        status: str,
        content_hash: Optional[str] = None,
        error_msg: Optional[str] = None,
        chunk_count: int = 0,
    ) -> None:
        """设置文件状态并持久化。"""
        existing = self._data.get(file_path, {})
        now_iso = datetime.now(timezone.utc).isoformat()

        entry = {
            "path": file_path,
            "content_hash": content_hash or existing.get("content_hash", ""),
            "status": status,
            "last_indexed_at": now_iso if status == "indexed" else existing.get("last_indexed_at", ""),
            "error_msg": error_msg,
            "chunk_count": chunk_count if status == "indexed" else existing.get("chunk_count", 0),
        }
        self._data[file_path] = entry
        self._save()

    def get_status(self, file_path: str) -> Optional[FileStatus]:
        """获取单个文件的状态。"""
        entry = self._data.get(file_path)
        if not entry:
            return None
        return FileStatus(**entry)

    def get_hash(self, file_path: str) -> Optional[str]:
        """获取文件的已记录 hash。"""
        entry = self._data.get(file_path)
        return entry.get("content_hash") if entry else None

    def remove(self, file_path: str) -> None:
        """移除文件记录并持久化。"""
        if file_path in self._data:
            del self._data[file_path]
            self._save()

    def list_all(self) -> dict[str, FileStatus]:
        """返回所有文件状态。"""
        return {
            k: FileStatus(**v) for k, v in self._data.items()
        }

    def list_failed(self) -> list[str]:
        """返回所有 failed 状态的文件路径。"""
        return [
            k for k, v in self._data.items()
            if v.get("status") == "failed"
        ]

    def list_stale(self) -> list[str]:
        """返回所有 stale 状态的文件路径。"""
        return [
            k for k, v in self._data.items()
            if v.get("status") == "stale"
        ]

    def list_indexed(self) -> list[str]:
        """返回所有 indexed 状态的文件路径。"""
        return [
            k for k, v in self._data.items()
            if v.get("status") == "indexed"
        ]

    @property
    def summary(self) -> dict[str, int]:
        """状态汇总统计。"""
        counts: dict[str, int] = {}
        for v in self._data.values():
            s = v.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
        return counts
