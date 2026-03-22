"""
WealthPilot — 投资决策页面 (strategy.py)

Tab 1: 投资决策引擎  ← 新增（PRD V2.0）
Tab 2: 策略设定     ← 原有功能保留
"""

import streamlit as st
from app.models import Portfolio, get_session
from app.state import portfolio_id


def render():
    st.title("💡 投资决策")

    tab_engine, tab_strategy = st.tabs(["🧠 决策引擎", "⚙️ 策略设定"])

    with tab_engine:
        _render_decision_engine()

    with tab_strategy:
        _render_strategy_settings()


# ══════════════════════════════════════════════════════════════════════════════
# Tab 1: 投资决策引擎
# ══════════════════════════════════════════════════════════════════════════════

def _render_decision_engine():
    """PRD V2.0 投资决策引擎主界面。"""
    import os
    from decision_engine import decision_flow
    from decision_engine.decision_flow import FlowStage

    # ── API Key 检查 ──────────────────────────────────────────────────────────
    if not os.environ.get("ANTHROPIC_API_KEY"):
        st.warning(
            "⚙️ 未检测到 `ANTHROPIC_API_KEY`。\n\n"
            "请在终端运行：\n```\nexport ANTHROPIC_API_KEY='sk-ant-your-key'\n```\n"
            "然后重启 Streamlit。",
            icon="🔑"
        )
        st.stop()

    # ── ① 输入区 ──────────────────────────────────────────────────────────────
    st.markdown("#### 📝 输入决策需求")
    st.caption("用自然语言描述您的投资想法，系统将逐步分析并给出结构化建议。")

    # 示例快捷按钮
    example_col1, example_col2, example_col3 = st.columns(3)
    example_input = ""
    with example_col1:
        if st.button("💡 理想汽车发布会前加仓吗？", use_container_width=True):
            example_input = "理想汽车下周有新车发布会，我想在发布会前加仓，合适吗？"
    with example_col2:
        if st.button("💡 Meta仓位太重，要减吗？", use_container_width=True):
            example_input = "我的Meta仓位感觉有点重了，要不要减一部分？"
    with example_col3:
        if st.button("💡 现在可以建仓苹果吗？", use_container_width=True):
            example_input = "我想买入苹果，当前时机合适吗？"

    user_input = st.text_area(
        label="投资决策输入",
        value=example_input,
        placeholder="例如：理想汽车发布会后我想加仓，当前仓位合适吗？",
        height=80,
        label_visibility="collapsed",
    )

    analyze_btn = st.button(
        "🔍 开始分析", type="primary", use_container_width=True,
        disabled=not user_input.strip()
    )

    if not analyze_btn:
        _render_placeholder()
        return

    # ── 执行决策流程 ──────────────────────────────────────────────────────────
    with st.spinner("正在分析中，请稍候..."):
        result = decision_flow.run(user_input.strip(), pid=portfolio_id)

    st.divider()

    # ── ② 意图解析展示 ────────────────────────────────────────────────────────
    if result.intent:
        _render_intent(result.intent)

    # 流程中断：显示原因（澄清问题 / 前置校验失败）
    if result.was_aborted:
        if result.pre_check and not result.pre_check.passed:
            st.error(f"⛔ 前置校验未通过：{result.aborted_reason}", icon="⚠️")
        elif result.intent and result.intent.needs_clarification:
            st.info(f"🤔 {result.aborted_reason}", icon="💬")
        else:
            st.error(f"流程中断：{result.aborted_reason}")
        _render_compliance_notice()
        return

    # ── ③ 数据展示 ────────────────────────────────────────────────────────────
    if result.data:
        _render_data_summary(result.data, result.intent)

    # ── ④ 规则校验 ────────────────────────────────────────────────────────────
    if result.rules:
        _render_rule_check(result.rules)

    # ── ⑤ 信号层 ─────────────────────────────────────────────────────────────
    if result.signals:
        _render_signals(result.signals)

    # ── ⑥ AI 推理过程（可折叠）──────────────────────────────────────────────
    if result.llm and not result.llm.is_fallback:
        with st.expander("🔬 AI 推理过程", expanded=False):
            st.markdown("**推理依据：**")
            for item in result.llm.reasoning:
                st.markdown(f"- {item}")

    # ── ⑦ 最终结论 ────────────────────────────────────────────────────────────
    if result.llm:
        _render_final_decision(result.llm)

    # ── ⑧ 合规提示（必须）────────────────────────────────────────────────────
    _render_compliance_notice()


