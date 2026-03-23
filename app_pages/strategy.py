"""
WealthPilot — 投资决策页面 (strategy.py) — V3.1

V3.1 核心变化：
- 多轮对话：每次用户输入走完整链路（intent→data→rule→signal→LLM），生成新 decision_id
- 左右布局：左侧 Chat 面板，右侧 Explain Panel
- 删除"策略设定" Tab，规则数据统一来自"投资纪律"模块
- Explain Panel 绑定 decision_id，点击"查看决策逻辑 📊"后切换

状态管理（session_state）：
    chat_history          — list[dict]：对话记录，每条包含 role/content/intent/decision_id 等
    decision_map          — dict[str, DecisionResult]：decision_id → 完整决策结果
    current_decision_id   — str | None：当前 Explain Panel 展示的 decision
    de_pending_input      — str | None：示例按钮触发的待处理输入
"""

import os

import streamlit as st
from app.state import portfolio_id


# ══════════════════════════════════════════════════════════════════════════════
# 状态初始化
# ══════════════════════════════════════════════════════════════════════════════

def _init_session_state():
    defaults = {
        "chat_history": [],
        "decision_map": {},
        "current_decision_id": None,
        "de_pending_input": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


# ══════════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════════

def render():
    st.title("💡 投资决策")
    _init_session_state()

    # 处理示例按钮触发的待处理输入（必须在渲染 UI 之前）
    _handle_pending_input()

    left_col, right_col = st.columns([6, 4], gap="medium")

    with left_col:
        _render_chat_panel()

    with right_col:
        _render_explain_panel()

    # chat_input 固定在页面底部（st.chat_input 特性）
    _render_chat_input()


# ══════════════════════════════════════════════════════════════════════════════
# 待处理输入（示例按钮 → session_state 中转 → 下一次 rerun 处理）
# ══════════════════════════════════════════════════════════════════════════════

def _handle_pending_input():
    pending = st.session_state.get("de_pending_input")
    if pending:
        st.session_state["de_pending_input"] = None
        with st.spinner("正在分析，请稍候..."):
            _process_submit(pending)
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# 左侧：Chat 面板
# ══════════════════════════════════════════════════════════════════════════════

def _render_chat_panel():
    col_title, col_clear = st.columns([4, 1])
    with col_title:
        st.markdown("### 💬 对话")
    with col_clear:
        if st.button("清空", use_container_width=True, type="secondary",
                     help="清空所有对话记录和决策历史"):
            st.session_state["chat_history"] = []
            st.session_state["decision_map"] = {}
            st.session_state["current_decision_id"] = None
            st.rerun()

    history = st.session_state["chat_history"]

    if not history:
        _render_empty_chat()
        return

    # 渲染对话历史
    for idx, msg in enumerate(history):
        if msg["role"] == "user":
            with st.chat_message("user"):
                st.markdown(msg["content"])
        else:
            with st.chat_message("assistant"):
                st.markdown(msg["content"])
                # 只有 investment_decision 完整流程才有 decision_id
                if msg.get("decision_id"):
                    btn_key = f"view_{msg['decision_id']}_{idx}"
                    if st.button("查看决策逻辑 📊", key=btn_key, type="secondary"):
                        st.session_state["current_decision_id"] = msg["decision_id"]
                        st.rerun()


def _render_empty_chat():
    """对话为空时展示欢迎卡片 + 示例按钮。"""
    st.markdown("""
    <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:12px;
                padding:20px;margin:8px 0;color:#64748B;">
    <h4 style="color:#1E3A5F;margin-top:0">🧠 投资决策助手</h4>
    <p style="margin-bottom:8px">用自然语言描述您的投资想法，系统将完整分析并给出结构化建议：</p>
    <p style="margin:0;font-size:13px">
      意图解析 → 数据加载 → 规则校验 → 信号分析 → AI 推理 → BUY/HOLD/SELL
    </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("**💡 快速体验：**")
    ex1, ex2, ex3 = st.columns(3)
    with ex1:
        if st.button("理想汽车发布会前加仓吗？", use_container_width=True):
            st.session_state["de_pending_input"] = (
                "理想汽车下周有新车发布会，我想在发布会前加仓，合适吗？"
            )
            st.rerun()
    with ex2:
        if st.button("Meta仓位太重，要减吗？", use_container_width=True):
            st.session_state["de_pending_input"] = "我的Meta仓位感觉有点重了，要不要减一部分？"
            st.rerun()
    with ex3:
        if st.button("现在可以建仓苹果吗？", use_container_width=True):
            st.session_state["de_pending_input"] = "我想买入苹果，当前时机合适吗？"
            st.rerun()

    st.caption("或者直接在底部输入框输入您的投资想法 👇")


# ══════════════════════════════════════════════════════════════════════════════
# Chat Input（固定在页面底部）
# ══════════════════════════════════════════════════════════════════════════════

def _render_chat_input():
    _has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    placeholder = (
        "输入您的投资想法，例如：理想汽车要不要加仓？"
        if _has_api_key
        else "⚙️ 请先配置 ANTHROPIC_API_KEY 后再使用"
    )
    prompt = st.chat_input(placeholder, disabled=not _has_api_key)
    if prompt:
        with st.spinner("正在分析，请稍候..."):
            _process_submit(prompt.strip())
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# 输入处理：执行完整决策链路，更新对话历史
# ══════════════════════════════════════════════════════════════════════════════

def _process_submit(user_input: str):
    """
    执行一轮完整分析：
    1. 从 chat_history 获取 last_intent（最近一条 user 消息的 intent 字段）
    2. 构建 context（最近 1 轮对话）
    3. 调用 decision_flow.run()
    4. 格式化 AI 回复
    5. 更新 chat_history / decision_map / current_decision_id
    """
    from decision_engine import decision_flow

    history = st.session_state["chat_history"]

    # ── 获取 last_intent（从历史最近一条 user 消息的 intent 字段）────────────
    # 注：不从 context 读取，context 仅供 general_chat LLM 使用（PRD 补充3）
    last_intent = None
    for msg in reversed(history):
        if msg["role"] == "user" and msg.get("intent") is not None:
            last_intent = msg["intent"]
            break

    # ── 构建 context：最近 1 轮对话（用于 general_chat）──────────────────────
    context = None
    user_msgs = [m for m in history if m["role"] == "user"]
    ai_msgs = [m for m in history if m["role"] == "assistant"]
    if user_msgs and ai_msgs:
        # 取最后一对 user+assistant 消息
        context = [
            {"role": "user", "content": user_msgs[-1]["content"]},
            {"role": "assistant", "content": ai_msgs[-1]["content"]},
        ]

    # ── 先把用户消息加入历史（intent 稍后回填）───────────────────────────────
    user_msg_idx = len(history)
    history.append({
        "role": "user",
        "content": user_input,
        "intent": None,  # 解析后回填
    })

    # ── 执行决策流程 ─────────────────────────────────────────────────────────
    result = decision_flow.run(
        user_input=user_input,
        last_intent=last_intent,
        context=context,
        pid=portfolio_id,
    )

    # 回填用户消息的 intent
    history[user_msg_idx]["intent"] = result.intent

    # ── 格式化 AI 回复 ───────────────────────────────────────────────────────
    ai_content = _format_ai_response(result)
    intent_type = result.intent.intent_type if result.intent else "investment_decision"

    ai_msg = {
        "role": "assistant",
        "content": ai_content,
        "intent_type": intent_type,
        "decision_id": result.decision_id,  # general_chat 时为 None
    }
    history.append(ai_msg)

    # ── 更新 decision_map 和 current_decision_id ─────────────────────────────
    if result.decision_id:
        st.session_state["decision_map"][result.decision_id] = result
        st.session_state["current_decision_id"] = result.decision_id


def _format_ai_response(result) -> str:
    """将 DecisionResult 渲染为 Chat 消息文本（Markdown）。"""
    intent_type = result.intent.intent_type if result.intent else "investment_decision"

    # general_chat：直接返回 LLM 回复
    if intent_type == "general_chat":
        return result.chat_response or "（无回复）"

    # 假设性问题 / 流程中断
    if result.was_aborted:
        reason = result.aborted_reason or "流程中断，请重新描述您的投资需求。"
        return reason

    # investment_decision 完整结果 → 渲染 【结论】【原因】【建议】【风险】
    if result.is_complete and result.llm:
        llm = result.llm
        lines: list[str] = []

        # 结论
        decision_colors = {"BUY": "🟢", "HOLD": "🟡", "SELL": "🔴"}
        dot = decision_colors.get(llm.decision, "⚪")
        lines.append(f"**【结论】{dot} {llm.decision_cn} {llm.decision_emoji}**")

        if llm.is_fallback:
            lines.append(f"\n⚠️ *{llm.error}*")
        if llm.decision_corrected:
            lines.append(
                f"\n*ℹ️ AI 原始输出「{llm.original_decision}」已自动修正为「{llm.decision_cn}」*"
            )

        # 原因
        if llm.reasoning:
            lines.append("\n**【原因】**")
            lines.extend(f"• {r}" for r in llm.reasoning)

        # 建议
        if llm.strategy:
            lines.append("\n**【建议】**")
            lines.extend(f"• {s}" for s in llm.strategy)

        # 风险
        if llm.risk:
            lines.append("\n**【风险提示】**")
            lines.extend(f"• {r}" for r in llm.risk)

        lines.append(
            "\n---\n*⚖️ 本系统输出仅供参考，不构成投资建议。投资有风险，入市需谨慎。*"
        )
        return "\n".join(lines)

    return "分析完成，但未能生成完整结论，请重试。"


# ══════════════════════════════════════════════════════════════════════════════
# 右侧：Explain Panel
# ══════════════════════════════════════════════════════════════════════════════

def _render_explain_panel():
    st.markdown("### 📊 决策逻辑")

    current_id = st.session_state.get("current_decision_id")
    decision_map = st.session_state.get("decision_map", {})
    history = st.session_state.get("chat_history", [])

    # 无历史 → 欢迎提示
    if not history:
        st.markdown("""
        <div style="background:#F0F9FF;border:1px solid #BAE6FD;border-radius:8px;
                    padding:16px;color:#0369A1;margin-top:8px;">
        <b>👈 开始对话后</b><br>点击对话中的「查看决策逻辑 📊」按钮，
        这里将展示完整的决策分析链路。
        </div>
        """, unsafe_allow_html=True)
        return

    # 最近一条 AI 消息是 general_chat 且没有 decision_id
    last_ai = next((m for m in reversed(history) if m["role"] == "assistant"), None)
    if last_ai and last_ai.get("intent_type") == "general_chat" and not current_id:
        st.info("当前对话非投资决策，无决策链路。")
        return

    # 没有选中的 decision
    if not current_id or current_id not in decision_map:
        st.caption("点击对话中的「查看决策逻辑 📊」按钮查看对应的分析详情。")
        return

    result = decision_map[current_id]

    # ── 展示各阶段 ──────────────────────────────────────────────────────────
    st.caption(f"Decision ID: `{current_id}`")
    st.divider()

    if result.intent:
        _ep_intent(result.intent)

    if result.data:
        _ep_data(result.data, result.intent)

    if result.rules:
        _ep_rules(result.rules)

    if result.signals:
        _ep_signals(result.signals)

    if result.llm:
        _ep_llm(result.llm)


# ── Explain Panel 子渲染函数（适配窄列布局）──────────────────────────────────

def _ep_intent(intent):
    st.markdown("**🎯 意图解析**")
    c1, c2 = st.columns(2)
    c1.metric("标的", intent.asset or "未识别")
    c2.metric("置信度", f"{intent.confidence_score:.0%}")
    c1.metric("操作类型", intent.action_type)
    c2.metric("时间维度", intent.time_horizon)
    if intent.trigger:
        st.caption(f"触发事件：{intent.trigger}")
    if intent.is_context_inherited:
        st.caption("🔗 部分字段继承自上轮对话")
    st.divider()


def _ep_data(data, intent):
    with st.expander("📊 持仓数据", expanded=False):
        # 数据质量告警
        for w in (data.data_warnings or []):
            if w.level == "warning":
                st.warning(f"⚠️ {w.message}")

        st.caption(f"组合总市值：¥{data.total_assets:,.0f}")
        st.caption("口径：聚合市值 / 投资组合总市值")

        if data.target_position:
            tp = data.target_position
            st.markdown(f"**📌 目标持仓（{tp.name}）**")
            st.markdown(f"- 当前仓位：**{tp.weight:.1%}**")
            st.markdown(f"- 聚合市值：¥{tp.market_value_cny:,.0f}")
            st.markdown(f"- 加权收益率：{tp.profit_loss_rate:.1%}")
            if tp.platforms:
                st.caption(f"持仓平台：{' / '.join(tp.platforms)}")
        else:
            st.caption("当前未持有该标的（新建仓）")

        if data.research:
            st.markdown("**📖 投研观点**")
            for v in data.research[:3]:
                st.caption(f"• {v}")


def _ep_rules(rule_result):
    st.markdown("**📏 规则校验**")
    if rule_result.violation:
        st.error(f"⛔ {rule_result.status_label}")
    elif rule_result.warning:
        st.warning(f"⚠️ {rule_result.status_label}")
    else:
        st.success(f"✅ {rule_result.status_label}")
    for detail in rule_result.rule_details:
        st.caption(detail)
    st.divider()


def _ep_signals(signals):
    st.markdown("**📡 信号层**")
    c1, c2 = st.columns(2)
    pos_icon = {"偏高": "🟠", "合理": "🟢", "偏低": "🔵"}.get(signals.position_signal, "⚪")
    c1.metric("仓位", f"{pos_icon} {signals.position_signal}")
    fund_icon = {"正面": "📈", "负面": "📉", "中性": "➡️", "N/A": "❓"}.get(
        signals.fundamental_signal, "➡️"
    )
    c2.metric("基本面", f"{fund_icon} {signals.fundamental_signal}")
    unc_icon = {"高": "⚠️", "中": "🔔", "低": "✅"}.get(
        signals.event_signal.uncertainty, "❓"
    )
    c1.metric("事件", f"{unc_icon} 不确定性{signals.event_signal.uncertainty}")
    c2.metric("情绪", f"➡️ {signals.sentiment_signal}")
    st.divider()


def _ep_llm(llm):
    with st.expander("🔬 AI 推理过程", expanded=True):
        if llm.reasoning:
            st.markdown("**推理依据：**")
            for item in llm.reasoning:
                st.caption(f"• {item}")
