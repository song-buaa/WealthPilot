"""
投研观点 — WealthPilot Research & Viewpoints Module

流程：资料导入 → AI解析 → 候选观点卡审核 → 正式观点库
MVP：结构化字段检索，无 embedding / RAG。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import pandas as pd
import streamlit as st
from sqlalchemy.orm import joinedload

from app.models import (
    ResearchDocument, ResearchCard, ResearchViewpoint,
    get_session, init_db,
)
from app.ai_advisor import generate_research_card, generate_research_card_full
from app.research import retrieve_research_context, _parse_json_list

# pypdf / requests / bs4：在模块级导入，避免在每次 Streamlit rerun 时重复初始化
import io as _io
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


# ──────────────────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────────────────

_NAV_ITEMS = ["📥  资料导入", "🃏  候选观点卡", "📚  观点库", "🔍  决策检索"]

_STANCE_LABELS = {
    "bullish":  "🟢 看多",
    "bearish":  "🔴 看空",
    "neutral":  "⚪ 中性",
    "watch":    "👁️ 观察",
}
_HORIZON_LABELS = {
    "short":  "短期",
    "medium": "中期",
    "long":   "长期",
}
_APPROVAL_LABELS = {
    "strong":    "⭐⭐⭐ 强认可",
    "partial":   "⭐⭐ 部分认可",
    "reference": "⭐ 参考",
}
_VALIDITY_LABELS = {
    "active":   "🟢 有效",
    "watch":    "🟡 观察",
    "outdated": "🟠 过时",
    "invalid":  "🔴 失效",
}
_OBJECT_TYPE_LABELS = {
    "asset":    "个股/资产",
    "sector":   "行业/板块",
    "market":   "市场",
    "macro":    "宏观",
    "strategy": "策略",
}


# ──────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────

def _fmt_list(s: Optional[str]) -> str:
    """JSON 列表字段 → 换行文本，用于展示"""
    items = _parse_json_list(s)
    return "\n".join(f"• {it}" for it in items) if items else "—"


def _tags_to_str(s: Optional[str]) -> str:
    items = _parse_json_list(s)
    return "  ".join(f"`{t}`" for t in items) if items else "—"


def _str_to_json_list(text: str) -> str:
    """用户输入的换行文本 → JSON 列表字符串"""
    lines = [l.strip().lstrip("•- ") for l in text.strip().splitlines() if l.strip()]
    return json.dumps(lines, ensure_ascii=False)


def _tags_input_to_json(text: str) -> str:
    """逗号/空格分隔的标签 → JSON 列表"""
    import re
    tags = [t.strip() for t in re.split(r"[，,\s]+", text) if t.strip()]
    return json.dumps(tags, ensure_ascii=False)


# ──────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────
# Section 1：资料导入
# ──────────────────────────────────────────────────────────

def _extract_pdf_text(file_bytes: bytes) -> tuple[str, str]:
    """
    从 PDF 字节流提取纯文本（最多 20 页）。
    返回 (text, error_msg)；成功时 error_msg 为空字符串。
    """
    if not _HAS_PYPDF:
        return "", "pypdf 未安装，请联系管理员"
    try:
        reader = _pypdf.PdfReader(_io.BytesIO(file_bytes))
        texts = [page.extract_text() or "" for page in reader.pages[:20]]
        text = "\n\n".join(t for t in texts if t.strip())
        return text, ""
    except Exception as e:
        return "", str(e)


def _fetch_url_text(url: str) -> str:
    """抓取链接正文（requests + BeautifulSoup）。失败或超时返回空字符串。"""
    if not _HAS_BS4:
        return ""
    try:
        import re as _re
        headers = {"User-Agent": "Mozilla/5.0 (compatible; WealthPilot/1.0)"}
        resp = _requests.get(url, headers=headers, timeout=12)
        resp.raise_for_status()
        soup = _BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = _re.sub(r"\n{3,}", "\n\n", text)
        return text[:8000]
    except Exception:
        return ""


def _render_import() -> None:
    if st.session_state.get("ri_step", 0) == 1:
        _render_import_step1_inline()
    else:
        _render_import_step0()

    st.divider()
    _render_pending_section()

    st.divider()
    st.subheader("已导入资料")
    _render_document_list()


def _render_import_step0() -> None:
    """Step 0：选择资料来源并输入内容。"""
    st.subheader("新增研究资料")

    source_type = st.radio(
        "资料类型",
        ["text", "markdown", "link", "pdf"],
        format_func=lambda x: {
            "text":     "📝 纯文本粘贴",
            "markdown": "📄 Markdown",
            "link":     "🔗 链接（公众号/博客）",
            "pdf":      "📑 PDF 上传",
        }[x],
        horizontal=True,
        key="ri_source_type",
    )

    st.divider()

    raw_content = ""
    source_url = ""

    if source_type == "text":
        raw_content = st.text_area(
            "粘贴资料正文",
            height=280,
            key="ri_content",
            placeholder=(
                "把研报摘要、博主分析、会议纪要等原文粘贴到这里。\n"
                "无需全文，关键论据 + 结论段落即可（建议 300~2000 字）。"
            ),
            label_visibility="collapsed",
        )

    elif source_type == "markdown":
        uploaded_md = st.file_uploader(
            "上传 Markdown 文件（.md / .txt）",
            type=["md", "markdown", "txt"],
            key="ri_md",
        )
        if uploaded_md is not None:
            if st.session_state.get("_ri_md_cache_name") != uploaded_md.name:
                raw_content = uploaded_md.read().decode("utf-8", errors="replace")
                st.session_state["_ri_md_cache"] = raw_content
                st.session_state["_ri_md_cache_name"] = uploaded_md.name
            else:
                raw_content = st.session_state.get("_ri_md_cache", "")
            st.caption(f"✅ 已读取：{uploaded_md.name}（{len(raw_content)} 字）")
        else:
            raw_content = st.text_area(
                "或直接粘贴 Markdown 正文",
                height=250,
                key="ri_md_content",
                placeholder="也可以不上传文件，直接把内容粘贴到这里。",
            )

    elif source_type == "link":
        source_url = st.text_input(
            "链接地址",
            key="ri_url",
            placeholder="https://mp.weixin.qq.com/s/...",
        )
        st.caption("系统将尝试自动抓取正文；若抓取失败（需登录的页面），请在下方手动粘贴。")
        raw_content = st.text_area(
            "手动粘贴正文（可选，自动抓取失败时使用）",
            height=180,
            key="ri_content_link",
        )

    elif source_type == "pdf":
        uploaded_pdf = st.file_uploader(
            "上传 PDF 文件",
            type=["pdf"],
            key="ri_pdf",
        )
        if uploaded_pdf is not None:
            source_url = uploaded_pdf.name
            if st.session_state.get("_ri_pdf_name") != uploaded_pdf.name:
                st.session_state["_ri_pdf_bytes"] = uploaded_pdf.read()
                st.session_state["_ri_pdf_name"] = uploaded_pdf.name
            st.caption(f"✅ 已上传：{uploaded_pdf.name}")
        else:
            st.session_state.pop("_ri_pdf_bytes", None)
            st.session_state.pop("_ri_pdf_name", None)

    st.write("")

    if st.button("🔍 AI 解析", type="primary", key="ri_parse_btn"):
        content_to_parse = raw_content

        if source_type == "pdf":
            pdf_bytes = st.session_state.get("_ri_pdf_bytes")
            if not pdf_bytes:
                st.error("请先上传 PDF 文件。")
                return
            with st.spinner("正在提取 PDF 文字…"):
                content_to_parse, pdf_err = _extract_pdf_text(pdf_bytes)
            if not content_to_parse.strip():
                if pdf_err:
                    st.error(f"PDF 文字提取出错：{pdf_err}")
                else:
                    st.error("PDF 未能提取到文字（可能是扫描件或图片 PDF），请手动粘贴关键段落后重试。")
                return

        elif source_type == "link" and source_url and not raw_content.strip():
            with st.spinner("正在抓取链接正文…"):
                content_to_parse = _fetch_url_text(source_url)
            if not content_to_parse.strip():
                st.warning("自动抓取失败，请在上方手动粘贴正文后重试。")
                return

        if not content_to_parse.strip():
            st.error("请提供资料内容（上传文件或粘贴文字）。")
            return

        with st.spinner("AI 正在解析投研内容，请稍候…"):
            parsed = generate_research_card_full(content_to_parse)

        if "error" in parsed:
            st.error(f"AI 解析失败：{parsed['error']}")
            return

        st.session_state["ri_raw_content"] = content_to_parse
        st.session_state["ri_source_url"] = source_url
        st.session_state["ri_source_type_val"] = source_type
        st.session_state["ri_parsed"] = parsed
        st.session_state["ri_step"] = 1
        st.rerun()


def _ai_str(v) -> str:
    """将 AI 返回值统一转为可读字符串：列表 → 换行 bullet；其他 → str。"""
    if isinstance(v, list):
        return "\n".join(f"• {item}" for item in v if item)
    return v or ""


def _render_import_step1_inline() -> None:
    """Step 1：卡片样式预览（只读）+ 可编辑元数据 + 四个操作按钮。"""
    parsed = st.session_state.get("ri_parsed", {})
    raw_content = st.session_state.get("ri_raw_content", "")
    source_url = st.session_state.get("ri_source_url", "")
    source_type = st.session_state.get("ri_source_type_val", "text")

    col_hd, col_back = st.columns([5, 1])
    with col_hd:
        st.markdown("**AI 解析结果**")
    with col_back:
        if st.button("← 返回", key="ri_back"):
            st.session_state["ri_step"] = 0
            st.rerun()
    st.caption("确认基本信息后选择操作。如需修改观点内容，选「修改后录入」，在下方待审核区编辑。")

    # ── 元数据（紧凑可编辑，两行） ────────────────────────
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    with c1:
        title = st.text_input("资料标题 *", value=parsed.get("title") or "", key="ri2_title")
    with c2:
        object_name = st.text_input("标的名称", value=parsed.get("object_name") or "", key="ri2_object")
    with c3:
        _mkt_opts = ["", "港股", "美股", "A股", "宏观", "行业", "其他"]
        _mkt_ai = parsed.get("market_name") or ""
        market_name = st.selectbox("市场", _mkt_opts,
                                   index=_mkt_opts.index(_mkt_ai) if _mkt_ai in _mkt_opts else 0,
                                   key="ri2_market")
    with c4:
        author = st.text_input("作者/来源", value=parsed.get("author") or "", key="ri2_author")

    c5, c6, c7, c8 = st.columns([1, 2, 1, 1])
    with c5:
        publish_time = st.text_input("发布时间", value=parsed.get("publish_time") or "", key="ri2_publish")
    with c6:
        _ai_tags = parsed.get("suggested_tags")
        tags_input = st.text_input(
            "标签（逗号分隔）",
            value=", ".join(_ai_tags) if isinstance(_ai_tags, list) else "",
            key="ri2_tags",
        )
    with c7:
        _horizon_opts = ["", "short", "medium", "long"]
        _h_ai = parsed.get("horizon") or ""
        horizon = st.selectbox("期限", _horizon_opts,
                               index=_horizon_opts.index(_h_ai) if _h_ai in _horizon_opts else 0,
                               format_func=lambda x: _HORIZON_LABELS.get(x, "未设置") if x else "未设置",
                               key="ri2_horizon")
    with c8:
        _stance_opts = ["", "bullish", "bearish", "neutral", "watch"]
        _s_ai = parsed.get("stance") or ""
        stance = st.selectbox("立场", _stance_opts,
                              index=_stance_opts.index(_s_ai) if _s_ai in _stance_opts else 0,
                              format_func=lambda x: _STANCE_LABELS.get(x, "未设置") if x else "未设置",
                              key="ri2_stance")

    # ── 卡片内容（只读，与候选观点卡相同样式） ────────────
    st.write("")
    col_l, col_r = st.columns([3, 2])
    with col_l:
        if parsed.get("summary"):
            st.markdown(f"**📌 摘要**\n\n{parsed['summary']}")
        st.markdown(f"**💡 核心结论（Thesis）**\n\n{_ai_str(parsed.get('thesis')) or '—'}")
        c_bull, c_bear = st.columns(2)
        with c_bull:
            st.markdown(f"**🟢 看多逻辑**\n\n{_ai_str(parsed.get('bull_case')) or '—'}")
        with c_bear:
            st.markdown(f"**🔴 看空逻辑**\n\n{_ai_str(parsed.get('bear_case')) or '—'}")
        if parsed.get("action_suggestion"):
            st.markdown(f"**⚡ 操作建议**\n\n{_ai_str(parsed.get('action_suggestion'))}")
        if parsed.get("invalidation_conditions"):
            st.markdown(f"**🚨 失效条件**\n\n{parsed['invalidation_conditions']}")
    with col_r:
        if parsed.get("key_drivers"):
            st.markdown(f"**关键驱动**\n\n{_ai_str(parsed['key_drivers'])}")
        if parsed.get("risks"):
            st.markdown(f"**主要风险**\n\n{_ai_str(parsed['risks'])}")
        if parsed.get("key_metrics"):
            st.markdown(f"**观察指标**\n\n{_ai_str(parsed['key_metrics'])}")
        if isinstance(parsed.get("suggested_tags"), list):
            st.markdown(f"**建议标签**\n\n{'  '.join(f'`{t}`' for t in parsed['suggested_tags'])}")

    # ── 操作按钮 ──────────────────────────────────────────
    st.write("")
    ca, cb, cc, cd = st.columns(4)
    with ca:
        approve = st.button("✅ 认可·直接录入", type="primary",
                            use_container_width=True, key="ri2_approve")
    with cb:
        to_pending = st.button("✏️ 修改后录入", use_container_width=True, key="ri2_pending")
    with cc:
        save_only_btn = st.button("💾 仅保留资料", use_container_width=True, key="ri2_save_only")
    with cd:
        discard_btn = st.button("🗑️ 丢弃", use_container_width=True, key="ri2_discard")

    # ── 辅助函数 ──────────────────────────────────────────
    def _jl(key):
        v = parsed.get(key)
        return json.dumps(v, ensure_ascii=False) if isinstance(v, list) else None

    def _build_doc(status="parsed"):
        return ResearchDocument(
            title=title.strip(),
            source_type=source_type,
            source_url=source_url.strip() or None,
            raw_content=raw_content.strip(),
            author=author.strip() or None,
            publish_time=publish_time.strip() or None,
            object_name=object_name.strip() or None,
            market_name=market_name or None,
            tags=_tags_input_to_json(tags_input) if tags_input.strip() else None,
            parse_status=status,
        )

    def _build_card(doc_id):
        return ResearchCard(
            document_id=doc_id,
            summary=parsed.get("summary"),
            thesis=_ai_str(parsed.get("thesis")) or None,
            bull_case=_ai_str(parsed.get("bull_case")) or None,
            bear_case=_ai_str(parsed.get("bear_case")) or None,
            key_drivers=_jl("key_drivers"),
            risks=_jl("risks"),
            key_metrics=_jl("key_metrics"),
            horizon=horizon or None,
            stance=stance or None,
            action_suggestion=_ai_str(parsed.get("action_suggestion")) or None,
            invalidation_conditions=parsed.get("invalidation_conditions"),
            suggested_tags=_tags_input_to_json(tags_input) if tags_input.strip() else _jl("suggested_tags"),
        )

    def _clear_state():
        for k in ["ri_step", "ri_parsed", "ri_raw_content", "ri_source_url",
                  "ri_source_type_val", "_ri_pdf_bytes", "_ri_pdf_name"]:
            st.session_state.pop(k, None)

    if (approve or to_pending or save_only_btn) and not title.strip():
        st.error("请填写资料标题")
        return

    if approve:
        session = get_session()
        try:
            if session.query(ResearchDocument).filter(
                ResearchDocument.title == title.strip(),
                ResearchDocument.parse_status != "discarded",
            ).first():
                st.warning(f"⚠️ 已存在同名资料「{title.strip()}」")
                return
            doc = _build_doc()
            session.add(doc)
            session.flush()
            card = _build_card(doc.id)
            session.add(card)
            session.flush()
            vp = ResearchViewpoint(
                title=title.strip(),
                object_type="asset",
                object_name=object_name.strip() or None,
                market_name=market_name or None,
                topic_tags=_tags_input_to_json(tags_input) if tags_input.strip() else _jl("suggested_tags"),
                thesis=_ai_str(parsed.get("thesis")) or None,
                supporting_points=_jl("key_drivers"),
                opposing_points=_ai_str(parsed.get("bear_case")) or None,
                key_metrics=_jl("key_metrics"),
                risks=_jl("risks"),
                action_suggestion=_ai_str(parsed.get("action_suggestion")) or None,
                invalidation_conditions=parsed.get("invalidation_conditions"),
                horizon=horizon or None,
                stance=stance or None,
                user_approval_level="strong",
                validity_status="active",
                source_card_id=card.id,
                source_document_id=doc.id,
            )
            session.add(vp)
            session.commit()
            _clear_state()
            st.toast("✅ 已直接录入观点库！", icon="✅")
            st.rerun()
        except Exception as e:
            session.rollback()
            st.error(f"保存失败：{str(e)}")
        finally:
            session.close()

    elif to_pending:
        session = get_session()
        try:
            if session.query(ResearchDocument).filter(
                ResearchDocument.title == title.strip(),
                ResearchDocument.parse_status != "discarded",
            ).first():
                st.warning(f"⚠️ 已存在同名资料「{title.strip()}」")
                return
            doc = _build_doc()
            session.add(doc)
            session.flush()
            card = _build_card(doc.id)
            session.add(card)
            session.commit()
            _clear_state()
            st.toast("已保存到待审核，请在下方编辑后录入。", icon="📋")
            st.rerun()
        except Exception as e:
            session.rollback()
            st.error(f"保存失败：{str(e)}")
        finally:
            session.close()

    elif save_only_btn:
        session = get_session()
        try:
            doc = _build_doc(status="saved_only")
            session.add(doc)
            session.commit()
            _clear_state()
            st.toast("资料已保存（仅存档），可在下方已导入资料中触发解析。", icon="💾")
            st.rerun()
        except Exception as e:
            session.rollback()
            st.error(f"保存失败：{str(e)}")
        finally:
            session.close()

    elif discard_btn:
        _clear_state()
        st.rerun()


def _render_pending_section() -> None:
    """待审核观点卡（原「候选观点卡」Tab 内容，合并到资料导入）。"""
    st.subheader("待审核观点卡")
    session = get_session()
    try:
        cards = (
            session.query(ResearchCard)
            .options(joinedload(ResearchCard.viewpoint), joinedload(ResearchCard.document))
            .join(ResearchDocument, ResearchCard.document_id == ResearchDocument.id)
            .filter(ResearchDocument.parse_status == "parsed")
            .order_by(ResearchCard.created_at.desc())
            .all()
        )
        card_docs = {c.id: c.document for c in cards}
    finally:
        session.close()

    unreviewed = [c for c in cards if c.viewpoint is None]
    if not unreviewed:
        st.info("暂无待审核观点卡。解析完资料并选择「修改后录入」后，观点卡会出现在这里。")
        return
    st.markdown(f"共 **{len(unreviewed)}** 张待审核")
    for card in unreviewed:
        doc = card_docs.get(card.id) or card.document
        _render_single_card(card, doc, reviewed=False)


def _render_document_list() -> None:
    session = get_session()
    try:
        docs = session.query(ResearchDocument).filter(
            ResearchDocument.parse_status != "discarded"
        ).order_by(
            ResearchDocument.uploaded_at.desc()
        ).limit(50).all()
    finally:
        session.close()

    if not docs:
        st.info("尚无资料，请在上方新增。")
        return

    status_icon = {
        "pending":    "🕐 待解析",
        "parsed":     "✅ 已解析",
        "saved_only": "💾 仅保存",
        "discarded":  "🗑️ 已丢弃",
    }

    rows = []
    for d in docs:
        rows.append({
            "标题": d.title,
            "类型": d.source_type,
            "标的": d.object_name or "—",
            "市场": d.market_name or "—",
            "状态": status_icon.get(d.parse_status, d.parse_status),
            "上传时间": d.uploaded_at.strftime("%m-%d %H:%M") if d.uploaded_at else "—",
            "_id": d.id,
        })

    df = pd.DataFrame(rows)
    display_df = df.drop(columns=["_id"])
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # P1：待解析列表同时包含 pending 和 saved_only，两者都应允许触发解析
    pending = [d for d in docs if d.parse_status in ("pending", "saved_only")]
    if pending:
        st.write("")
        st.markdown(f"**{len(pending)} 份资料待解析**")
        sel_title = st.selectbox(
            "选择要解析的资料",
            [d.title for d in pending],
            key="ri_pending_sel",
        )
        if st.button("⚡ 对选中资料 AI 解析", key="ri_parse_existing"):
            doc = next(d for d in pending if d.title == sel_title)
            _run_parse_for_doc(doc)


def _run_parse_for_doc(doc: ResearchDocument) -> None:
    with st.spinner("AI 正在解析，请稍候…"):
        card_data = generate_research_card(
            raw_content=doc.raw_content or "",
            title=doc.title,
            object_name=doc.object_name or "",
            market_name=doc.market_name or "",
        )

    session = get_session()
    try:
        doc_db = session.query(ResearchDocument).get(doc.id)
        if "error" in card_data:
            st.error(f"解析失败：{card_data['error']}")
            return

        def _jl(key):
            v = card_data.get(key)
            return json.dumps(v, ensure_ascii=False) if isinstance(v, list) else None

        # P1：防重复解析 —— 若该 document 已有 Card，则更新字段而非新增
        existing_card = session.query(ResearchCard).filter_by(document_id=doc.id).first()
        if existing_card:
            existing_card.summary               = card_data.get("summary")
            existing_card.thesis                = card_data.get("thesis")
            existing_card.bull_case             = card_data.get("bull_case")
            existing_card.bear_case             = card_data.get("bear_case")
            existing_card.key_drivers           = _jl("key_drivers")
            existing_card.risks                 = _jl("risks")
            existing_card.key_metrics           = _jl("key_metrics")
            existing_card.horizon               = card_data.get("horizon")
            existing_card.stance                = card_data.get("stance")
            existing_card.action_suggestion     = card_data.get("action_suggestion")
            existing_card.invalidation_conditions = card_data.get("invalidation_conditions")
            existing_card.suggested_tags        = _jl("suggested_tags")
            st.info("ℹ️ 该资料已有解析结果，已更新为最新内容。")
        else:
            card = ResearchCard(
                document_id=doc.id,
                summary=card_data.get("summary"),
                thesis=card_data.get("thesis"),
                bull_case=card_data.get("bull_case"),
                bear_case=card_data.get("bear_case"),
                key_drivers=_jl("key_drivers"),
                risks=_jl("risks"),
                key_metrics=_jl("key_metrics"),
                horizon=card_data.get("horizon"),
                stance=card_data.get("stance"),
                action_suggestion=card_data.get("action_suggestion"),
                invalidation_conditions=card_data.get("invalidation_conditions"),
                suggested_tags=_jl("suggested_tags"),
            )
            session.add(card)

        doc_db.parse_status = "parsed"
        session.commit()
        st.success("✅ 解析完成！请点击上方「候选观点卡」Tab 查看。")
        st.rerun()
    except Exception as e:
        session.rollback()
        st.error(f"保存解析结果失败：{str(e)}")
    finally:
        session.close()


# ──────────────────────────────────────────────────────────
# Section 2：候选观点卡
# ──────────────────────────────────────────────────────────

def _render_cards() -> None:
    session = get_session()
    try:
        # 使用 joinedload 预加载关联对象，避免 session 关闭后 lazy-load 触发 DetachedInstanceError
        cards = (
            session.query(ResearchCard)
            .options(
                joinedload(ResearchCard.viewpoint),
                joinedload(ResearchCard.document),
            )
            .join(ResearchDocument, ResearchCard.document_id == ResearchDocument.id)
            .filter(ResearchDocument.parse_status == "parsed")
            .order_by(ResearchCard.created_at.desc())
            .all()
        )
        card_docs = {c.id: c.document for c in cards}
    finally:
        session.close()

    if not cards:
        st.info("暂无候选观点卡。请先在「资料导入」中添加资料并触发 AI 解析。")
        return

    # 筛选已录入 / 未录入
    unreviewed = [c for c in cards if c.viewpoint is None]
    reviewed   = [c for c in cards if c.viewpoint is not None]

    st.markdown(
        f"共 **{len(unreviewed)}** 张待审核 · **{len(reviewed)}** 张已录入观点库"
    )

    if unreviewed:
        st.subheader("待审核")
        for card in unreviewed:
            doc = card_docs.get(card.id) or card.document
            _render_single_card(card, doc, reviewed=False)

    if reviewed:
        with st.expander(f"已处理 ({len(reviewed)} 张)", expanded=False):
            for card in reviewed:
                doc = card_docs.get(card.id) or card.document
                _render_single_card(card, doc, reviewed=True)


def _render_single_card(card: ResearchCard, doc: ResearchDocument,
                        reviewed: bool = False) -> None:
    stance_label = _STANCE_LABELS.get(card.stance or "", card.stance or "未知")
    horizon_label = _HORIZON_LABELS.get(card.horizon or "", card.horizon or "未知")
    title = doc.title if doc else f"资料 #{card.document_id}"

    with st.expander(
        f"{'✅ ' if reviewed else ''}{stance_label}  |  {title}  "
        f"({horizon_label})  —  {card.created_at.strftime('%m-%d') if card.created_at else ''}",
        expanded=not reviewed,
    ):
        col_l, col_r = st.columns([3, 2])

        with col_l:
            st.markdown(f"**📌 摘要**\n\n{card.summary or '—'}")
            st.markdown(f"**💡 核心结论（Thesis）**\n\n{card.thesis or '—'}")

            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**🟢 看多逻辑**\n\n{card.bull_case or '—'}")
            with c2:
                st.markdown(f"**🔴 看空逻辑**\n\n{card.bear_case or '—'}")

            st.markdown(f"**⚡ 操作建议**\n\n{card.action_suggestion or '—'}")
            st.markdown(f"**🚨 失效条件**\n\n{card.invalidation_conditions or '—'}")

        with col_r:
            st.markdown(f"**关键驱动**\n\n{_fmt_list(card.key_drivers)}")
            st.markdown(f"**主要风险**\n\n{_fmt_list(card.risks)}")
            st.markdown(f"**观察指标**\n\n{_fmt_list(card.key_metrics)}")
            st.markdown(f"**建议标签**\n\n{_tags_to_str(card.suggested_tags)}")

        if not reviewed:
            st.divider()
            _render_card_actions(card, doc)


def _render_card_actions(card: ResearchCard, doc: ResearchDocument) -> None:
    """候选卡片的四个操作按钮"""
    c1, c2, c3, c4 = st.columns(4)
    key_prefix = f"card_{card.id}"

    with c1:
        approve = st.button(
            "✅ 认可 · 直接录入",
            use_container_width=True, type="primary",
            key=f"{key_prefix}_approve",
        )
    with c2:
        edit = st.button(
            "✏️ 修改后录入",
            use_container_width=True,
            key=f"{key_prefix}_edit",
        )
    with c3:
        save_only = st.button(
            "💾 仅保留资料",
            use_container_width=True,
            key=f"{key_prefix}_save",
        )
    with c4:
        discard = st.button(
            "🗑️ 丢弃",
            use_container_width=True,
            key=f"{key_prefix}_discard",
        )

    # 修改表单（展开状态存在 session_state）
    edit_key = f"{key_prefix}_editing"
    if edit:
        st.session_state[edit_key] = True
    if approve:
        _save_viewpoint_from_card(card, doc, approval="strong", edited_fields={})
        st.rerun()
    if save_only:
        _set_doc_status(doc.id, "saved_only")
        st.success("已标记为「仅保存」")
    if discard:
        _delete_doc(doc.id)
        st.rerun()

    if st.session_state.get(edit_key):
        _render_edit_form(card, doc, key_prefix)


def _render_edit_form(card: ResearchCard, doc: ResearchDocument,
                      key_prefix: str) -> None:
    """修改候选卡内容后录入观点库的表单"""
    st.markdown("---")
    st.markdown("**✏️ 编辑后录入**")

    col_a, col_b = st.columns(2)
    with col_a:
        title      = st.text_input("观点标题 *", value=doc.title,
                                   key=f"{key_prefix}_ef_title")
        object_name = st.text_input("标的名称", value=doc.object_name or "",
                                    key=f"{key_prefix}_ef_obj")
        market_name = st.text_input("市场", value=doc.market_name or "",
                                    key=f"{key_prefix}_ef_mkt")
        object_type = st.selectbox(
            "标的类型",
            list(_OBJECT_TYPE_LABELS.keys()),
            format_func=lambda x: _OBJECT_TYPE_LABELS[x],
            key=f"{key_prefix}_ef_otype",
        )
        approval = st.selectbox(
            "认可程度",
            list(_APPROVAL_LABELS.keys()),
            format_func=lambda x: _APPROVAL_LABELS[x],
            key=f"{key_prefix}_ef_appr",
        )
        tags_raw = st.text_input(
            "标签（逗号分隔）",
            value=", ".join(_parse_json_list(card.suggested_tags)),
            key=f"{key_prefix}_ef_tags",
        )

    with col_b:
        thesis = st.text_area("核心结论", value=card.thesis or "",
                              height=90, key=f"{key_prefix}_ef_thesis")
        action = st.text_area("操作建议", value=card.action_suggestion or "",
                              height=70, key=f"{key_prefix}_ef_action")
        invalidation = st.text_area(
            "失效条件", value=card.invalidation_conditions or "",
            height=70, key=f"{key_prefix}_ef_inv",
        )

    if st.button("💾 确认录入观点库", type="primary",
                 use_container_width=True, key=f"{key_prefix}_ef_submit"):
        _save_viewpoint_from_card(
            card, doc,
            approval=approval,
            edited_fields={
                "title": title,
                "object_name": object_name,
                "market_name": market_name,
                "object_type": object_type,
                "topic_tags": _tags_input_to_json(tags_raw),
                "thesis": thesis,
                "action_suggestion": action,
                "invalidation_conditions": invalidation,
            },
        )
        st.session_state.pop(f"{key_prefix}_editing", None)
        st.rerun()


def _save_viewpoint_from_card(
    card: ResearchCard,
    doc: ResearchDocument,
    approval: str,
    edited_fields: dict,
) -> None:
    session = get_session()
    try:
        card_db = session.query(ResearchCard).get(card.id)
        vp = ResearchViewpoint(
            title=edited_fields.get("title") or doc.title,
            object_type=edited_fields.get("object_type", "asset"),
            object_name=edited_fields.get("object_name") or doc.object_name,
            market_name=edited_fields.get("market_name") or doc.market_name,
            topic_tags=edited_fields.get("topic_tags") or card.suggested_tags,
            thesis=edited_fields.get("thesis") or card.thesis,
            supporting_points=card.key_drivers,
            opposing_points=card.bear_case,
            key_metrics=card.key_metrics,
            risks=card.risks,
            action_suggestion=edited_fields.get("action_suggestion") or card.action_suggestion,
            invalidation_conditions=edited_fields.get("invalidation_conditions") or card.invalidation_conditions,
            horizon=card.horizon,
            stance=card.stance,
            user_approval_level=approval,
            validity_status="active",
            source_card_id=card.id,
            source_document_id=doc.id,
        )
        session.add(vp)
        doc_db = session.query(ResearchDocument).get(doc.id)
        if doc_db:
            doc_db.parse_status = "parsed"  # 保持 parsed，卡片已绑定 viewpoint
        session.commit()
        st.toast("✅ 已录入正式观点库！", icon="✅")
    except Exception as e:
        session.rollback()
        st.error(f"录入失败：{str(e)}")
    finally:
        session.close()


def _delete_doc(doc_id: int) -> None:
    """彻底删除文档及其关联的候选卡（级联删除）。"""
    session = get_session()
    try:
        session.query(ResearchCard).filter_by(document_id=doc_id).delete()
        doc = session.query(ResearchDocument).get(doc_id)
        if doc:
            session.delete(doc)
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def _set_doc_status(doc_id: int, status: str) -> None:
    session = get_session()
    try:
        doc = session.query(ResearchDocument).get(doc_id)
        if doc:
            doc.parse_status = status
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


# ──────────────────────────────────────────────────────────
# Section 3：观点库
# ──────────────────────────────────────────────────────────

def _render_viewpoints() -> None:
    session = get_session()
    try:
        all_vps = session.query(ResearchViewpoint).order_by(
            ResearchViewpoint.updated_at.desc()
        ).all()
    finally:
        session.close()

    if not all_vps:
        st.info("观点库为空。请在「候选观点卡」中审核并录入观点。")
        return

    # ── 筛选栏 ──────────────────────────────────────────
    with st.expander("🔎 筛选", expanded=True):
        fc1, fc2, fc3, fc4, fc5 = st.columns(5)
        with fc1:
            all_objects = sorted({v.object_name for v in all_vps if v.object_name})
            sel_obj = st.selectbox("标的", ["全部"] + all_objects, key="vl_obj")
        with fc2:
            all_markets = sorted({v.market_name for v in all_vps if v.market_name})
            sel_mkt = st.selectbox("市场", ["全部"] + all_markets, key="vl_mkt")
        with fc3:
            sel_horizon = st.selectbox(
                "时间维度",
                ["全部", "short", "medium", "long"],
                format_func=lambda x: "全部" if x == "全部" else _HORIZON_LABELS.get(x, x),
                key="vl_horizon",
            )
        with fc4:
            sel_stance = st.selectbox(
                "方向",
                ["全部", "bullish", "bearish", "neutral", "watch"],
                format_func=lambda x: "全部" if x == "全部" else _STANCE_LABELS.get(x, x),
                key="vl_stance",
            )
        with fc5:
            sel_status = st.selectbox(
                "有效性",
                ["全部", "active", "watch", "outdated", "invalid"],
                format_func=lambda x: "全部" if x == "全部" else _VALIDITY_LABELS.get(x, x),
                key="vl_status",
            )
        search_kw = st.text_input("关键词搜索（标题/结论）", key="vl_search",
                                  placeholder="如：降价压力 / 港股流动性")

    # ── 筛选逻辑 ─────────────────────────────────────────
    filtered = all_vps
    if sel_obj != "全部":
        filtered = [v for v in filtered if v.object_name == sel_obj]
    if sel_mkt != "全部":
        filtered = [v for v in filtered if v.market_name == sel_mkt]
    if sel_horizon != "全部":
        filtered = [v for v in filtered if v.horizon == sel_horizon]
    if sel_stance != "全部":
        filtered = [v for v in filtered if v.stance == sel_stance]
    if sel_status != "全部":
        filtered = [v for v in filtered if v.validity_status == sel_status]
    if search_kw.strip():
        kw = search_kw.lower()
        filtered = [
            v for v in filtered
            if kw in (v.title or "").lower()
            or kw in (v.thesis or "").lower()
            or kw in (v.action_suggestion or "").lower()
        ]

    st.markdown(f"共 **{len(filtered)}** 条观点")

    # ── 观点列表 ──────────────────────────────────────────
    for vp in filtered:
        _render_viewpoint_row(vp)


def _render_viewpoint_row(vp: ResearchViewpoint) -> None:
    stance_label  = _STANCE_LABELS.get(vp.stance or "", "—")
    horizon_label = _HORIZON_LABELS.get(vp.horizon or "", "—")
    validity_label = _VALIDITY_LABELS.get(vp.validity_status or "", "—")
    approval_label = _APPROVAL_LABELS.get(vp.user_approval_level or "", "—")
    updated = vp.updated_at.strftime("%Y-%m-%d") if vp.updated_at else "—"

    with st.expander(
        f"{stance_label}  |  **{vp.title}**  |  {vp.object_name or '—'}  "
        f"|  {horizon_label}  |  {validity_label}  ({updated})",
        expanded=False,
    ):
        col_main, col_meta = st.columns([3, 1])

        with col_main:
            st.markdown(f"**核心结论**\n\n{vp.thesis or '—'}")
            c1, c2 = st.columns(2)
            with c1:
                sp = _parse_json_list(vp.supporting_points)
                st.markdown("**支撑逻辑**\n\n" + ("\n".join(f"• {s}" for s in sp) if sp else "—"))
            with c2:
                op = _parse_json_list(vp.opposing_points)
                if not op and vp.opposing_points:
                    op = [vp.opposing_points]
                st.markdown("**对立逻辑**\n\n" + ("\n".join(f"• {o}" for o in op) if op else "—"))
            st.markdown(f"**操作建议**\n\n{vp.action_suggestion or '—'}")
            st.markdown(f"**失效条件**\n\n{vp.invalidation_conditions or '—'}")
            st.markdown(f"**观察指标**\n\n{_fmt_list(vp.key_metrics)}")

        with col_meta:
            st.markdown(f"**认可程度**\n\n{approval_label}")
            st.markdown(f"**市场**\n\n{vp.market_name or '—'}")
            st.markdown(f"**标签**\n\n{_tags_to_str(vp.topic_tags)}")
            st.markdown(f"**风险**\n\n{_fmt_list(vp.risks)}")

            # 有效性快速修改
            new_status = st.selectbox(
                "修改有效性",
                list(_VALIDITY_LABELS.keys()),
                index=list(_VALIDITY_LABELS.keys()).index(vp.validity_status or "active"),
                format_func=lambda x: _VALIDITY_LABELS[x],
                key=f"vp_status_{vp.id}",
            )
            if new_status != vp.validity_status:
                if st.button("更新", key=f"vp_update_{vp.id}", use_container_width=True):
                    _update_viewpoint_status(vp.id, new_status)
                    st.success("已更新")


def _update_viewpoint_status(vp_id: int, new_status: str) -> None:
    session = get_session()
    try:
        vp = session.query(ResearchViewpoint).get(vp_id)
        if vp:
            vp.validity_status = new_status
            vp.updated_at = datetime.now()
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


# ──────────────────────────────────────────────────────────
# Section 4：检索测试
# ──────────────────────────────────────────────────────────

def _render_retrieval() -> None:
    st.subheader("投研观点检索")
    st.caption(
        "输入一句自然语言查询，系统按「标的匹配 > 标签匹配 > 关键词匹配 > 新鲜度 > 认可度」"
        "综合排序，召回最相关的投研观点。"
    )

    col_q, col_obj, col_mkt = st.columns([3, 1, 1])
    with col_q:
        query = st.text_input(
            "查询语句",
            key="ret_query",
            placeholder="如：美团现在适不适合加仓 / 港股流动性风险",
        )
    with col_obj:
        obj_filter = st.text_input("精确标的（可空）", key="ret_obj",
                                   placeholder="如：美团")
    with col_mkt:
        mkt_filter = st.text_input("市场（可空）", key="ret_mkt",
                                   placeholder="如：港股")

    col_k, col_inactive = st.columns([1, 2])
    with col_k:
        top_k = st.slider("最多返回条数", 1, 10, 5, key="ret_topk")
    with col_inactive:
        include_inactive = st.checkbox("包含 outdated/invalid 观点", key="ret_inactive")

    run = st.button("🔍 检索", type="primary", use_container_width=True, key="ret_run")

    if run:
        if not query.strip() and not obj_filter.strip():
            st.warning("请至少输入查询语句或标的名称")
            return

        results = retrieve_research_context(
            query=query.strip(),
            object_name=obj_filter.strip() or None,
            market_name=mkt_filter.strip() or None,
            top_k=top_k,
            include_inactive=include_inactive,
        )

        if not results:
            st.info("未找到相关观点。请先在观点库中录入投研观点。")
            return

        st.markdown(f"**召回 {len(results)} 条观点：**")
        for i, r in enumerate(results, 1):
            stance = _STANCE_LABELS.get(r.get("stance") or "", "—")
            horizon = _HORIZON_LABELS.get(r.get("horizon") or "", "—")
            validity = _VALIDITY_LABELS.get(r.get("validity_status") or "", "—")
            approval = _APPROVAL_LABELS.get(r.get("user_approval_level") or "", "—")
            score = r.get("_score", 0)

            with st.expander(
                f"#{i}  {stance}  |  **{r['title']}**  |  {r.get('object_name', '—')}  "
                f"|  {horizon}  |  {validity}  (得分 {score})",
                expanded=(i == 1),
            ):
                st.markdown(f"**核心结论**\n\n{r.get('thesis') or '—'}")
                st.markdown(f"**操作建议**\n\n{r.get('action_suggestion') or '—'}")
                sp = r.get("supporting_points") or []
                if sp:
                    st.markdown("**支撑逻辑**\n\n" + "\n".join(f"• {s}" for s in sp))
                risks = r.get("risks") or []
                if risks:
                    st.markdown("**风险**\n\n" + "\n".join(f"• {s}" for s in risks))
                st.markdown(f"**失效条件**\n\n{r.get('invalidation_conditions') or '—'}")
                st.caption(
                    f"认可度：{approval} · 标签：{', '.join(r.get('topic_tags', [])) or '—'} "
                    f"· 更新：{r.get('updated_at', '—')}"
                )


# ──────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────

def render() -> None:
    # 确保投研模块的表已创建（init_db 是幂等的）
    init_db()

    st.title("投研观点")
    st.caption("资料导入 → AI 提炼 → 观点审核 → 观点库 → 决策检索")

    tab_import, tab_viewpoints, tab_retrieval = st.tabs(
        ["📥 资料导入", "📚 观点库", "🔍 决策检索"]
    )

    with tab_import:
        _render_import()

    with tab_viewpoints:
        _render_viewpoints()

    with tab_retrieval:
        _render_retrieval()