# ── 渲染子函数 ─────────────────────────────────────────────────────────────────

def _render_placeholder():
    """未输入时展示说明卡片。"""
    st.markdown("""
    <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:12px;padding:24px;margin-top:16px;color:#64748B;">
    <h4 style="color:#1E3A5F;margin-top:0">🧠 投资决策引擎</h4>
    <p>基于 PRD V2.0 实现的结构化决策流程：</p>
    <ol>
      <li><b>意图解析</b>：理解您的投资需求（Claude AI）</li>
      <li><b>数据加载</b>：读取持仓、纪律、投研观点</li>
      <li><b>前置校验</b>：确认数据完备性</li>
      <li><b>规则校验</b>：检查是否违反投资纪律</li>
      <li><b>信号生成</b>：仓位 / 事件 / 基本面 / 情绪 四维信号</li>
      <li><b>AI 推理</b>：Claude 综合给出 BUY / HOLD / SELL 建议</li>
    </ol>
    </div>
    """, unsafe_allow_html=True)


def _render_intent(intent):
    """② 意图解析展示。"""
    st.markdown("#### 🎯 意图解析")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("标的", intent.asset or "未识别")
    c2.metric("操作类型", intent.action_type)
    c3.metric("时间维度", intent.time_horizon)
    c4.metric("置信度", f"{intent.confidence_score:.0%}")
    if intent.trigger:
        st.caption(f"触发事件：{intent.trigger}")


def _render_data_summary(data, intent):
    """③ 数据展示：用户画像 / 持仓 / 投研。"""
    with st.expander("📊 数据详情（用户画像 / 持仓 / 投研观点）", expanded=False):
        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown("**👤 用户画像**")
            st.markdown(f"- 风险偏好：{data.profile.risk_level}")
            st.markdown(f"- 投资目标：{data.profile.goal}")
            st.markdown(f"- 总资产：¥{data.total_assets:,.0f}")

            st.markdown("**📌 目标持仓**")
            if data.target_position:
                tp = data.target_position
                st.markdown(f"- 当前仓位：{tp.weight:.1%}")
                st.markdown(f"- 市值：¥{tp.market_value_cny:,.0f}")
                st.markdown(f"- 收益率：{tp.profit_loss_rate:.1%}")
            else:
                st.caption("当前未持有该标的")

        with col_b:
            st.markdown("**📖 投研观点**")
            if data.research:
                for view in data.research[:3]:
                    st.markdown(f"- {view}")
            else:
                st.caption("暂无投研观点")


def _render_rule_check(rule_result):
    """④ 规则校验展示。"""
    st.markdown("#### 📏 规则校验")

    if rule_result.violation:
        st.error(f"⛔ 规则违规：{rule_result.status_label}", icon="🚫")
    elif rule_result.warning:
        st.warning(f"⚠️ {rule_result.status_label}")
    else:
        st.success(f"✅ {rule_result.status_label}")

    # 详细规则明细
    for detail in rule_result.rule_details:
        st.caption(detail)


def _render_signals(signals):
    """⑤ 信号层展示（重点模块）。"""
    st.markdown("#### 📡 信号层分析")

    c1, c2, c3, c4 = st.columns(4)

    # 仓位信号
    pos_color = {"偏高": "🟠", "合理": "🟢", "偏低": "🔵"}.get(signals.position_signal, "⚪")
    c1.metric("仓位信号", f"{pos_color} {signals.position_signal}")

    # 事件信号
    unc_icon = {"高": "⚠️", "中": "🔔", "低": "✅"}.get(signals.event_signal.uncertainty, "❓")
    c2.metric(
        "事件信号",
        f"{unc_icon} 不确定性{signals.event_signal.uncertainty}",
        delta=f"方向：{signals.event_signal.direction}"
    )

    # 基本面信号
    fund_icon = {"正面": "📈", "负面": "📉", "中性": "➡️", "N/A": "❓"}.get(
        signals.fundamental_signal, "➡️"
    )
    c3.metric("基本面信号", f"{fund_icon} {signals.fundamental_signal}")

    # 情绪信号
    c4.metric("情绪信号", f"➡️ {signals.sentiment_signal}")


