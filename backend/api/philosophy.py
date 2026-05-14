"""
Philosophy API — 投资理念文档端点。

参照 discipline.py 的手册管理 API 设计。
"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from backend.services import philosophy_service as svc

router = APIRouter()


@router.get("")
def get_philosophy():
    """获取投资理念文档内容。"""
    return svc.get_philosophy()


@router.post("")
async def upload_philosophy(file: UploadFile = File(...)):
    """上传投资理念文档（Markdown 文件）。"""
    data = await file.read()
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        content = data.decode("gbk", errors="replace")

    if not content.strip():
        raise HTTPException(status_code=422, detail="文件内容为空")

    svc.save_philosophy(content)
    return svc.get_philosophy()


@router.delete("")
def reset_philosophy():
    """恢复默认投资理念文档。"""
    return svc.reset_philosophy()
