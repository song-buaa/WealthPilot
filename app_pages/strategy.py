"""
WealthPilot — 投资决策页面 (strategy.py) — V3.2

核心设计：
- 左侧 Chat：主交互区，输入框在左侧内部，AI 回答为自然语言总结
- 右侧 Explain Panel：决策链路黑箱拆解，包含完整 6 个模块（含最终结论）
- 布局：55:45（左:右）
- 不使用 st.chat_input（页面底部公共输入框），改用左侧内嵌 text_area + 按钮

左侧 AI 回答规则：
- 基于 decision result 生成自然语言段落，不使用【结论】【原因】【建议】模板
- 保持专业、克制的投资助手口吻
- 流程中断、假设问题、普通对话均有对应的自然语言处理

右侧 Explain Panel 包含：
  ① 意图解析（紧凑行式）
  ② 持仓数据（默认折叠）
  ③ 规则校验（状态 badge + 明细）
  ④ 信号层（2×2 紧凑卡片）
  ⑤ AI 推理过程（默认折叠）
  ⑥ 最终结论（彩色高亮卡片，RESTORED）

状态管理（session_state）：
    chat_history         — list[dict]：对话记录
    decision_map         — dict[str, DecisionResult]
    current_decision_id  — str | None
    de_pending_input     — str | None：示例按钮中转
    de_chat_input        — str：文本框内容
    de_should_clear      — bool：下次 rerun 时清空文本框
"""

import os

import streamlit as st
from app.state import portfolio_id


_STRATEGY_CSS = """
<style>
/* ══════════════════════════════════════════════════════════
   WealthPilot 投资决策页 — 布局规范实现
   规范来源：docs/WealthPilot 投资决策页 UI结构规范.md
   ══════════════════════════════════════════════════════════ */

/* ── 1. 页面级：禁止整体滚动，高度锁定视口 ─────────────── */
.main .block-container {
  height: calc(100vh - 64px) !important;
  overflow: hidden !important;
  padding: 16px 24px 0 24px !important;
  max-width: 100% !important;
}

/* ── 2. 两列：等高对齐，各自独立 ───────────────────────── */
section.main [data-testid="stHorizontalBlock"] {
  align-items: stretch !important;
  height: calc(100vh - 96px) !important;
  gap: 16px !important;
}
section.main [data-testid="stHorizontalBlock"] > [data-testid="column"],
section.main [data-testid="stHorizontalBlock"] > [data-testid="column"] > div {
  display: flex !important;
  flex-direction: column !important;
  height: 100% !important;
  overflow: hidden !important;
}

/* ── 3. 左列 Chat：Flex Column（规范 §二.实现约束）──────── */
[data-testid="stVerticalBlock"]:has(.de-left-marker) {
  display: flex !important;
  flex-direction: column !important;
  height: 100% !important;
  overflow: hidden !important;
}

/* 消息区 → flex:1，overflow-y:auto（规范：Message List）*/
[data-testid="stVerticalBlock"]:has(.de-left-marker)
  > [data-testid="element-container"]:has(#de-msgs-body) {
  flex: 1 !important;
  overflow-y: auto !important;
  min-height: 0 !important;
}

/* 示例按钮区 → 不伸展 */
[data-testid="stVerticalBlock"]:has(.de-left-marker)
  > [data-testid="element-container"]:has(#de-example-btns) {
  flex: 0 0 auto !important;
}

/* 输入区 → 固定底部，不伸展（规范：Input Area）*/
[data-testid="stVerticalBlock"]:has(.de-left-marker)
  > [data-testid="element-container"]:has(#de-input-zone) {
  flex: 0 0 auto !important;
  border-top: 1px solid #E5E7EB !important;
  background: #FFFFFF !important;
  padding: 10px 0 6px !important;
}

/* ── 4. 右列 Panel：标题固定，内容独立滚动（规范 §三）───── */
[data-testid="stVerticalBlock"]:has(.de-right-marker) {
  display: flex !important;
  flex-direction: column !important;
  height: 100% !important;
  overflow: hidden !important;
}
/* 内容容器 → 独立滚动，带边框（规范：容器规则）*/
[data-testid="stVerticalBlock"]:has(.de-right-marker)
  > [data-testid="element-container"]:has(#de-panel-content) {
  flex: 1 !important;
  overflow-y: auto !important;
  min-height: 0 !important;
  border: 1px solid #E5E7EB !important;
  border-radius: 8px !important;
  padding: 12px !important;
  box-sizing: border-box !important;
}

/* ── 5. Chat 消息排版（规范 §二.4 排版规则）─────────────── */
.stChatMessage p {
  font-size: var(--wp-text-body) !important;
  color: var(--wp-color-body) !important;
  line-height: 1.65 !important;
  margin-bottom: 10px !important;
}
.stChatMessage p:last-child { margin-bottom: 0 !important; }
.stChatMessage ul, .stChatMessage ol {
  margin: 4px 0 10px 16px !important;
  padding: 0 !important;
}
.stChatMessage li {
  font-size: var(--wp-text-body) !important;
  color: var(--wp-color-body) !important;
  line-height: 1.6 !important;
  margin-bottom: 4px !important;
}
.stChatMessage strong {
  color: var(--wp-color-h1) !important;
  font-weight: 700 !important;
}
/* AI 回答中的小节标题（操作建议 / 风险提示）*/
.stChatMessage h4 {
  font-size: var(--wp-text-title) !important;
  font-weight: 600 !important;
  color: var(--wp-color-title) !important;
  margin: 14px 0 4px !important;
}
</style>
"""


