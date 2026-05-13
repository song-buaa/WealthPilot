"""
KnowledgeIndexer - 增量索引管理器。

维护 MD 文件 <-> Chroma 索引的同步。

三重触发：
1. 启动时全量扫描（full_scan_and_sync）
2. 写文件后触发（on_file_write）
3. API 手动按钮（调用 full_scan_and_sync）
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

from backend.knowledge.chunker import KnowledgeChunker
from backend.knowledge.frontmatter import parse, infer_source_type
from backend.knowledge.schemas import ChunkInput, SyncReport
from backend.knowledge.status_tracker import StatusTracker
from backend.knowledge.store import KnowledgeStore, KnowledgeStoreError

logger = logging.getLogger(__name__)


class KnowledgeIndexer:
    """增量索引管理器。"""

    def __init__(
        self,
        knowledge_root: Path,
        store: KnowledgeStore,
        chunker: KnowledgeChunker,
        status_tracker: StatusTracker,
    ):
        self._root = knowledge_root
        self._store = store
        self._chunker = chunker
        self._tracker = status_tracker

    def full_scan_and_sync(self) -> SyncReport:
        """
        全量扫描同步。

        逻辑：
        1. 遍历 knowledge_base/ 下所有 .md（跳过 _ 开头的目录和文件）
        2. 计算每个文件的 content_hash
        3. 对比 file_index.json：
           - 新文件：process_file() + status=indexed
           - hash 变化：delete + process_file() + status=indexed
           - 文件缺失：delete_by_doc() + 移除记录
        4. 写回 file_index.json
        """
        start = time.time()
        report = SyncReport()

        # 发现所有 MD 文件
        md_files = self._discover_md_files()
        current_paths = {str(self._relative_path(f)) for f in md_files}

        # 检查已记录但已缺失的文件
        all_tracked = self._tracker.list_all()
        for tracked_path in list(all_tracked.keys()):
            if tracked_path not in current_paths:
                self._store.delete_by_parent_doc(tracked_path)
                self._tracker.remove(tracked_path)
                report.deleted_files.append(tracked_path)
                logger.info(f"[Indexer] 删除缺失文件索引: {tracked_path}")

        # 处理每个 MD 文件
        total_chunks = 0
        for md_file in md_files:
            rel_path = str(self._relative_path(md_file))
            content = md_file.read_text(encoding="utf-8")
            current_hash = self._compute_hash(content)
            stored_hash = self._tracker.get_hash(rel_path)

            if stored_hash == current_hash:
                # 无变化，跳过
                status = self._tracker.get_status(rel_path)
                if status:
                    total_chunks += status.chunk_count
                continue

            is_update = stored_hash is not None

            try:
                if is_update:
                    self._store.delete_by_parent_doc(rel_path)

                chunk_count = self._process_file_internal(
                    md_file, rel_path, content, current_hash
                )
                total_chunks += chunk_count

                if is_update:
                    report.updated_files.append(rel_path)
                else:
                    report.added_files.append(rel_path)

            except Exception as e:
                error_msg = str(e)
                self._tracker.set_status(
                    rel_path, "failed",
                    content_hash=current_hash,
                    error_msg=error_msg,
                )
                report.failed_files.append((rel_path, error_msg))
                logger.error(f"[Indexer] 文件处理失败: {rel_path}: {e}")

        # 加上未变化文件的 chunk 数
        report.total_chunks = total_chunks
        report.duration_ms = int((time.time() - start) * 1000)

        logger.info(
            f"[Indexer] 全量同步完成: "
            f"added={len(report.added_files)} "
            f"updated={len(report.updated_files)} "
            f"deleted={len(report.deleted_files)} "
            f"failed={len(report.failed_files)} "
            f"chunks={report.total_chunks} "
            f"duration={report.duration_ms}ms"
        )
        return report

    def on_file_write(self, file_path: Path) -> bool:
        """
        写文件后触发的增量同步（单文件）。

        Returns:
            True 表示索引成功，False 表示失败（已记录状态）
        """
        rel_path = str(self._relative_path(file_path))
        content = file_path.read_text(encoding="utf-8")
        current_hash = self._compute_hash(content)

        # 先删除旧的 chunks
        self._store.delete_by_parent_doc(rel_path)

        try:
            self._process_file_internal(
                file_path, rel_path, content, current_hash
            )
            return True
        except Exception as e:
            self._tracker.set_status(
                rel_path, "failed",
                content_hash=current_hash,
                error_msg=str(e),
            )
            logger.error(f"[Indexer] 增量索引失败: {rel_path}: {e}")
            return False

    def process_file(self, file_path: Path) -> int:
        """
        处理单个 MD 文件的完整流程。

        Returns:
            生成的 chunk 数量
        """
        rel_path = str(self._relative_path(file_path))
        content = file_path.read_text(encoding="utf-8")
        current_hash = self._compute_hash(content)
        return self._process_file_internal(
            file_path, rel_path, content, current_hash
        )

    def get_index_status(self) -> dict:
        """查询全量索引状态。"""
        return {
            "summary": self._tracker.summary,
            "failed": self._tracker.list_failed(),
            "stale": self._tracker.list_stale(),
        }

    # ── 内部方法 ──────────────────────────────────────────────

    def _process_file_internal(
        self,
        file_path: Path,
        rel_path: str,
        content: str,
        content_hash: str,
    ) -> int:
        """
        处理单文件：解析 → 切片 → 入库 → 更新状态。

        Raises:
            KnowledgeStoreError: Embedding 或 Chroma 写入失败
        """
        # 1. 解析 frontmatter
        fm, body = parse(file_path)

        # 2. 推断 source_type
        source_type = infer_source_type(file_path, fm)

        # 3. 构建 chunk 元数据
        metadata = {
            "source_type": source_type,
            "parent_doc_path": rel_path,
            "date": fm.get("date", ""),
            "time_sensitivity": fm.get("time_sensitivity", ""),
            "source": fm.get("source", ""),
        }
        # 将 date 转为字符串
        if metadata["date"] and not isinstance(metadata["date"], str):
            metadata["date"] = str(metadata["date"])
        # tags 需要序列化（Chroma 不支持 list 类型 metadata）
        tags = fm.get("tags", [])
        if tags:
            metadata["tags"] = ",".join(str(t) for t in tags)

        # 4. 切片
        chunk_pairs = self._chunker.chunk(body, metadata)
        if not chunk_pairs:
            self._tracker.set_status(
                rel_path, "indexed",
                content_hash=content_hash,
                chunk_count=0,
            )
            return 0

        # 5. 构造 ChunkInput
        chunk_inputs = []
        for text, meta in chunk_pairs:
            chunk_inputs.append(ChunkInput(
                content=text,
                source_type=source_type,
                parent_doc_path=rel_path,
                chunk_index=meta.get("chunk_index", 0),
                metadata={k: v for k, v in meta.items()
                          if k not in ("source_type", "parent_doc_path", "chunk_index")},
            ))

        # 6. 写入向量库
        added = self._store.add_chunks(chunk_inputs)

        # 7. 更新状态
        self._tracker.set_status(
            rel_path, "indexed",
            content_hash=content_hash,
            chunk_count=added,
        )

        logger.info(f"[Indexer] 索引完成: {rel_path} → {added} chunks")
        return added

    def _discover_md_files(self) -> list[Path]:
        """发现 knowledge_base/ 下所有 .md 文件。

        跳过规则：
        - _ 开头的目录（如 _index/）和文件（如 _template.md）
        - 根目录下的文件（如 README.md），只索引子目录中的文件
        """
        md_files = []
        for path in self._root.rglob("*.md"):
            rel = path.relative_to(self._root)
            parts = rel.parts
            # 跳过 _ 开头的目录或文件
            if any(p.startswith("_") for p in parts):
                continue
            # 跳过根目录下的文件（必须在子目录中）
            if len(parts) < 2:
                continue
            md_files.append(path)
        return sorted(md_files)

    def _relative_path(self, file_path: Path) -> Path:
        """计算相对于项目根目录的路径。"""
        try:
            return file_path.relative_to(self._root.parent.parent)
        except ValueError:
            return file_path

    @staticmethod
    def _compute_hash(content: str) -> str:
        """计算内容的 MD5 hash。"""
        return hashlib.md5(content.encode("utf-8")).hexdigest()
