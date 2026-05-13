"""
KnowledgeStore - Chroma + OpenAI Embedding 封装。单例模式。

职责：
- 向量库的初始化、读写、删除
- Embedding 模型的调用
- 检索结果封装为 RetrievedChunk

设计原则：
- 失败抛 KnowledgeStoreError（由调用方决定阻塞/降级）
- 返回空列表是正常结果，不抛异常
- 所有配置来自 knowledge.yaml，不硬编码
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

from backend.knowledge.schemas import ChunkInput, RetrievedChunk

logger = logging.getLogger(__name__)


class KnowledgeStoreError(Exception):
    """知识库操作异常。"""
    pass


def _load_config() -> dict:
    """加载 knowledge.yaml 配置。"""
    import yaml
    config_path = Path(__file__).parent.parent / "config" / "knowledge.yaml"
    if not config_path.exists():
        return {}
    return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}


class KnowledgeStore:
    """向量库统一封装。单例。"""

    _instance: Optional["KnowledgeStore"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._config = _load_config()
        self._collection = None
        self._embedding_fn = None
        self._ready = False
        self._init_error: Optional[str] = None
        self._try_init()

    def _try_init(self) -> None:
        """尝试初始化 Chroma 和 Embedding。失败不抛异常，标记为 not ready。"""
        kb_config = self._config.get("knowledge_base", {})
        if not kb_config.get("enabled", True):
            self._init_error = "knowledge_base.enabled=false"
            return

        try:
            import chromadb
            from chromadb.config import Settings

            index_dir = kb_config.get("index_dir", "backend/knowledge_base/_index")
            chroma_path = Path(index_dir) / "chroma_db"
            chroma_path.mkdir(parents=True, exist_ok=True)

            self._client = chromadb.PersistentClient(
                path=str(chroma_path),
                settings=Settings(anonymized_telemetry=False),
            )

            collection_name = kb_config.get(
                "chroma_collection", "wealthpilot_knowledge"
            )

            embed_config = self._config.get("embedding", {})
            model_name = embed_config.get("model", "text-embedding-3-small")

            self._embedding_fn = self._build_embedding_fn(model_name)

            self._collection = self._client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )

            self._ready = True
            logger.info(
                f"[KnowledgeStore] 初始化成功: collection={collection_name}, "
                f"model={model_name}, existing_count={self._collection.count()}"
            )

        except Exception as e:
            self._init_error = str(e)
            logger.warning(f"[KnowledgeStore] 初始化失败（降级模式）: {e}")

    def _build_embedding_fn(self, model_name: str):
        """构建 OpenAI Embedding 调用函数。"""
        import openai

        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise KnowledgeStoreError("OPENAI_API_KEY 未设置")

        client = openai.OpenAI(api_key=api_key)
        embed_config = self._config.get("embedding", {})
        timeout = embed_config.get("timeout_seconds", 10)

        def embed(texts: list[str]) -> list[list[float]]:
            resp = client.embeddings.create(
                model=model_name,
                input=texts,
                timeout=timeout,
            )
            return [item.embedding for item in resp.data]

        return embed

    @classmethod
    def get_instance(cls) -> "KnowledgeStore":
        """获取单例。首次调用时初始化。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（测试用）。"""
        with cls._lock:
            cls._instance = None

    def is_ready(self) -> bool:
        """检查 Chroma 和 Embedding 是否可用。"""
        return self._ready

    def retrieve(
        self,
        query: str,
        source_types: Optional[list[str]] = None,
        top_k: int = 5,
        filters: Optional[dict[str, Any]] = None,
        apply_decay: bool = False,
    ) -> list[RetrievedChunk]:
        """
        语义检索。

        Args:
            query: 检索查询文本
            source_types: 过滤的 source_type 列表（None 表示不过滤）
            top_k: 最多返回条数
            filters: 额外元数据过滤（如 {"asset": "理想汽车"}）
            apply_decay: 是否启用时效衰减（MVP 默认 False）

        Returns:
            RetrievedChunk 列表，按 semantic_score 降序

        Raises:
            KnowledgeStoreError: Embedding 或 Chroma 调用失败
        """
        if not self._ready:
            return []

        if not query or not query.strip():
            return []

        try:
            query_embedding = self._embedding_fn([query])[0]
        except Exception as e:
            raise KnowledgeStoreError(f"Embedding 失败: {e}") from e

        # 构建 Chroma where 过滤
        where = self._build_where(source_types, filters)

        try:
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, self._collection.count() or top_k),
                where=where if where else None,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            raise KnowledgeStoreError(f"Chroma 查询失败: {e}") from e

        chunks = []
        if results and results["documents"] and results["documents"][0]:
            docs = results["documents"][0]
            metas = results["metadatas"][0] if results["metadatas"] else [{}] * len(docs)
            distances = results["distances"][0] if results["distances"] else [1.0] * len(docs)

            for doc, meta, dist in zip(docs, metas, distances):
                # Chroma cosine distance → similarity score
                score = max(0.0, 1.0 - dist)

                chunk = RetrievedChunk(
                    content=doc,
                    source_type=meta.get("source_type", ""),
                    source_channel="local_rag",
                    parent_doc_path=meta.get("parent_doc_path", ""),
                    chunk_index=meta.get("chunk_index", 0),
                    semantic_score=round(score, 4),
                    date=meta.get("date"),
                    time_sensitivity=meta.get("time_sensitivity"),
                    metadata={
                        k: v for k, v in meta.items()
                        if k not in ("source_type", "parent_doc_path",
                                     "chunk_index", "date", "time_sensitivity")
                    },
                )

                # MVP: decay 不启用，score 保持原值
                if apply_decay:
                    from backend.knowledge.decay import compute_decay_factor
                    decay_config = self._config.get("decay", {})
                    factor = compute_decay_factor(
                        chunk.time_sensitivity,
                        chunk.date,
                        half_lives=decay_config.get("half_life_months"),
                        enabled=decay_config.get("enabled", False),
                    )
                    chunk.semantic_score = round(chunk.semantic_score * factor, 4)

                chunks.append(chunk)

        chunks.sort(key=lambda c: -c.semantic_score)
        return chunks

    def add_chunks(self, chunks: list[ChunkInput]) -> int:
        """
        批量添加 chunks 到向量库。

        Args:
            chunks: ChunkInput 列表

        Returns:
            成功添加数量

        Raises:
            KnowledgeStoreError: Embedding 或 Chroma 写入失败
        """
        if not self._ready or not chunks:
            return 0

        texts = [c.content for c in chunks]
        ids = [
            self._make_chunk_id(c.parent_doc_path, c.chunk_index)
            for c in chunks
        ]
        metadatas = []
        for c in chunks:
            meta = {
                "source_type": c.source_type,
                "parent_doc_path": c.parent_doc_path,
                "chunk_index": c.chunk_index,
                **c.metadata,
            }
            # Chroma metadata 只支持 str/int/float/bool
            meta = {k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool))}
            metadatas.append(meta)

        try:
            embeddings = self._embedding_fn(texts)
        except Exception as e:
            raise KnowledgeStoreError(f"Embedding 批量生成失败: {e}") from e

        try:
            self._collection.upsert(
                ids=ids,
                documents=texts,
                embeddings=embeddings,
                metadatas=metadatas,
            )
            return len(chunks)
        except Exception as e:
            raise KnowledgeStoreError(f"Chroma 写入失败: {e}") from e

    def delete_by_parent_doc(self, parent_doc_path: str) -> int:
        """删除某 MD 文件对应的所有 chunks。返回删除数量。"""
        if not self._ready:
            return 0

        try:
            existing = self._collection.get(
                where={"parent_doc_path": parent_doc_path},
                include=[],
            )
            if existing and existing["ids"]:
                self._collection.delete(ids=existing["ids"])
                return len(existing["ids"])
            return 0
        except Exception as e:
            logger.warning(f"删除 chunks 失败 ({parent_doc_path}): {e}")
            return 0

    def count(self) -> int:
        """返回向量库中的 chunk 总数。"""
        if not self._ready:
            return 0
        return self._collection.count()

    @staticmethod
    def _make_chunk_id(parent_doc_path: str, chunk_index: int) -> str:
        """生成 chunk 的唯一 ID。"""
        raw = f"{parent_doc_path}::{chunk_index}"
        return hashlib.md5(raw.encode()).hexdigest()

    @staticmethod
    def _build_where(
        source_types: Optional[list[str]],
        filters: Optional[dict[str, Any]],
    ) -> Optional[dict]:
        """构建 Chroma where 过滤条件。"""
        conditions = []

        if source_types and len(source_types) == 1:
            conditions.append({"source_type": source_types[0]})
        elif source_types and len(source_types) > 1:
            conditions.append({"source_type": {"$in": source_types}})

        if filters:
            for k, v in filters.items():
                if isinstance(v, (str, int, float, bool)):
                    conditions.append({k: v})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}
