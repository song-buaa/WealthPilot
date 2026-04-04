"""
Research API 路由 — 投研观点
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from typing import Optional

from backend.services import research_service as svc

router = APIRouter()


# ── 请求体模型 ────────────────────────────────────────────────────────────────

class ViewpointCreate(BaseModel):
    title: str
    object_type: str = "asset"
    object_name: Optional[str] = None
    market_name: Optional[str] = None
    topic_tags: Optional[list[str]] = None
    thesis: Optional[str] = None
    supporting_points: Optional[list[str]] = None
    opposing_points: Optional[list[str]] = None
    key_metrics: Optional[list[str]] = None
    risks: Optional[list[str]] = None
    action_suggestion: Optional[str] = None
    invalidation_conditions: Optional[str] = None
    horizon: Optional[str] = None
    stance: Optional[str] = None
    user_approval_level: str = "reference"
    validity_status: str = "active"


class ViewpointUpdate(BaseModel):
    title: Optional[str] = None
    object_type: Optional[str] = None
    object_name: Optional[str] = None
    market_name: Optional[str] = None
    topic_tags: Optional[list[str]] = None
    thesis: Optional[str] = None
    supporting_points: Optional[list[str]] = None
    opposing_points: Optional[list[str]] = None
    key_metrics: Optional[list[str]] = None
    risks: Optional[list[str]] = None
    action_suggestion: Optional[str] = None
    invalidation_conditions: Optional[str] = None
    horizon: Optional[str] = None
    stance: Optional[str] = None
    user_approval_level: Optional[str] = None
    validity_status: Optional[str] = None


class ParseTextRequest(BaseModel):
    content: str
    title: str = ""
    source_url: Optional[str] = None


class ParseUrlRequest(BaseModel):
    url: str


class ApproveCardRequest(BaseModel):
    overrides: Optional[dict] = None


# ── 观点库 ────────────────────────────────────────────────────────────────────

@router.get("/viewpoints")
def list_viewpoints(q: Optional[str] = Query(default=None, description="关键词检索")):
    """获取观点列表，可用 ?q=理想汽车 进行关键词检索"""
    return svc.list_viewpoints(query=q)


@router.post("/viewpoints", status_code=201)
def create_viewpoint(req: ViewpointCreate):
    """手动新增观点"""
    try:
        return svc.create_viewpoint(req.model_dump())
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.put("/viewpoints/{viewpoint_id}")
def update_viewpoint(viewpoint_id: int, req: ViewpointUpdate):
    """更新观点"""
    try:
        return svc.update_viewpoint(viewpoint_id, req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/viewpoints/{viewpoint_id}", status_code=204)
def delete_viewpoint(viewpoint_id: int):
    """删除观点"""
    try:
        svc.delete_viewpoint(viewpoint_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── 文档管理 ──────────────────────────────────────────────────────────────────

@router.get("/documents")
def list_documents():
    """获取文档列表（含解析状态）"""
    return svc.list_documents()


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: int):
    """删除文档及关联候选卡"""
    try:
        svc.delete_document(document_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── 候选观点卡 ────────────────────────────────────────────────────────────────

@router.get("/cards")
def list_cards():
    """获取所有候选观点卡"""
    return svc.list_cards()


@router.post("/cards/{card_id}/approve", status_code=201)
def approve_card(card_id: int, req: ApproveCardRequest):
    """将候选卡升级为正式观点"""
    try:
        return svc.approve_card(card_id, req.overrides)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))


# ── 内容解析 ──────────────────────────────────────────────────────────────────

@router.post("/parse/text", status_code=201)
def parse_text(req: ParseTextRequest):
    """
    解析纯文本/Markdown 内容，AI 提炼后生成候选观点卡。
    """
    try:
        return svc.parse_text(req.content, req.title, req.source_url)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/parse/url", status_code=201)
def parse_url(req: ParseUrlRequest):
    """
    抓取 URL 正文后 AI 解析。
    """
    try:
        return svc.parse_url(req.url)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/parse/pdf", status_code=201)
async def parse_pdf(file: UploadFile = File(...)):
    """
    上传 PDF 文件，提取文字后 AI 解析。
    Content-Type: multipart/form-data
    """
    data = await file.read()
    try:
        return svc.parse_pdf(data, file.filename or "upload.pdf")
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/documents/{document_id}/reparse", status_code=201)
def reparse_document(document_id: int):
    """重新解析已存档文档"""
    try:
        return svc.reparse_document(document_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
