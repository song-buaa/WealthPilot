"""
Research API 路由 — 投研观点
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from fastapi.responses import JSONResponse
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


class DocumentUpdateRequest(BaseModel):
    title: Optional[str] = None
    object_name: Optional[str] = None
    market_name: Optional[str] = None
    author: Optional[str] = None
    publish_time: Optional[str] = None


@router.patch("/documents/{document_id}")
def update_document(document_id: int, req: DocumentUpdateRequest):
    """更新文档基本信息"""
    try:
        return svc.update_document(document_id, req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


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


# ══════════════════════════════════════════════════════════════════
# v2 Endpoints（路径前缀 /v2/，与 v1 不冲突）
# ══════════════════════════════════════════════════════════════════


class V2IngestUploadRequest(BaseModel):
    title: str
    content: str
    source_url: Optional[str] = None


class V2IngestAVRequest(BaseModel):
    symbol: str


class V2JudgmentUpdateRequest(BaseModel):
    judgment: dict = {}
    confirm: bool = False
    action: Optional[str] = None  # "confirm" | "unconfirm" | "modify" | "discard" | "restore"


@router.post("/v2/ingest/upload", status_code=201)
def v2_ingest_upload(req: V2IngestUploadRequest):
    """用户上传内容 → v2 ViewpointCard。"""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="content 不能为空")
    try:
        return svc.v2_ingest_upload(req.title, req.content, req.source_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v2/ingest/alpha_vantage", status_code=201)
def v2_ingest_alpha_vantage(req: V2IngestAVRequest):
    """触发 Alpha Vantage 拉取。"""
    if not req.symbol.strip():
        raise HTTPException(status_code=400, detail="symbol 不能为空")
    try:
        result = svc.v2_ingest_alpha_vantage(req.symbol)
        headers = {}
        if result.get("errors"):
            headers["X-Partial-Failure"] = "true"
        return JSONResponse(content=result, status_code=201, headers=headers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v2/cards/{card_id}/judgment")
def v2_update_judgment(card_id: str, req: V2JudgmentUpdateRequest):
    """更新判断层。confirm=true 时确认卡片。"""
    result = svc.v2_update_judgment(card_id, req.judgment, req.confirm, req.action)
    if result is None:
        raise HTTPException(status_code=404, detail=f"card {card_id} not found")
    return result


@router.get("/v2/cards")
def v2_list_cards(
    symbol: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    render: bool = Query(False),
    top_k: int = Query(10, ge=1, le=100),
):
    """查询 v2 观点卡。render=true 返回决策引擎可消费格式。"""
    return svc.v2_query_cards(
        symbol=symbol, status=status, event_type=event_type,
        render=render, top_k=top_k,
    )


@router.get("/v2/holdings_us")
def v2_holdings_us():
    """返回当前持仓列表，按市值占比排序，从 ticker+currency 直接推断 symbol。"""
    import re
    from app.database import get_session
    from app.models import Position
    from app.state import portfolio_id as default_pid

    def _infer_symbol(ticker: str, currency: str) -> tuple:
        """从 ticker + currency 推断 (symbol, market, supported)。"""
        if not ticker or not ticker.strip():
            return None, None, False
        t = ticker.strip()

        # A 股：数字.SH / 数字.SZ
        if re.match(r'^\d{6}\.S[HZ]$', t):
            market = t[-2:]
            code = t[:6]
            return f"{code}:{market}", market, False  # A 股暂不支持自动拉取

        # 期权格式（如 AAPL240621C00190000）：跳过
        if re.match(r'^[A-Z]+\d{6}[CP]\d+$', t):
            return None, None, False

        # ISIN/基金代码（LU 开头、纯数字 6 位）：不支持
        if t.startswith('LU') or t.startswith('IE') or re.match(r'^\d{6}$', t):
            return None, None, False

        # 其他特殊代码（含 - 或长度异常）：不支持
        if '-' in t or len(t) > 10:
            return None, None, False

        # 纯字母 + USD → 美股
        if re.match(r'^[A-Z]{1,5}$', t) and currency == 'USD':
            return f"{t}:US", "US", True

        # 纯字母但非 USD（罕见）
        if re.match(r'^[A-Z]{1,5}$', t):
            return f"{t}:US", "US", True  # 默认当 US

        return None, None, False

    session = get_session()
    try:
        positions = (
            session.query(Position)
            .filter(Position.portfolio_id == default_pid)
            .filter(Position.market_value_cny > 0)
            .order_by(Position.market_value_cny.desc())
            .all()
        )

        total_mv = sum(p.market_value_cny for p in positions) or 1.0
        seen_names = set()
        result = []

        for p in positions:
            if p.name in seen_names:
                continue
            seen_names.add(p.name)

            symbol, market, supported = _infer_symbol(p.ticker or '', p.currency or 'CNY')

            # 如果 ticker 推断失败，尝试 EntityRegistry 按名称匹配
            if not symbol:
                from research_v2.symbol import get_registry
                registry = get_registry()
                for entity in registry.all_entities():
                    if (entity.display_name_cn in p.name or p.name in entity.display_name_cn
                            or (entity.display_name_en and entity.display_name_en.lower() in (p.name or '').lower())):
                        for s in entity.symbols:
                            if s.market in ("US", "HK"):
                                symbol = str(s)
                                market = s.market
                                supported = True
                                break
                        break

            weight = round(p.market_value_cny / total_mv, 4)

            result.append({
                "symbol": symbol,
                "asset_name": p.name,
                "market": market,
                "supported": supported,
                "weight": weight,
            })

        return result
    finally:
        session.close()
