"""
Slug 生成工具 — 把中文标的名和标题转为文件系统友好的 ASCII slug。

用于投研观点 MD 文件的目录名和文件名生成。
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from pypinyin import lazy_pinyin, Style


_MAPPING_FILE = Path(__file__).parent / "asset_slug_mapping.json"

_slug_cache: dict[str, str] | None = None


def _load_mapping() -> dict[str, str]:
    global _slug_cache
    if _slug_cache is None:
        if _MAPPING_FILE.exists():
            _slug_cache = json.loads(_MAPPING_FILE.read_text(encoding="utf-8"))
        else:
            _slug_cache = {}
    return _slug_cache


def _save_mapping(mapping: dict[str, str]) -> None:
    global _slug_cache
    _slug_cache = mapping
    _MAPPING_FILE.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_asset_slug(asset_name: str) -> str:
    """
    获取标的的 ASCII slug（用作目录名）。

    优先从 asset_slug_mapping.json 查找已有映射，
    未命中时用拼音转换兜底并自动写回 JSON 积累映射。
    """
    if not asset_name:
        return "unknown"

    mapping = _load_mapping()

    # 精确匹配
    if asset_name in mapping:
        return mapping[asset_name]

    # 兜底：拼音转换
    slug = _to_ascii_slug(asset_name)
    mapping[asset_name] = slug
    _save_mapping(mapping)
    return slug


def generate_title_slug(title: str) -> str:
    """
    把任意标题转成 ASCII slug（用作文件名）。

    规则：
    1. 中文字符用拼音替换
    2. 空格/特殊字符替换成下划线
    3. 截断到 30 字符
    4. 全部小写
    """
    return _to_ascii_slug(title, max_len=30)


def _to_ascii_slug(text: str, max_len: int = 30) -> str:
    """通用拼音 slug 转换。"""
    if not text:
        return "untitled"

    # 分离中文和非中文字符，中文用拼音替换
    parts = []
    for char in text:
        if '\u4e00' <= char <= '\u9fff':
            py = lazy_pinyin(char, style=Style.NORMAL)
            parts.append(py[0] if py else char)
        elif char.isascii() and (char.isalnum() or char in " _-"):
            parts.append(char)
        else:
            parts.append(" ")

    raw = "".join(parts)

    # 标准化：小写 + 空格/_/- 统一为下划线 + 去重连续下划线
    slug = raw.lower().strip()
    slug = re.sub(r"[\s_-]+", "_", slug)
    slug = slug.strip("_")

    # 截断
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("_")

    return slug or "untitled"


def ensure_unique_path(file_path: Path) -> Path:
    """
    确保文件路径唯一。如已存在同名文件，自动追加序号后缀。

    例：2026-05-13_li_xiang_q1.md → 2026-05-13_li_xiang_q1_2.md
    """
    if not file_path.exists():
        return file_path

    stem = file_path.stem
    suffix = file_path.suffix
    parent = file_path.parent

    counter = 2
    while True:
        new_path = parent / f"{stem}_{counter}{suffix}"
        if not new_path.exists():
            return new_path
        counter += 1
