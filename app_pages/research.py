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
from app.ai_advisor import generate_research_card
from app.research import retrieve_research_context, _parse_json_list


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
# 导航
# ──────────────────────────────────────────────────────────

def _research_nav() -> str:
    if "research_nav" not in st.session_state:
        st.session_state["research_nav"] = _NAV_ITEMS[0]

    _sc = getattr(st, "segmented_control", None)
    if _sc is not None:
        try:
            result = _sc(
                "投研导航", options=_NAV_ITEMS, key="research_nav",
                label_visibility="collapsed", use_container_width=True,
            )
        except TypeError:
            result = _sc(
                "投研导航", options=_NAV_ITEMS, key="research_nav",
                label_visibility="collapsed",
            )
        return result if result is not None else _NAV_ITEMS[0]

    cols = st.columns(len(_NAV_ITEMS))
    for col, item in zip(cols, _NAV_ITEMS):
        with col:
            if st.button(
                item, use_container_width=True,
                type="primary" if st.session_state.get("research_nav") == item else "secondary",
                key=f"_rnav_{item}",
            ):
                st.session_state["research_nav"] = item
    return st.session_state.get("research_nav", _NAV_ITEMS[0])


# ──────────────────────────────────────────────────────────
# Section 1：资料导入
# ──────────────────────────────────────────────────────────

