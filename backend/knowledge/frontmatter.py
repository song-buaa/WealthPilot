"""
双格式 Frontmatter 解析器。

支持两种格式：
1. YAML frontmatter（--- 分隔）：v3.6 新增知识文件的标准格式
2. HTML 注释 JSON（<!-- RULES_CONFIG ... -->）：投资纪律手册的现有格式（跳过不解析为 frontmatter）

对于 HTML 注释 JSON，本解析器只负责"识别并跳过"——
真正的 RULES_CONFIG 解析由 app/discipline/handbook_parser.py 处理。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import frontmatter


def parse(file_path: Path) -> tuple[dict, str]:
    """
    解析 MD 文件，返回 (frontmatter_dict, content_body)。

    自动识别格式：
    - YAML frontmatter：文件以 --- 开头 → 解析 YAML 部分
    - HTML 注释 JSON：文件含 <!-- RULES_CONFIG ... --> → frontmatter 为空 dict，
      正文为去掉 HTML 注释后的内容
    - 都没有：frontmatter 为空 dict，正文为整个文件

    Args:
        file_path: MD 文件的绝对或相对路径

    Returns:
        (frontmatter_dict, content_body)
    """
    raw = Path(file_path).read_text(encoding="utf-8")

    # 尝试 YAML frontmatter（python-frontmatter 库）
    if raw.lstrip().startswith("---"):
        post = frontmatter.loads(raw)
        return dict(post.metadata), post.content.strip()

    # 检查 HTML 注释 JSON（投资纪律手册格式）
    body = _strip_html_json_block(raw)
    return {}, body.strip()


def parse_from_text(text: str) -> tuple[dict, str]:
    """
    从字符串解析 frontmatter（不依赖文件路径）。

    用于测试和内存中处理。
    """
    if text.lstrip().startswith("---"):
        post = frontmatter.loads(text)
        return dict(post.metadata), post.content.strip()

    body = _strip_html_json_block(text)
    return {}, body.strip()


_HTML_JSON_PATTERN = re.compile(
    r"<!--\s*RULES_CONFIG\s*\n(.*?)\n\s*-->",
    re.DOTALL,
)


def _strip_html_json_block(text: str) -> str:
    """移除 <!-- RULES_CONFIG ... --> 块，返回干净的正文。"""
    return _HTML_JSON_PATTERN.sub("", text)


def infer_source_type(file_path: Path, fm: dict) -> str:
    """
    从文件路径或 frontmatter 推断 source_type。

    优先使用 frontmatter 中显式声明的 source_type，
    否则从目录结构推断。

    Args:
        file_path: 文件路径（相对于 knowledge_base/）
        fm: 已解析的 frontmatter dict

    Returns:
        source_type 字符串
    """
    if "source_type" in fm:
        return fm["source_type"]

    path_str = str(file_path)
    if "allocation_principles" in path_str:
        return "allocation_principles"
    if "investment_principles" in path_str:
        return "investment_principles"
    if "investment_style" in path_str:
        return "investment_style"
    if "research_views" in path_str:
        return "research_views"

    return "allocation_principles"  # 安全默认值