# ══════════════════════════════════════════════════════════════════════════════
# 状态初始化
# ══════════════════════════════════════════════════════════════════════════════

def _init_session_state():
    defaults = {
        "chat_history": [],
        "decision_map": {},
        "current_decision_id": None,
        "de_pending_input": None,
        "de_chat_input": "",
        "de_should_clear": False,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


# ══════════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════════

def render():
    from ui_components import inject_global_css
    inject_global_css()
    st.markdown(_STRATEGY_CSS, unsafe_allow_html=True)

    st.markdown(
        f'<h1 style="font-size:var(--wp-text-h1);font-weight:700;'
        f'color:var(--wp-color-h1);margin-bottom:8px">💡 投资决策</h1>',
        unsafe_allow_html=True,
    )
    _init_session_state()

    # 处理示例按钮触发的待处理输入（在渲染列之前执行）
    _handle_pending_input()

    left_col, right_col = st.columns([1, 1], gap="medium")  # 50:50

    with left_col:
        # CSS layout marker: identifies this stVerticalBlock as the left chat column
        st.markdown('<div class="de-left-marker" style="display:none"></div>',
                    unsafe_allow_html=True)
        _render_chat_panel()

    with right_col:
        _render_explain_panel()


# ══════════════════════════════════════════════════════════════════════════════
# 待处理输入（示例按钮 → de_pending_input → 下次 rerun 处理）
# ══════════════════════════════════════════════════════════════════════════════

def _handle_pending_input():
    pending = st.session_state.get("de_pending_input")
    if pending:
        st.session_state["de_pending_input"] = None
        st.session_state["de_should_clear"] = True  # 同步清空输入框
        with st.spinner("正在分析，请稍候..."):
            _process_submit(pending)
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# 左侧：Chat 面板
# ══════════════════════════════════════════════════════════════════════════════

def _render_chat_panel():
    # ── 清空输入框（必须在 text_area widget 创建前执行）──────────────────────
    if st.session_state.get("de_should_clear"):
        st.session_state["de_chat_input"] = ""
        st.session_state["de_should_clear"] = False

    _has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    # ── 标题行 ────────────────────────────────────────────────────────────────
    hcol, clcol = st.columns([5, 1])
    with hcol:
        st.markdown(
            '<h2 style="font-size:var(--wp-text-h2);font-weight:600;'
            'color:var(--wp-color-h1);margin:0 0 2px 0;line-height:1.4">💬 对话</h2>',
            unsafe_allow_html=True,
        )
    with clcol:
        if st.button("清空", use_container_width=True, type="secondary",
                     help="清空对话记录和决策历史"):
            st.session_state["chat_history"] = []
            st.session_state["decision_map"] = {}
            st.session_state["current_decision_id"] = None
            st.session_state["de_chat_input"] = ""
            st.rerun()

    if not _has_api_key:
        st.warning(
            "🔑 **未配置 `ANTHROPIC_API_KEY`**，AI 功能暂不可用。"
            "请在终端执行 `export ANTHROPIC_API_KEY='sk-ant-...'` 后重启 Streamlit。",
            icon="⚠️",
        )

    # ── 消息历史区（flex:1，CSS 控制可滚动 — 无 height 参数）─────────────────
    history = st.session_state["chat_history"]

    msg_container = st.container()  # 无 height，由 CSS flex:1 + overflow-y:auto 控制
    with msg_container:
        # CSS layout marker: 让 CSS 找到此容器并应用 flex:1
        st.markdown('<div id="de-msgs-body" style="display:none"></div>',
                    unsafe_allow_html=True)
        if not history:
            _render_empty_welcome()
        else:
            for idx, msg in enumerate(history):
                if msg["role"] == "user":
                    with st.chat_message("user"):
                        st.markdown(msg["content"])
                else:
                    with st.chat_message("assistant"):
                        st.markdown(msg["content"])
                        if msg.get("decision_id"):
                            btn_key = f"view_{msg['decision_id']}_{idx}"
                            if st.button("查看决策逻辑 📊", key=btn_key,
                                         type="secondary"):
                                st.session_state["current_decision_id"] = (
                                    msg["decision_id"]
                                )
                                st.rerun()

    # ── 示例按钮（无历史时展示，flex:0 0 auto）────────────────────────────────
    if not history:
        st.markdown('<div id="de-example-btns" style="display:none"></div>',
                    unsafe_allow_html=True)
        st.caption("**💡 快速体验：**")
        ex1, ex2, ex3 = st.columns(3)
        _example_btn(ex1, "理想汽车发布会前加仓吗？",
                     "理想汽车下周有新车发布会，我想在发布会前加仓，合适吗？")
        _example_btn(ex2, "Meta仓位太重，要减吗？",
                     "我的Meta仓位感觉有点重了，要不要减一部分？")
        _example_btn(ex3, "现在可以建仓苹果吗？",
                     "我想买入苹果，当前时机合适吗？")

    # ── 输入区（CSS 贴底 — border-top 和 padding 由 CSS 控制）──────────────────
    inp_col, btn_col = st.columns([6, 1])
    with inp_col:
        # CSS layout marker（必须在 text_area 之前，用于 :has() CSS 选择器定位输入区）
        st.markdown('<div id="de-input-zone" style="display:none"></div>',
                    unsafe_allow_html=True)
        user_text = st.text_area(
            "投资想法",
            key="de_chat_input",
            placeholder="例如：理想汽车要不要加仓？那蔚来呢？",
            height=76,
            label_visibility="collapsed",
            disabled=not _has_api_key,
        )
    with btn_col:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        send = st.button("发送", type="primary", use_container_width=True,
                         disabled=not _has_api_key)

    if send:
        text = (st.session_state.get("de_chat_input") or "").strip()
        if text:
            st.session_state["de_should_clear"] = True
            with st.spinner("正在分析，请稍候..."):
                _process_submit(text)
            st.rerun()


def _example_btn(col, label: str, full_text: str):
    with col:
        if st.button(label, use_container_width=True):
            st.session_state["de_pending_input"] = full_text
            st.rerun()


def _render_empty_welcome():
    st.markdown(
        '<div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;'
        'padding:18px 20px;">'
        '<b style="color:var(--wp-color-h1);font-size:var(--wp-text-h2)">🧠 投资决策助手</b>'
        '<br><br>'
        '<span style="font-size:var(--wp-text-body);color:var(--wp-color-desc)">'
        '用自然语言描述您的投资想法，系统将完整分析后给出专业建议：</span><br>'
        '<span style="font-size:var(--wp-text-meta);color:var(--wp-color-meta)">'
        '意图解析 → 数据加载 → 规则校验 → 信号分析 → AI 推理'
        '</span>'
        '</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 输入处理：执行完整决策链路，更新对话历史
# ══════════════════════════════════════════════════════════════════════════════

def _process_submit(user_input: str):
    """
    1. 获取 last_intent（从历史最近一条 user 消息的 intent 字段，不从 context 读取）
    2. 构建 context（最近 1 轮，供 general_chat LLM 使用）
    3. 执行 decision_flow.run()
    4. 生成自然语言回答（左侧）
    5. 更新 chat_history / decision_map / current_decision_id
    """
    from decision_engine import decision_flow

    history = st.session_state["chat_history"]

    # last_intent：从历史中最近一条 user 消息的 intent 字段取（PRD §补充1）
    last_intent = None
    for msg in reversed(history):
        if msg["role"] == "user" and msg.get("intent") is not None:
            last_intent = msg["intent"]
            break

    # context：最近 1 轮（user + assistant），仅用于 general_chat
    context = None
    user_msgs = [m for m in history if m["role"] == "user"]
    ai_msgs   = [m for m in history if m["role"] == "assistant"]
    if user_msgs and ai_msgs:
        context = [
            {"role": "user",      "content": user_msgs[-1]["content"]},
            {"role": "assistant", "content": ai_msgs[-1]["content"]},
        ]

    # 先把用户消息加入历史（intent 稍后回填）
    user_msg_idx = len(history)
    history.append({"role": "user", "content": user_input, "intent": None})

    # 执行决策流程
    result = decision_flow.run(
        user_input=user_input,
        last_intent=last_intent,
        context=context,
        pid=portfolio_id,
    )

    # 回填 intent
    history[user_msg_idx]["intent"] = result.intent

    # 生成左侧 Chat 回答
    ai_content  = _build_chat_answer(result, user_input)
    intent_type = result.intent.intent_type if result.intent else "investment_decision"

    history.append({
        "role":        "assistant",
        "content":     ai_content,
        "intent_type": intent_type,
        "decision_id": result.decision_id,
    })

    if result.decision_id:
        st.session_state["decision_map"][result.decision_id] = result
        st.session_state["current_decision_id"] = result.decision_id


# ══════════════════════════════════════════════════════════════════════════════
# 左侧 Chat 回答生成
# ══════════════════════════════════════════════════════════════════════════════

def _build_chat_answer(result, user_input: str) -> str:
    """
    生成左侧 Chat 面板的 AI 回答。

    - general_chat：返回 result.chat_response（已由 llm_engine.chat() 生成）
    - aborted：返回中断原因
    - investment_decision 完整结果：调用 llm_engine.generate_chat_answer() 生成自然语言
    """
    from decision_engine import llm_engine

    intent_type = result.intent.intent_type if result.intent else "investment_decision"

    if intent_type == "general_chat":
        return result.chat_response or "（系统暂无回复，请重试）"

    if result.was_aborted:
        return result.aborted_reason or "分析中断，请重新描述您的投资需求。"

    if result.is_complete and result.llm:
        llm = result.llm

        # ① 结论行（H2 级别，最先看到）
        conclusion = f"**{llm.decision_emoji} 建议{llm.decision_cn}**"

        # ② 自然语言解释（LLM 生成，仅含结论+原因，不含策略/风险）
        explanation = llm_engine.generate_chat_answer(
            user_query=user_input,
            intent=result.intent,
            data=result.data,
            rules=result.rules,
            llm_result=llm,
        )

        # ③ 操作建议（结构化 bullet，来自决策引擎 strategy 字段）
        strategy_md = ""
        if llm.strategy:
            bullets = "\n".join(f"- {s}" for s in llm.strategy)
            strategy_md = f"\n\n#### 操作建议\n{bullets}"

        # ④ 风险提示（结构化 bullet，来自决策引擎 risk 字段）
        risk_md = ""
        if llm.risk:
            bullets = "\n".join(f"- {r}" for r in llm.risk)
            risk_md = f"\n\n#### 风险提示\n{bullets}"

        # 降级提示
        suffix_parts = []
        if llm.is_fallback:
            suffix_parts.append(f"⚠️ *AI 推理遇到问题（{llm.error}），结论为降级结果。*")
        if llm.decision_corrected:
            suffix_parts.append(
                f"ℹ️ *AI 原始输出「{llm.original_decision}」不在标准选项内，"
                f"已自动修正为「{llm.decision_cn}」。*"
            )
        suffix = ("\n\n> " + "\n> ".join(suffix_parts)) if suffix_parts else ""
        disclaimer = "\n\n---\n*⚖️ 仅供参考，不构成投资建议。投资有风险，入市需谨慎。*"

        # 拼装结构：结论 + 解释 + 操作建议 + 风险提示（规范 §二.5 AI回答强制格式）
        return f"{conclusion}\n\n{explanation}{strategy_md}{risk_md}{suffix}{disclaimer}"

    return "分析未能完成，请重试。"


# ══════════════════════════════════════════════════════════════════════════════
# 右侧：Explain Panel（完整 6 模块，紧凑样式）
# ══════════════════════════════════════════════════════════════════════════════

def _render_explain_panel():
    # CSS layout marker: 识别右列 stVerticalBlock，应用 flex column 布局
    st.markdown('<div class="de-right-marker" style="display:none"></div>',
                unsafe_allow_html=True)
    # 标题固定（不参与滚动）
    st.markdown(
        '<h2 style="font-size:var(--wp-text-h2);font-weight:600;'
        'color:var(--wp-color-h1);margin:0 0 6px 0;line-height:1.4">📊 决策链路</h2>',
        unsafe_allow_html=True,
    )

    current_id   = st.session_state.get("current_decision_id")
    decision_map = st.session_state.get("decision_map", {})
    history      = st.session_state.get("chat_history", [])

    # 内容容器（无 height 参数，由 CSS flex:1 + overflow-y:auto + border 控制）
    panel = st.container()

    with panel:
        # CSS layout marker: 识别此容器，CSS 应用 border + 独立滚动
        st.markdown('<div id="de-panel-content" style="display:none"></div>',
                    unsafe_allow_html=True)
        # 无历史
        if not history:
            st.markdown(
                '<div style="background:#F0F9FF;border:1px solid #BAE6FD;border-radius:8px;'
                'padding:14px;color:#0369A1;font-size:var(--wp-text-desc);margin-top:4px">'
                '💡 开始对话后，点击 AI 回复下方的「查看决策逻辑 📊」，'
                '这里将展示完整的分析链路。</div>',
                unsafe_allow_html=True,
            )
            return

        # 最近一条 AI 消息是 general_chat 且无 decision
        last_ai = next((m for m in reversed(history) if m["role"] == "assistant"), None)
        if last_ai and last_ai.get("intent_type") == "general_chat" and not current_id:
            st.info("当前对话为普通问答，无投资决策链路。")
            return

        # 尚未点击过按钮
        if not current_id or current_id not in decision_map:
            st.caption("点击左侧 AI 回复下方的「查看决策逻辑 📊」按钮查看分析详情。")
            return

        result = decision_map[current_id]
        st.caption(f"Decision ID: `{current_id}`")

        # ── ① 意图解析 ────────────────────────────────────────────────────────────
        if result.intent:
            _ep_intent(result.intent)

        # ── ② 持仓数据（默认折叠）────────────────────────────────────────────────
        if result.data:
            _ep_data(result.data)

        # ── ③ 规则校验 ────────────────────────────────────────────────────────────
        if result.rules:
            _ep_rules(result.rules)

        # ── ④ 信号层 ──────────────────────────────────────────────────────────────
        if result.signals:
            _ep_signals(result.signals)

        # ── ⑤ AI 推理过程（默认折叠）────────────────────────────────────────────
        if result.llm:
            _ep_reasoning(result.llm)

        # ── ⑥ 最终结论（RESTORED — 彩色卡片）────────────────────────────────────
        if result.llm:
            _ep_conclusion(result.llm)

        # 合规提示
        st.caption("⚖️ 本系统输出仅供参考，不构成投资建议。")


# ── 紧凑子模块渲染函数 ─────────────────────────────────────────────────────────

# Label：小字 + uppercase（对应 Typography System Label 层级）
_EP_LABEL_CSS = (
    "color:var(--wp-color-label);"
    "font-size:var(--wp-text-label);"
    "font-weight:600;"
    "text-transform:uppercase;"
    "letter-spacing:0.5px"
)
# Value：正文 Body 层级
_EP_VAL_CSS = (
    "font-weight:600;"
    "font-size:var(--wp-text-body);"
    "color:var(--wp-color-title)"
)


def _ep_row_md(label: str, value: str) -> str:
    """生成一行 label: value 的紧凑 HTML。"""
    return (
        f'<span style="{_EP_LABEL_CSS}">{label}</span>&nbsp;'
        f'<span style="{_EP_VAL_CSS}">{value}</span>'
    )


def _ep_intent(intent):
    with st.container(border=True):
        st.markdown(
            '<div style="font-size:var(--wp-text-title);font-weight:600;'
            'color:var(--wp-color-title);margin-bottom:6px">🎯 意图解析</div>',
            unsafe_allow_html=True,
        )
        rows = [
            _ep_row_md("标的", intent.asset or "未识别"),
            _ep_row_md("操作", intent.action_type),
            _ep_row_md("时间", intent.time_horizon),
            _ep_row_md("置信度", f"{intent.confidence_score:.0%}"),
        ]
        if intent.trigger:
            rows.append(_ep_row_md("触发", intent.trigger))
        st.markdown(
            "<div style='line-height:2;padding:2px 0'>" +
            "<br>".join(rows) +
            "</div>",
            unsafe_allow_html=True,
        )
        if intent.is_context_inherited:
            st.markdown(
                '<div style="font-size:var(--wp-text-meta);color:var(--wp-color-meta);'
                'margin-top:4px">🔗 部分字段继承自上轮对话</div>',
                unsafe_allow_html=True,
            )


def _ep_data(data):
    with st.expander("📊 持仓数据", expanded=False):
        for w in (data.data_warnings or []):
            if w.level == "warning":
                st.caption(f"⚠️ {w.message}")

        st.caption(f"组合总市值：¥{data.total_assets:,.0f}　口径：聚合市值 / 组合总市值")

        if data.target_position:
            tp = data.target_position
            st.markdown(
                f"**{tp.name}**　"
                f"仓位 **{tp.weight:.1%}**　"
                f"市值 ¥{tp.market_value_cny:,.0f}　"
                f"收益率 {tp.profit_loss_rate:.1%}"
            )
            if tp.platforms:
                st.caption(f"持仓平台：{' / '.join(tp.platforms)}")
        else:
            st.caption("当前未持有该标的（新建仓）")

        if data.research:
            st.caption("**投研观点：**")
            for v in data.research[:3]:
                st.caption(f"• {v}")


def _ep_rules(rule_result):
    with st.container(border=True):
        st.markdown(
            '<div style="font-size:var(--wp-text-title);font-weight:600;'
            'color:var(--wp-color-title);margin-bottom:6px">📏 规则校验</div>',
            unsafe_allow_html=True,
        )
        if rule_result.violation:
            st.error(f"⛔ {rule_result.status_label}", icon="🚫")
        elif rule_result.warning:
            st.warning(f"⚠️ {rule_result.status_label}")
        else:
            st.success(f"✅ {rule_result.status_label}")
        for detail in rule_result.rule_details:
            st.markdown(
                f'<div style="font-size:var(--wp-text-desc);color:var(--wp-color-desc);'
                f'padding:1px 0">• {detail}</div>',
                unsafe_allow_html=True,
            )


def _ep_signals(signals):
    with st.container(border=True):
        st.markdown(
            '<div style="font-size:var(--wp-text-title);font-weight:600;'
            'color:var(--wp-color-title);margin-bottom:6px">📡 信号层</div>',
            unsafe_allow_html=True,
        )
        pos_icon  = {"偏高": "🟠", "合理": "🟢", "偏低": "🔵"}.get(signals.position_signal, "⚪")
        fund_icon = {"正面": "📈", "负面": "📉", "中性": "➡️", "N/A": "❓"}.get(
            signals.fundamental_signal, "➡️")
        unc_icon  = {"高": "⚠️", "中": "🔔", "低": "✅"}.get(
            signals.event_signal.uncertainty, "❓")

        rows = [
            _ep_row_md("仓位", f"{pos_icon} {signals.position_signal}"),
            _ep_row_md("基本面", f"{fund_icon} {signals.fundamental_signal}"),
            _ep_row_md("事件", f"{unc_icon} 不确定性{signals.event_signal.uncertainty}"),
            _ep_row_md("情绪", f"➡️ {signals.sentiment_signal}"),
        ]
        st.markdown(
            "<div style='line-height:2;padding:2px 0'>" +
            "<br>".join(rows) +
            "</div>",
            unsafe_allow_html=True,
        )


def _ep_reasoning(llm):
    with st.expander("🔬 AI 推理过程", expanded=False):
        if llm.reasoning:
            for item in llm.reasoning:
                st.caption(f"• {item}")
        else:
            st.caption("（无推理依据）")


def _ep_conclusion(llm):
    """最终结论 — 彩色高亮卡片"""
    colors = {"BUY": ("#059669", "#ECFDF5", "#D1FAE5"),
              "HOLD": ("#D97706", "#FFFBEB", "#FDE68A"),
              "SELL": ("#DC2626", "#FEF2F2", "#FECACA")}
    text_c, bg_c, border_c = colors.get(llm.decision, ("#64748B", "#F8FAFC", "#E2E8F0"))

    with st.container(border=True):
        st.markdown(
            '<div style="font-size:var(--wp-text-title);font-weight:600;'
            'color:var(--wp-color-title);margin-bottom:6px">🏁 最终结论</div>',
            unsafe_allow_html=True,
        )
        # 决策结果大卡片
        st.markdown(
            f'<div style="background:{bg_c};border:1px solid {border_c};border-radius:8px;'
            f'padding:12px 16px;margin:0 0 8px 0">'
            f'<span style="font-size:var(--wp-text-h1)">{llm.decision_emoji}</span>'
            f'<span style="font-size:var(--wp-text-h2);font-weight:700;color:{text_c};'
            f'margin-left:10px">{llm.decision_cn}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        if llm.is_fallback:
            st.markdown(
                f'<div style="font-size:var(--wp-text-meta);color:var(--wp-color-meta)">'
                f'⚠️ AI 推理不可用：{llm.error}</div>',
                unsafe_allow_html=True,
            )
        if llm.decision_corrected:
            st.markdown(
                f'<div style="font-size:var(--wp-text-meta);color:var(--wp-color-meta)">'
                f'ℹ️ 原始输出「{llm.original_decision}」已自动修正</div>',
                unsafe_allow_html=True,
            )

        if llm.strategy:
            # "操作建议" → Title 层级（小节标题）
            st.markdown(
                '<div style="font-size:var(--wp-text-title);font-weight:600;'
                'color:var(--wp-color-title);margin:6px 0 3px">操作建议</div>',
                unsafe_allow_html=True,
            )
            items_html = "".join(
                f'<div style="font-size:var(--wp-text-desc);color:var(--wp-color-desc);'
                f'padding:2px 0 2px 8px;line-height:1.5">• {s}</div>'
                for s in llm.strategy
            )
            st.markdown(items_html, unsafe_allow_html=True)

        if llm.risk:
            # "风险提示" → Title 层级（小节标题）
            st.markdown(
                '<div style="font-size:var(--wp-text-title);font-weight:600;'
                'color:var(--wp-color-title);margin:6px 0 3px">风险提示</div>',
                unsafe_allow_html=True,
            )
            items_html = "".join(
                f'<div style="font-size:var(--wp-text-desc);color:var(--wp-color-desc);'
                f'padding:2px 0 2px 8px;line-height:1.5">• {r}</div>'
                for r in llm.risk
            )
            st.markdown(items_html, unsafe_allow_html=True)
