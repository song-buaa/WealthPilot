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


@router.post("/v2/ingest/akshare", status_code=201)
def v2_ingest_akshare(req: V2IngestAVRequest):
    """触发 AKShare 港股数据拉取。"""
    if not req.symbol.strip():
        raise HTTPException(status_code=400, detail="symbol 不能为空")
    try:
        result = svc.v2_ingest_akshare(req.symbol)
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
    """返回当前持仓列表，按市值占比排序，支持跨市场分组。"""
    import re
    from app.database import get_session
    from app.models import Position
    from app.state import portfolio_id as default_pid

    _ETF_TICKERS = {
        "QQQ", "SPY", "IWM", "GLD", "SLV", "TLT", "IEF", "SHY",
        "HYG", "LQD", "VXX", "UVXY", "SQQQ", "TQQQ", "DIA", "VOO",
    }

    def _infer_symbol(ticker: str, currency: str, name: str = "") -> tuple:
        """从 ticker + currency 推断 (symbol, market, supported)。"""
        if not ticker or not ticker.strip():
            return None, None, False
        t = ticker.strip()
        c = (currency or "").upper()

        # 港股：带 .HK 后缀 或 HKD currency + 纯数字
        if t.endswith(".HK"):
            code = t.replace(".HK", "").lstrip("0") or "0"
            return f"{code}:HK", "HK", True
        if c == "HKD" and re.match(r"^\d{4,5}$", t):
            code = t.lstrip("0") or "0"
            return f"{code}:HK", "HK", True

        # A 股：数字.SH / 数字.SZ
        if re.match(r"^\d{6}\.S[HZ]$", t):
            return f"{t[:6]}:{t[-2:]}", t[-2:], False

        # 期权
        if re.match(r"^[A-Z]+\d{6}[CP]\d+$", t):
            return None, None, False

        # ISIN/基金代码
        if t.startswith("LU") or t.startswith("IE") or re.match(r"^\d{6}$", t):
            return None, None, False

        # 含 - 或过长
        if "-" in t or len(t) > 10:
            return None, None, False

        # 美股：纯字母 + USD
        if re.match(r"^[A-Z]{1,5}$", t) and c == "USD":
            is_etf = t in _ETF_TICKERS or "ETF" in name.upper()
            return f"{t}:US", "US", not is_etf
        if re.match(r"^[A-Z]{1,5}$", t):
            is_etf = t in _ETF_TICKERS or "ETF" in name.upper()
            return f"{t}:US", "US", not is_etf

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

        # 按 symbol 去重（同名不同平台合并市值）
        sym_map: dict[str, dict] = {}
        for p in positions:
            symbol, market, supported = _infer_symbol(p.ticker or "", p.currency or "CNY", p.name or "")
            if not symbol:
                continue
            if symbol in sym_map:
                sym_map[symbol]["weight"] += p.market_value_cny / total_mv
            else:
                sym_map[symbol] = {
                    "symbol": symbol,
                    "asset_name": p.name,
                    "market": market,
                    "supported": supported,
                    "weight": p.market_value_cny / total_mv,
                    "entity_id": None,
                    "sibling_symbols": [],
                }

        # 查 EntityRegistry 填 entity_id + sibling_symbols
        try:
            from research_v2.symbol import Symbol as Sym, get_registry
            registry = get_registry()
            for sym_str, item in sym_map.items():
                try:
                    entity = registry.lookup(Sym.parse(sym_str))
                    if entity:
                        item["entity_id"] = entity.entity_id
                except Exception:
                    pass

            # 填 sibling_symbols
            entity_groups: dict[str, list[str]] = {}
            for sym_str, item in sym_map.items():
                eid = item.get("entity_id")
                if eid:
                    entity_groups.setdefault(eid, []).append(sym_str)
            for sym_str, item in sym_map.items():
                eid = item.get("entity_id")
                if eid and len(entity_groups.get(eid, [])) > 1:
                    item["sibling_symbols"] = [s for s in entity_groups[eid] if s != sym_str]
        except Exception:
            pass

        # 不支持的持仓也包含（标 supported=false）
        seen_names = set()
        unsupported = []
        for p in positions:
            symbol, _, _ = _infer_symbol(p.ticker or "", p.currency or "CNY", p.name or "")
            if symbol and symbol in sym_map:
                continue
            if p.name in seen_names:
                continue
            seen_names.add(p.name)
            unsupported.append({
                "symbol": None,
                "asset_name": p.name,
                "market": None,
                "supported": False,
                "weight": round(p.market_value_cny / total_mv, 4),
                "entity_id": None,
                "sibling_symbols": [],
            })

        result = sorted(sym_map.values(), key=lambda x: -x["weight"])
        for item in result:
            item["weight"] = round(item["weight"], 4)
        result.extend(unsupported)
        return result
    finally:
        session.close()
