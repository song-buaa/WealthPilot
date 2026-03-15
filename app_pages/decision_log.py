"""
WealthPilot - 决策日志页面
记录和查看投资决策历史。
"""

import streamlit as st
import pandas as pd
from datetime import datetime

from app.models import DecisionLog, get_session
from app.state import portfolio_id

TRIGGER_OPTIONS = ["策略偏离", "纪律触发", "事件驱动", "风险暴露"]
STATUS_OPTIONS = ["待执行", "已执行", "已取消"]


def render():
    st.title("📋 决策日志")

    _render_add_form()
    st.divider()
    _render_log_list()


def _render_add_form():
    with st.expander("➕ 记录新决策", expanded=False):
        with st.form("add_decision_form", clear_on_submit=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                title = st.text_input("决策标题 *", placeholder="例：降低权益仓位至60%")
            with col2:
                trigger = st.selectbox("决策动因 *", TRIGGER_OPTIONS)

            context = st.text_area("市场背景 / 持仓快照", placeholder="当时的市场环境、持仓情况等", height=80)
            reasoning = st.text_area("分析逻辑", placeholder="为什么做出这个决策", height=80)
            conclusion = st.text_area("决策结论 *", placeholder="具体操作计划", height=80)

            submitted = st.form_submit_button("保存决策", type="primary")

        if submitted:
            if not title.strip() or not conclusion.strip():
                st.error("标题和决策结论为必填项。")
            else:
                _save_decision(title.strip(), trigger, context, reasoning, conclusion.strip())
                st.success("决策已记录！")
                st.rerun()


def _save_decision(title, trigger, context, reasoning, conclusion):
    session = get_session()
    try:
        log = DecisionLog(
            portfolio_id=portfolio_id,
            title=title,
            trigger=trigger,
            context=context or None,
            reasoning=reasoning or None,
            conclusion=conclusion,
            status="待执行",
        )
        session.add(log)
        session.commit()
    finally:
        session.close()


def _render_log_list():
    session = get_session()
    try:
        logs = (
            session.query(DecisionLog)
            .filter_by(portfolio_id=portfolio_id)
            .order_by(DecisionLog.created_at.desc())
            .all()
        )
        # detach from session
        log_data = [
            {
                "id": l.id,
                "title": l.title,
                "trigger": l.trigger,
                "status": l.status,
                "conclusion": l.conclusion,
                "context": l.context,
                "reasoning": l.reasoning,
                "created_at": l.created_at,
                "executed_at": l.executed_at,
            }
            for l in logs
        ]
    finally:
        session.close()

    if not log_data:
        st.info("暂无决策记录。点击上方「记录新决策」开始记录。")
        return

    st.subheader(f"历史记录（共 {len(log_data)} 条）")

    # Summary table
    summary = pd.DataFrame([
        {
            "ID": d["id"],
            "标题": d["title"],
            "动因": d["trigger"],
            "状态": d["status"],
            "创建时间": d["created_at"].strftime("%Y-%m-%d %H:%M") if d["created_at"] else "-",
        }
        for d in log_data
    ])
    st.dataframe(summary, use_container_width=True, hide_index=True)

    st.divider()

    # Detail + status update per record
    for d in log_data:
        status_icon = {"待执行": "🟡", "已执行": "🟢", "已取消": "⚫"}.get(d["status"], "⚪")
        with st.expander(f"{status_icon} [{d['trigger']}] {d['title']}", expanded=False):
            col1, col2 = st.columns([2, 1])
            with col1:
                if d["context"]:
                    st.markdown("**市场背景**")
                    st.write(d["context"])
                if d["reasoning"]:
                    st.markdown("**分析逻辑**")
                    st.write(d["reasoning"])
                st.markdown("**决策结论**")
                st.write(d["conclusion"])
            with col2:
                st.caption(f"创建：{d['created_at'].strftime('%Y-%m-%d %H:%M') if d['created_at'] else '-'}")
                if d["executed_at"]:
                    st.caption(f"执行：{d['executed_at'].strftime('%Y-%m-%d %H:%M')}")

                new_status = st.selectbox(
                    "更新状态",
                    STATUS_OPTIONS,
                    index=STATUS_OPTIONS.index(d["status"]) if d["status"] in STATUS_OPTIONS else 0,
                    key=f"status_{d['id']}",
                )
                if st.button("更新", key=f"update_{d['id']}"):
                    _update_status(d["id"], new_status)
                    st.rerun()


def _update_status(log_id: int, new_status: str):
    session = get_session()
    try:
        log = session.query(DecisionLog).filter_by(id=log_id).first()
        if log:
            log.status = new_status
            if new_status == "已执行" and not log.executed_at:
                log.executed_at = datetime.now()
            session.commit()
    finally:
        session.close()