def _render_final_decision(llm_result):
    """⑦ 最终决策结论。"""
    st.markdown("---")
    st.markdown("#### 🏁 最终结论")

    # 决策标签
    decision_colors = {"BUY": "#059669", "HOLD": "#D97706", "SELL": "#DC2626"}
    color = decision_colors.get(llm_result.decision, "#64748B")

    if llm_result.is_fallback:
        st.warning(f"⚠️ AI 推理不可用：{llm_result.error}", icon="🤖")

    st.markdown(
        f'<div style="text-align:center;padding:20px 0;">'
        f'<span style="font-size:48px">{llm_result.decision_emoji}</span><br>'
        f'<span style="font-size:28px;font-weight:700;color:{color}">'
        f'{llm_result.decision_cn}</span>'
        f'</div>',
        unsafe_allow_html=True
    )

    # 策略建议
    if llm_result.strategy:
        st.markdown("**📋 操作策略**")
        for s in llm_result.strategy:
            st.markdown(f"- {s}")

    # 风险提示
    if llm_result.risk:
        st.markdown("**⚠️ 风险提示**")
        for r in llm_result.risk:
            st.markdown(f"- {r}")


def _render_compliance_notice():
    """⑧ 合规声明（PRD 要求必须展示）。"""
    st.markdown("---")
    st.caption(
        "⚖️ **免责声明**：本系统输出仅供参考，不构成投资建议。"
        "投资有风险，入市需谨慎，最终决策请结合自身情况独立判断。"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Tab 2: 策略设定（原有功能完整保留）
# ══════════════════════════════════════════════════════════════════════════════

def _render_strategy_settings():
    """原有策略设定表单，保持不变。"""
    st.write("设定您的目标资产配置和投资纪律约束。系统将基于这些设定进行偏离检测和风险告警。")

    session = get_session()
    try:
        p = session.query(Portfolio).filter_by(id=portfolio_id).first()

        with st.form("strategy_form"):
            st.subheader("资产配置框架")
            st.caption("设置每类资产的占比区间（下限 ~ 上限）。填写 0 ~ 100 表示不设约束。")

            st.markdown("**权益**")
            col1, col2 = st.columns(2)
            with col1:
                min_equity = st.number_input("权益下限 (%)", 0.0, 100.0, float(p.min_equity_pct), 5.0)
            with col2:
                max_equity = st.number_input("权益上限 (%)", 0.0, 100.0, float(p.max_equity_pct), 5.0)

            st.markdown("**固收**")
            col3, col4 = st.columns(2)
            with col3:
                min_fi = st.number_input("固收下限 (%)", 0.0, 100.0, float(p.min_fixed_income_pct), 5.0)
            with col4:
                max_fi = st.number_input("固收上限 (%)", 0.0, 100.0, float(p.max_fixed_income_pct), 5.0)

            st.markdown("**货币**")
            col5, col6 = st.columns(2)
            with col5:
                min_cash = st.number_input("货币下限 (%)", 0.0, 100.0, float(p.min_cash_pct), 5.0)
            with col6:
                max_cash = st.number_input("货币上限 (%)", 0.0, 100.0, float(p.max_cash_pct), 5.0)

            st.markdown("**另类**")
            col7, col8 = st.columns(2)
            with col7:
                min_alt = st.number_input("另类下限 (%)", 0.0, 100.0, float(p.min_alternative_pct), 5.0)
            with col8:
                max_alt = st.number_input("另类上限 (%)", 0.0, 100.0, float(p.max_alternative_pct), 5.0)

            st.divider()
            st.subheader("投资纪律约束")

            col9, col10 = st.columns(2)
            with col9:
                max_single = st.number_input("单一持仓上限 (%)", 5.0, 50.0, float(p.max_single_stock_pct), 5.0)
            with col10:
                max_leverage = st.number_input("最大杠杆率 (%)", 0.0, 100.0, float(p.max_leverage_ratio), 5.0)

            submitted = st.form_submit_button("保存策略", type="primary")

            if submitted:
                errors = []
                for label, mn, mx in [("权益", min_equity, max_equity),
                                       ("固收", min_fi, max_fi),
                                       ("货币", min_cash, max_cash),
                                       ("另类", min_alt, max_alt)]:
                    if mn > mx:
                        errors.append(f"{label}：下限 ({mn}%) 不能大于上限 ({mx}%)")
                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    p.min_equity_pct = min_equity
                    p.max_equity_pct = max_equity
                    p.min_fixed_income_pct = min_fi
                    p.max_fixed_income_pct = max_fi
                    p.min_cash_pct = min_cash
                    p.max_cash_pct = max_cash
                    p.min_alternative_pct = min_alt
                    p.max_alternative_pct = max_alt
                    p.max_single_stock_pct = max_single
                    p.max_leverage_ratio = max_leverage
                    session.commit()
                    st.success("策略已保存！")
    finally:
        session.close()
