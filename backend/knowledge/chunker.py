"""
KnowledgeChunker - 多策略切片器（MVP 最小可用版）。

MVP 实现：
- 跳过 YAML frontmatter（已由 FrontmatterParser 处理）
- 跳过 <!-- RULES_CONFIG ... --> HTML 注释块
- 使用 RecursiveCharacterTextSplitter 做兜底切片

后续批次扩展：
- 按对话轮次切片（research_views）
- 按规则编号切片（investment_principles）
- 按 H2/H3 标题切片（allocation_principles）
"""
from __future__ import annotations

import re
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter


_HTML_JSON_PATTERN = re.compile(
    r"<!--\s*RULES_CONFIG\s*\n.*?\n\s*-->",
    re.DOTALL,
)


class KnowledgeChunker:
    """多策略切片器。MVP 版本使用统一的兜底切片。"""

    def __init__(
        self,
        chunk_size: int = 600,
        chunk_overlap: int = 100,
    ):
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n## ", "\n### ", "\n\n", "\n", "。", "，", " "],
            keep_separator=True,
        )

    def chunk(
        self,
        content: str,
        metadata: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        """
        对正文内容做切片。

        Args:
            content: frontmatter 已剥离的正文
            metadata: 来自 frontmatter 的元数据（source_type / date / tags 等）

        Returns:
            [(chunk_text, chunk_metadata), ...]
            chunk_metadata 继承 parent metadata 并追加 chunk_index。
        """
        # 跳过 HTML 注释 JSON 块
        clean = _HTML_JSON_PATTERN.sub("", content).strip()

        if not clean:
            return []

        texts = self._splitter.split_text(clean)

        results = []
        for i, text in enumerate(texts):
            text = text.strip()
            if not text:
                continue
            chunk_meta = {
                **metadata,
                "chunk_index": i,
            }
            results.append((text, chunk_meta))

        return results
