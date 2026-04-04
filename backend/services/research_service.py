"""
Research Service — 投研观点业务逻辑

从 app_pages/research.py 提取的纯业务逻辑，去除所有 Streamlit 依赖。
直接复用：app.ai_advisor, app.research, app.models
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import joinedload

from app.models import (
    ResearchDocument, ResearchCard, ResearchViewpoint,
    get_session,
)
from app.ai_advisor import generate_research_card, generate_research_card_full
from app.research import retrieve_research_context, _parse_json_list

try:
    import pypdf as _pypdf
    _HAS_PYPDF = True
except ImportError:
    _HAS_PYPDF = False

try:
    import requests as _requests
    from bs4 import BeautifulSoup as _BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False


# ── 观点库 CRUD ───────────────────────────────────────────────────────────────

def list_viewpoints(query: Optional[str] = None) -> dict:
    """
    获取观点列表，支持关键词检索。
    query 为 None 或空时返回全部。
    """
    session = get_session()
    try:
        if query and query.strip():
            items = retrieve_research_context(query.strip())
            # retrieve_research_context 已返回相关性排序的 dict 列表
            return {"items": items, "total": len(items)}

        viewpoints = (
            session.query(ResearchViewpoint)
            .order_by(ResearchViewpoint.updated_at.desc())
            .all()
        )
        items = [_viewpoint_to_dict(v) for v in viewpoints]
        return {"items": items, "total": len(items)}
    finally:
        session.close()


def create_viewpoint(data: dict) -> dict:
    """手动新增正式观点"""
    session = get_session()
    try:
        v = ResearchViewpoint(
            title=data["title"],
            object_type=data.get("object_type", "asset"),
            object_name=data.get("object_name"),
            market_name=data.get("market_name"),
            topic_tags=_to_json_list(data.get("topic_tags")),
            thesis=data.get("thesis"),
            supporting_points=_to_json_list(data.get("supporting_points")),
            opposing_points=_to_json_list(data.get("opposing_points")),
            key_metrics=_to_json_list(data.get("key_metrics")),
            risks=_to_json_list(data.get("risks")),
            action_suggestion=data.get("action_suggestion"),
            invalidation_conditions=data.get("invalidation_conditions"),
            horizon=data.get("horizon"),
            stance=data.get("stance"),
            user_approval_level=data.get("user_approval_level", "reference"),
            validity_status=data.get("validity_status", "active"),
        )
        session.add(v)
        session.commit()
        session.refresh(v)
        return _viewpoint_to_dict(v)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def update_viewpoint(viewpoint_id: int, data: dict) -> dict:
    """更新观点字段"""
    session = get_session()
    try:
        v = session.query(ResearchViewpoint).get(viewpoint_id)
        if v is None:
            raise ValueError(f"viewpoint {viewpoint_id} not found")

        updatable = [
            "title", "object_type", "object_name", "market_name",
            "thesis", "action_suggestion", "invalidation_conditions",
            "horizon", "stance", "user_approval_level", "validity_status",
        ]
        list_fields = [
            "topic_tags", "supporting_points", "opposing_points",
            "key_metrics", "risks",
        ]

        for field in updatable:
            if field in data:
                setattr(v, field, data[field])
        for field in list_fields:
            if field in data:
                setattr(v, field, _to_json_list(data[field]))

        v.updated_at = datetime.now()
        session.commit()
        session.refresh(v)
        return _viewpoint_to_dict(v)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def delete_viewpoint(viewpoint_id: int) -> None:
    session = get_session()
    try:
        v = session.query(ResearchViewpoint).get(viewpoint_id)
        if v is None:
            raise ValueError(f"viewpoint {viewpoint_id} not found")
        session.delete(v)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── 文档列表 ──────────────────────────────────────────────────────────────────

def list_documents() -> dict:
    """获取所有文档（含解析状态）"""
    session = get_session()
    try:
        docs = (
            session.query(ResearchDocument)
            .order_by(ResearchDocument.uploaded_at.desc())
            .all()
        )
        items = [_document_to_dict(d) for d in docs]
        return {"items": items, "total": len(items)}
    finally:
        session.close()


def delete_document(document_id: int) -> None:
    """删除文档及其关联的候选卡（级联）"""
    session = get_session()
    try:
        d = session.query(ResearchDocument).get(document_id)
        if d is None:
            raise ValueError(f"document {document_id} not found")
        session.delete(d)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── 内容解析 ──────────────────────────────────────────────────────────────────

def parse_text(content: str, title: str, source_url: Optional[str] = None) -> dict:
    """
    解析纯文本/Markdown 内容，生成 ResearchDocument + ResearchCard。
    返回 {"document_id": N, "card": {...}}
    """
    if not content.strip():
        raise ValueError("content 不能为空")

    # 使用 generate_research_card_full 一次提取元数据+结构化字段
    card_data = generate_research_card_full(content)
    if "error" in card_data:
        raise RuntimeError(f"AI 解析失败: {card_data['error']}")

    resolved_title = card_data.get("title") or title or "未命名资料"

    session = get_session()
    try:
        doc = ResearchDocument(
            title=resolved_title,
            source_type="text" if not source_url else "link",
            source_url=source_url,
            raw_content=content,
            object_name=card_data.get("object_name"),
            market_name=card_data.get("market_name"),
            author=card_data.get("author"),
            publish_time=card_data.get("publish_time"),
            parse_status="parsed",
        )
        session.add(doc)
        session.flush()

        card = _create_card_from_data(doc.id, card_data)
        session.add(card)
        session.commit()
        session.refresh(card)

        return {"document_id": doc.id, "document_title": resolved_title, "card": _card_to_dict(card)}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reparse_document(document_id: int) -> dict:
    """重新解析已存档文档（从 raw_content 重新触发 AI）"""
    session = get_session()
    try:
        doc = session.query(ResearchDocument).get(document_id)
        if doc is None:
            raise ValueError(f"document {document_id} not found")
        if not doc.raw_content:
            raise ValueError("该文档没有原始内容，无法重新解析")
        raw_content = doc.raw_content
        title = doc.title
        source_url = doc.source_url
    finally:
        session.close()

    return parse_text(raw_content, title, source_url)


def parse_url(url: str) -> dict:
    """抓取 URL 正文后解析"""
    content = _fetch_url_text(url)
    if not content:
        raise RuntimeError("无法抓取该链接内容，请手动粘贴正文")
    return parse_text(content, title="", source_url=url)


def parse_pdf(file_bytes: bytes, filename: str) -> dict:
    """解析 PDF 文件"""
    text, error = _extract_pdf_text(file_bytes)
    if error:
        raise RuntimeError(f"PDF 解析失败: {error}")
    if not text.strip():
        raise RuntimeError("PDF 中未提取到可读文字（可能是扫描版 PDF）")
    return parse_text(text, title=filename)


# ── 审核：候选卡 → 正式观点 ──────────────────────────────────────────────────

def approve_card(card_id: int, overrides: Optional[dict] = None) -> dict:
    """
    将候选卡升级为正式观点。
    overrides：用户在前端修改的字段（可选）。
    """
    session = get_session()
    try:
        card = (
            session.query(ResearchCard)
            .options(joinedload(ResearchCard.document))
            .get(card_id)
        )
        if card is None:
            raise ValueError(f"card {card_id} not found")
        if card.viewpoint is not None:
            raise ValueError(f"card {card_id} 已关联观点，请直接编辑观点")

        doc = card.document
        ov = overrides or {}

        v = ResearchViewpoint(
            title=ov.get("title") or (doc.title if doc else "未命名"),
            object_type=ov.get("object_type", "asset"),
            object_name=ov.get("object_name") or (doc.object_name if doc else None),
            market_name=ov.get("market_name") or (doc.market_name if doc else None),
            topic_tags=ov.get("topic_tags") or card.suggested_tags,
            thesis=ov.get("thesis") or card.thesis,
            supporting_points=_to_json_list(ov.get("supporting_points")) or card.key_drivers,
            opposing_points=_to_json_list(ov.get("opposing_points")) or card.bear_case,
            key_metrics=ov.get("key_metrics") or card.key_metrics,
            risks=ov.get("risks") or card.risks,
            action_suggestion=ov.get("action_suggestion") or card.action_suggestion,
            invalidation_conditions=ov.get("invalidation_conditions") or card.invalidation_conditions,
            horizon=ov.get("horizon") or card.horizon,
            stance=ov.get("stance") or card.stance,
            user_approval_level=ov.get("user_approval_level", "reference"),
            validity_status="active",
            source_card_id=card.id,
            source_document_id=card.document_id,
        )
        session.add(v)
        session.commit()
        session.refresh(v)
        return _viewpoint_to_dict(v)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def list_cards() -> dict:
    """获取所有候选观点卡（含文档信息和是否已审核）"""
    session = get_session()
    try:
        cards = (
            session.query(ResearchCard)
            .options(
                joinedload(ResearchCard.viewpoint),
                joinedload(ResearchCard.document),
            )
            .join(ResearchDocument, ResearchCard.document_id == ResearchDocument.id)
            .order_by(ResearchCard.created_at.desc())
            .all()
        )
        items = [_card_to_dict(c, include_doc=True) for c in cards]
        return {"items": items, "total": len(items)}
    finally:
        session.close()


# ── 内部：序列化辅助 ──────────────────────────────────────────────────────────

def _viewpoint_to_dict(v: ResearchViewpoint) -> dict:
    return {
        "id":                    v.id,
        "title":                 v.title,
        "object_type":           v.object_type,
        "object_name":           v.object_name,
        "market_name":           v.market_name,
        "topic_tags":            _parse_json_list(v.topic_tags),
        "thesis":                v.thesis,
        "supporting_points":     _parse_json_list(v.supporting_points),
        "opposing_points":       _parse_json_list(v.opposing_points),
        "key_metrics":           _parse_json_list(v.key_metrics),
        "risks":                 _parse_json_list(v.risks),
        "action_suggestion":     v.action_suggestion,
        "invalidation_conditions": v.invalidation_conditions,
        "horizon":               v.horizon,
        "stance":                v.stance,
        "user_approval_level":   v.user_approval_level,
        "validity_status":       v.validity_status,
        "source_card_id":        v.source_card_id,
        "source_document_id":    v.source_document_id,
        "created_at":            v.created_at.isoformat() if v.created_at else None,
        "updated_at":            v.updated_at.isoformat() if v.updated_at else None,
    }


def _document_to_dict(d: ResearchDocument) -> dict:
    return {
        "id":           d.id,
        "title":        d.title,
        "source_type":  d.source_type,
        "source_url":   d.source_url,
        "object_name":  d.object_name,
        "market_name":  d.market_name,
        "author":       d.author,
        "publish_time": d.publish_time,
        "tags":         _parse_json_list(d.tags),
        "parse_status": d.parse_status,
        "notes":        d.notes,
        "uploaded_at":  d.uploaded_at.isoformat() if d.uploaded_at else None,
    }


def _card_to_dict(card: ResearchCard, include_doc: bool = False) -> dict:
    d = {
        "id":                    card.id,
        "document_id":           card.document_id,
        "summary":               card.summary,
        "thesis":                card.thesis,
        "bull_case":             card.bull_case,
        "bear_case":             card.bear_case,
        "key_drivers":           _parse_json_list(card.key_drivers),
        "risks":                 _parse_json_list(card.risks),
        "key_metrics":           _parse_json_list(card.key_metrics),
        "horizon":               card.horizon,
        "stance":                card.stance,
        "action_suggestion":     card.action_suggestion,
        "invalidation_conditions": card.invalidation_conditions,
        "suggested_tags":        _parse_json_list(card.suggested_tags),
        "is_approved":           card.viewpoint is not None,
        "viewpoint_id":          card.viewpoint.id if card.viewpoint else None,
        "created_at":            card.created_at.isoformat() if card.created_at else None,
    }
    if include_doc and card.document:
        d["document_title"] = card.document.title
        d["document_object_name"] = card.document.object_name
    return d


def _to_json_list(value) -> Optional[str]:
    """将 list 或 None 转为 JSON 字符串列表"""
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return value  # 已经是 JSON 字符串


def _create_card_from_data(document_id: int, card_data: dict) -> ResearchCard:
    def _jl(key):
        v = card_data.get(key)
        return json.dumps(v, ensure_ascii=False) if isinstance(v, list) else None

    def _str(key):
        """Text 字段：list→换行拼接，其他原样返回"""
        v = card_data.get(key)
        if isinstance(v, list):
            return "\n".join(str(i) for i in v if i)
        return v

    return ResearchCard(
        document_id=document_id,
        summary=_str("summary"),
        thesis=_str("thesis"),
        bull_case=_str("bull_case"),
        bear_case=_str("bear_case"),
        key_drivers=_jl("key_drivers"),
        risks=_jl("risks"),
        key_metrics=_jl("key_metrics"),
        horizon=card_data.get("horizon"),
        stance=card_data.get("stance"),
        action_suggestion=card_data.get("action_suggestion"),
        invalidation_conditions=card_data.get("invalidation_conditions"),
        suggested_tags=_jl("suggested_tags"),
    )


# ── 内部：内容抓取 ────────────────────────────────────────────────────────────

def _extract_pdf_text(file_bytes: bytes) -> tuple[str, str]:
    if not _HAS_PYPDF:
        return "", "pypdf 未安装（pip install pypdf）"
    try:
        reader = _pypdf.PdfReader(io.BytesIO(file_bytes))
        texts = [page.extract_text() or "" for page in reader.pages[:20]]
        text = "\n\n".join(t for t in texts if t.strip())
        return text, ""
    except Exception as e:
        return "", str(e)


def _fetch_url_text(url: str) -> str:
    if not _HAS_BS4:
        return ""
    try:
        import re
        headers = {"User-Agent": "Mozilla/5.0 (compatible; WealthPilot/1.0)"}
        resp = _requests.get(url, headers=headers, timeout=12)
        resp.raise_for_status()
        soup = _BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:8000]
    except Exception:
        return ""
