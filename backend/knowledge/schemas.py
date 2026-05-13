"""
WealthPilot v3.6 Knowledge Layer Schemas.

知识检索的输入/输出数据契约。KnowledgeStore / KnowledgeIndexer /
KnowledgeChunker 以及未来的 wp-retrieve-principles Skill 共用。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ════════════════════════════════════════════════════════════
# 类型枚举
# ════════════════════════════════════════════════════════════

SourceType = Literal[
    "research_views",
    "investment_principles",
    "investment_style",
    "allocation_principles",
]

SourceChannel = Literal[
    "local_rag",
    "research_card",
    "web",
    "mcp",
    "local_principles",
]

IndexStatus = Literal["indexed", "pending", "failed", "stale"]


# ════════════════════════════════════════════════════════════
# 写入端契约
# ════════════════════════════════════════════════════════════

class ChunkInput(BaseModel):
    """写入向量库时的 chunk 输入。"""
    content: str = Field(..., description="chunk 正文")
    source_type: str = Field(..., description="知识类型")
    parent_doc_path: str = Field(..., description="来源 MD 文件相对路径")
    chunk_index: int = Field(default=0, description="在父文档中的序号")
    metadata: dict[str, Any] = Field(default_factory=dict)


# ════════════════════════════════════════════════════════════
# 读取端契约
# ════════════════════════════════════════════════════════════

class RetrievedChunk(BaseModel):
    """从知识库召回的单个知识片段。所有检索类 Skill 共用。"""

    # 内容
    content: str = Field(..., description="chunk 正文")

    # 分类
    source_type: str = Field(..., description="知识内容的语义类别")
    source_channel: str = Field(
        default="local_rag", description="数据来源通道"
    )

    # 溯源
    parent_doc_path: str = Field(..., description="来源 MD 文件相对路径")
    chunk_index: int = Field(default=0, description="在父文档中的序号")

    # 打分
    semantic_score: float = Field(..., description="语义相似度 0-1")

    # 时效（MVP 存而不用）
    date: Optional[str] = Field(default=None, description="ISO 8601")
    time_sensitivity: Optional[str] = Field(default=None)

    # 扩展元数据
    metadata: dict[str, Any] = Field(default_factory=dict)


# ════════════════════════════════════════════════════════════
# 文件索引状态
# ════════════════════════════════════════════════════════════

class FileStatus(BaseModel):
    """file_index.json 中每个文件的状态记录。"""
    path: str
    content_hash: str
    status: str = "pending"  # indexed / pending / failed / stale
    last_indexed_at: str = ""  # ISO 8601
    error_msg: Optional[str] = None
    chunk_count: int = 0


# ════════════════════════════════════════════════════════════
# 同步报告
# ════════════════════════════════════════════════════════════

class SyncReport(BaseModel):
    """KnowledgeIndexer.full_scan_and_sync() 的返回结果。"""
    added_files: list[str] = Field(default_factory=list)
    updated_files: list[str] = Field(default_factory=list)
    deleted_files: list[str] = Field(default_factory=list)
    failed_files: list[tuple[str, str]] = Field(default_factory=list)
    total_chunks: int = 0
    duration_ms: int = 0
