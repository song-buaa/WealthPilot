"""
Knowledge API — 知识库文件访问端点。

v3.6.1 新增：/api/knowledge/file 提供 MD 文件内容预览。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from backend.knowledge.frontmatter import parse

router = APIRouter()

# 知识库根目录（相对于项目根目录）
_KNOWLEDGE_ROOT = Path(__file__).parent.parent.parent / "knowledge_base"


@router.get("/file")
async def get_knowledge_file(path: str = Query(..., description="相对于 knowledge_base/ 的路径")):
    """
    返回知识库 MD 文件的 frontmatter 元数据 + 正文内容。

    安全限制：
    - 只允许读取 knowledge_base/ 目录下的文件
    - 不允许路径穿越（..）
    - 仅允许 .md 文件
    """
    # 路径安全校验
    if ".." in path:
        raise HTTPException(status_code=403, detail="路径不合法")

    # 处理后端返回的路径格式（可能包含 knowledge_base/ 前缀或 backend/knowledge_base/ 前缀）
    clean_path = path
    if clean_path.startswith("backend/knowledge_base/"):
        clean_path = clean_path[len("backend/knowledge_base/"):]
    elif clean_path.startswith("knowledge_base/"):
        clean_path = clean_path[len("knowledge_base/"):]

    target = (_KNOWLEDGE_ROOT / clean_path).resolve()
    root_resolved = _KNOWLEDGE_ROOT.resolve()

    if not str(target).startswith(str(root_resolved)):
        raise HTTPException(status_code=403, detail="路径不合法")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    if not target.suffix == ".md":
        raise HTTPException(status_code=403, detail="只允许读取 .md 文件")

    frontmatter, content = parse(target)

    return {
        "path": clean_path,
        "frontmatter": frontmatter,
        "content": content,
    }