def _render_import() -> None:
    st.subheader("新增研究资料")

    source_type = st.radio(
        "资料类型",
        ["text", "markdown", "link", "pdf"],
        format_func=lambda x: {
            "text": "📝 纯文本粘贴",
            "markdown": "📄 Markdown",
            "link": "🔗 链接（公众号/博客）",
            "pdf": "📑 PDF 上传",
        }[x],
        horizontal=True,
        key="ri_source_type",
    )

    st.divider()

    col_meta, col_content = st.columns([1, 2], gap="large")

    with col_meta:
        st.markdown("**基本信息**")
        title = st.text_input("资料标题 *", key="ri_title",
                              placeholder="如：美团2025年投资价值深度报告")
        object_name = st.text_input("标的名称", key="ri_object",
                                    placeholder="如：美团 / 拼多多 / 宏观流动性")
        market_name = st.selectbox(
            "市场", ["", "港股", "美股", "A股", "宏观", "行业", "其他"],
            key="ri_market",
        )
        author = st.text_input("作者/来源", key="ri_author",
                               placeholder="如：国泰君安、X用户 / 自研")
        publish_time = st.text_input("发布时间（可模糊）", key="ri_publish",
                                     placeholder="如：2025-03 / 2025-Q1 / 今天")
        tags_input = st.text_input("标签（逗号分隔）", key="ri_tags",
                                   placeholder="如：智能驾驶, 港股, 高增长")
        notes = st.text_area("备注", height=60, key="ri_notes",
                             placeholder="可选，记录你对这份资料的第一印象")

    with col_content:
        st.markdown("**资料内容**")

        raw_content = ""
        source_url = ""

        if source_type == "text":
            raw_content = st.text_area(
                "粘贴资料正文",
                height=300,
                key="ri_content",
                placeholder=(
                    "把研报摘要、博主分析、会议纪要等原文粘贴到这里。\n"
                    "无需全文，关键论据 + 结论段落即可（建议 300-2000 字）。"
                ),
                label_visibility="collapsed",
            )
        elif source_type == "markdown":
            uploaded_md = st.file_uploader(
                "上传 Markdown 文件", type=["md", "markdown", "txt"],
                key="ri_md",
            )
            # 关键：必须在 st.text_area(key="ri_md_content") 实例化【之前】写入 session_state。
            # Streamlit 规则：widget 创建后不允许再修改其绑定的 session_state key，
            # 否则抛出 StreamlitAPIException。
            if uploaded_md is not None:
                # 仅在新文件上传时（文件名变化）才更新，避免重复 read()
                if st.session_state.get("_ri_md_cache_name") != uploaded_md.name:
                    content = uploaded_md.read().decode("utf-8", errors="replace")
                    st.session_state["_ri_md_cache"] = content
                    st.session_state["_ri_md_cache_name"] = uploaded_md.name
                    # 在 widget 实例化前写入，text_area 渲染时会直接使用此值
                    st.session_state["ri_md_content"] = content
            raw_content = st.text_area(
                "文件内容（可编辑）" if st.session_state.get("ri_md_content") else "或直接粘贴 Markdown 正文",
                height=300,
                key="ri_md_content",
                placeholder="也可以不上传文件，直接把 Markdown 内容粘贴到这里。",
            )
        elif source_type == "link":
            source_url = st.text_input(
                "链接地址", key="ri_url",
                placeholder="https://mp.weixin.qq.com/s/...",
            )
            raw_content = st.text_area(
                "手动粘贴正文（目前不支持自动抓取，请手动复制核心内容）",
                height=250, key="ri_content_link",
                label_visibility="visible",
            )
            st.caption(
                "🚧 公众号/博客自动抓取功能规划中，当前请手动粘贴正文。"
            )
        elif source_type == "pdf":
            uploaded_pdf = st.file_uploader(
                "上传 PDF", type=["pdf"], key="ri_pdf",
            )
            st.caption(
                "🚧 PDF 自动解析功能规划中。\n"
                "当前请在下方手动粘贴关键段落（摘要、结论、风险提示部分即可）。"
            )
            raw_content = st.text_area(
                "手动粘贴 PDF 关键段落",
                height=220, key="ri_content_pdf",
                label_visibility="visible",
            )
            if uploaded_pdf:
                source_url = uploaded_pdf.name  # 先存文件名作为 source_url

        st.write("")  # 间距
        col_save, col_parse = st.columns(2)
        with col_save:
            save_only = st.button(
                "💾 仅保存资料（不解析）",
                use_container_width=True, key="ri_save_only",
            )
        with col_parse:
            save_and_parse = st.button(
                "⚡ 保存并立即 AI 解析",
                type="primary", use_container_width=True, key="ri_save_parse",
            )

    # ── 处理提交 ────────────────────────────────────────
    if save_only or save_and_parse:
        if not title.strip():
            st.error("请填写资料标题")
            return
        if not raw_content.strip():
            st.error("请粘贴资料正文（AI 解析需要内容）")
            return

        # P2：超长文本提前提示（截断发生在 ai_advisor.py[:4000]）
        if len(raw_content.strip()) > 4000:
            st.warning(
                f"⚠️ 资料正文共 {len(raw_content.strip())} 字，超过 4000 字上限，"
                "AI 解析将仅处理前 4000 字，后半部分信息不会被提炼。"
            )

        session = get_session()
        try:
            # P1：标题查重，存在同名资料时给出警告并中止，避免产生冗余数据
            dup = session.query(ResearchDocument).filter_by(title=title.strip()).first()
            if dup:
                st.warning(
                    f"⚠️ 已存在同名资料「{title.strip()}」（上传于 {dup.uploaded_at.strftime('%m-%d %H:%M') if dup.uploaded_at else '未知时间'}），"
                    "请确认是否重复导入。如需重新解析，请在下方「已导入资料」中选择该资料触发解析。"
                )
                session.close()
                return

            doc = ResearchDocument(
                title=title.strip(),
                source_type=source_type,
                source_url=source_url.strip() or None,
                raw_content=raw_content.strip(),
                author=author.strip() or None,
                publish_time=publish_time.strip() or None,
                object_name=object_name.strip() or None,
                market_name=market_name or None,
                tags=_tags_input_to_json(tags_input) if tags_input.strip() else None,
                notes=notes.strip() or None,
                parse_status="saved_only" if save_only else "pending",
            )
            session.add(doc)
            session.commit()
            doc_id = doc.id

            if save_and_parse:
                # 立即调用 AI 解析
                with st.spinner("AI 正在提炼投研观点，请稍候…"):
                    card_data = generate_research_card(
                        raw_content=raw_content.strip(),
                        title=title.strip(),
                        object_name=object_name.strip(),
                        market_name=market_name,
                    )

                if "error" in card_data:
                    st.error(f"AI 解析失败：{card_data['error']}")
                    doc.parse_status = "saved_only"
                else:
                    def _jl(key):
                        v = card_data.get(key)
                        return json.dumps(v, ensure_ascii=False) if isinstance(v, list) else None

                    card = ResearchCard(
                        document_id=doc_id,
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
                    doc.parse_status = "parsed"
                    session.commit()
                    # 用独立中转变量传递跳转意图，render() 顶部在 widget 实例化前统一应用，
                    # 避免在 segmented_control(key="research_nav") 创建后修改其绑定的 state
                    st.toast("✅ 资料已保存，AI 解析完成！", icon="✅")
                    st.session_state["_research_nav_target"] = _NAV_ITEMS[1]
                    st.rerun()
            else:
                session.commit()
                st.success("✅ 资料已保存。可前往「候选观点卡」手动触发 AI 解析。")

        except Exception as e:
            session.rollback()
            st.error(f"保存失败：{str(e)}")
        finally:
            session.close()

    # ── 现有资料列表 ─────────────────────────────────────
    st.divider()
    st.subheader("已导入资料")
    _render_document_list()


def _render_document_list() -> None:
    session = get_session()
    try:
        docs = session.query(ResearchDocument).order_by(
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
        st.success("✅ 解析完成！请前往「候选观点卡」查看。")
        st.session_state["_research_nav_target"] = _NAV_ITEMS[1]
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
    if save_only:
        _set_doc_status(doc.id, "saved_only")
        st.success("已标记为「仅保存」")
    if discard:
        _set_doc_status(doc.id, "discarded")
        st.info("已丢弃该资料")

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
        st.success("✅ 已录入正式观点库！")
    except Exception as e:
        session.rollback()
        st.error(f"录入失败：{str(e)}")
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
    st.caption("资料导入 → AI 提炼 → 候选卡审核 → 观点库 → 决策检索")

    # 处理程序触发的 Tab 跳转：必须在 _research_nav() 实例化 segmented_control 前应用，
    # 避免在 widget 创建后修改其绑定的 session_state key（会触发 Streamlit 警告/异常）。
    if "_research_nav_target" in st.session_state:
        st.session_state["research_nav"] = st.session_state.pop("_research_nav_target")

    active_nav = _research_nav()

    # 全量 elif 路由，避免 else 兜底导致任意非法值都渲染检索页
    if active_nav == _NAV_ITEMS[0]:
        _render_import()
    elif active_nav == _NAV_ITEMS[1]:
        _render_cards()
    elif active_nav == _NAV_ITEMS[2]:
        _render_viewpoints()
    elif active_nav == _NAV_ITEMS[3]:
        _render_retrieval()
    else:
        # 兜底：session_state 被外部写入了非法值时，重置到首页
        st.session_state["research_nav"] = _NAV_ITEMS[0]
        st.rerun()
