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
import uuid

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
        "de_chat_input": "",
        "de_should_clear": False,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val
    # intent_engine 会话 ID（每次 Streamlit 进程生命周期内唯一）
    if "ie_session_id" not in st.session_state:
        st.session_state["ie_session_id"] = uuid.uuid4().hex


# ══════════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════════

def render():
    st.title("💡 投资决策")
    _init_session_state()

    # 处理示例按钮触发的待处理输入（在渲染列之前执行）
    _handle_pending_input()

    left_col, right_col = st.columns([11, 9], gap="medium")  # 55:45

    with left_col:
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

    _has_api_key = bool(os.environ.get("OPENAI_API_KEY"))

    # ── 标题行 ────────────────────────────────────────────────────────────────
    hcol, clcol = st.columns([5, 1])
    with hcol:
        st.markdown("### 💬 对话")
    with clcol:
        if st.button("清空", use_container_width=True, type="secondary",
                     help="清空对话记录和决策历史"):
            st.session_state["chat_history"] = []
            st.session_state["decision_map"] = {}
            st.session_state["current_decision_id"] = None
            st.session_state["de_chat_input"] = ""
            st.session_state["ie_session_id"] = uuid.uuid4().hex  # 重置意图上下文
            st.rerun()

    if not _has_api_key:
        st.warning(
            "🔑 **未配置 `OPENAI_API_KEY`**，AI 功能暂不可用。"
            "请在终端执行 `export OPENAI_API_KEY='sk-...'` 后重启 Streamlit。",
            icon="⚠️",
        )

    # ── 消息历史区（固定高度可滚动）──────────────────────────────────────────
    history = st.session_state["chat_history"]

    st.markdown("""
    <style>
    [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] li {
        font-size: 0.8rem;
    }
    </style>
    """, unsafe_allow_html=True)

    msg_container = st.container(height=420)
    with msg_container:
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
                        # investment_decision 才有 decision_id
                        if msg.get("decision_id"):
                            btn_key = f"view_{msg['decision_id']}_{idx}"
                            if st.button("查看决策逻辑 📊", key=btn_key,
                                         type="secondary"):
                                st.session_state["current_decision_id"] = (
                                    msg["decision_id"]
                                )
                                st.rerun()

    # ── 示例按钮（无历史时展示）──────────────────────────────────────────────
    if not history:
        st.caption("**💡 快速体验：**")
        ex1, ex2, ex3 = st.columns(3)
        _example_btn(ex1, "理想汽车发布会前加仓吗？",
                     "理想汽车下周有新车发布会，我想在发布会前加仓，合适吗？")
        _example_btn(ex2, "Meta仓位太重，要减吗？",
                     "我的Meta仓位感觉有点重了，要不要减一部分？")
        _example_btn(ex3, "现在可以建仓苹果吗？",
                     "我想买入苹果，当前时机合适吗？")

    # ── 输入区（固定在左侧 Chat 内部底部）────────────────────────────────────
    st.divider()
    inp_col, btn_col = st.columns([6, 1])
    with inp_col:
        user_text = st.text_area(
            "投资想法",
            key="de_chat_input",
            placeholder="例如：理想汽车要不要加仓？那蔚来呢？",
            height=76,
            label_visibility="collapsed",
            disabled=not _has_api_key,
        )
    with btn_col:
        # 对齐按钮高度
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
    st.markdown("""
    <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;
                padding:18px 20px;color:#64748B;">
    <b style="color:#1E3A5F;font-size:15px">🧠 投资决策助手</b><br><br>
    用自然语言描述您的投资想法，系统将完整分析后给出专业建议：<br>
    <span style="font-size:12px;color:#94A3B8">
      意图解析 → 数据加载 → 规则校验 → 信号分析 → AI 推理
    </span>
    </div>
    """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# 输入处理：执行完整决策链路，更新对话历史
# ══════════════════════════════════════════════════════════════════════════════

def _payload_to_intent_result(payload, ctx):
    """
    适配器：将 IntentPayload（intent_engine）转换为 IntentResult（decision_engine）。

    action code 映射（英文 → 中文 action_type）：
        BUY / ADD         → 买入判断 / 加仓判断
        SELL / STOP_LOSS / TAKE_PROFIT → 卖出判断 / 减仓判断
        REDUCE            → 减仓判断
        ANALYZE / 其他    → 持有评估
    """
    from decision_engine.types import IntentResult

    _ACTION_MAP = {
        "BUY":         "买入判断",
        "ADD":         "加仓判断",
        "SELL":        "卖出判断",
        "STOP_LOSS":   "卖出判断",
        "TAKE_PROFIT": "减仓判断",
        "REDUCE":      "减仓判断",
        "HOLD":        "持有评估",
        "ANALYZE":     "持有评估",
    }

    # 标的：本轮识别 → 上下文继承
    asset = payload.entities.asset or ctx.inherited_fields.asset

    # 操作类型：取 actions 中第一个有效映射
    first_action = payload.actions[0] if payload.actions else "ANALYZE"
    action_type = _ACTION_MAP.get(first_action, "持有评估")

    # 时间跨度
    time_horizon = payload.entities.time_horizon or ctx.inherited_fields.time_horizon or "未知"

    return IntentResult(
        asset=asset,
        action_type=action_type,
        time_horizon=time_horizon,
        trigger=None,
        confidence_score=payload.confidence,
        clarification=None,
        intent_type="investment_decision",
        is_context_inherited=bool(not payload.entities.asset and ctx.inherited_fields.asset),
    )


def _process_multi_asset(payload, ctx, user_input, multi_assets, history, user_msg_idx):
    """
    多标的同操作分发：对 multi_assets 中每个标的顺序运行完整决策链路，
    合并结论输出到左侧 Chat，右侧链路面板展示最后一个标的的决策详情。
    """
    from decision_engine import decision_flow

    results: list[tuple[str, object]] = []  # (asset_name, DecisionResult)

    for asset_name in multi_assets:
        # 克隆 payload，替换 asset（保持其他字段不变）
        from dataclasses import replace as _replace
        from intent_engine.types import IntentEntities
        new_entities = _replace(
            payload.entities,
            asset=asset_name,
            multi_assets=[],  # 单次调用时清空，避免递归
        )
        single_payload = _replace(payload, entities=new_entities)
        intent_result = _payload_to_intent_result(single_payload, ctx)
        r = decision_flow.run_with_intent(
            intent=intent_result,
            user_input=user_input,
            pid=portfolio_id,
        )
        results.append((asset_name, r))

    if not results:
        return

    # ── 左侧 Chat：合并回答 ─────────────────────────────────────────────────
    ai_content = _build_multi_asset_chat_answer(results, user_input)

    # ── 右侧链路：使用最后一个标的的决策结果 ──────────────────────────────────
    last_name, last_result = results[-1]
    history[user_msg_idx]["intent"] = last_result.intent

    history.append({
        "role":        "assistant",
        "content":     ai_content,
        "intent_type": "investment_decision",
        "decision_id": last_result.decision_id,
    })

    # 全部结果写入 decision_map（以标的名为 key 区分）
    for asset_name, r in results:
        if r.decision_id:
            st.session_state["decision_map"][r.decision_id] = r
    # 右侧链路面板默认展示最后一个标的
    if last_result.decision_id:
        st.session_state["current_decision_id"] = last_result.decision_id


def _build_multi_asset_chat_answer(results: list, user_input: str) -> str:
    """
    将多个 DecisionResult 合并成一段自然语言回答。
    按「标的名 → 结论 → 关键理由 → 风险」格式逐一展示。
    """
    parts = []
    for asset_name, r in results:
        if r.was_aborted:
            parts.append(f"**{asset_name}**：{r.aborted_reason or '分析中断，请补充持仓数据后重试。'}")
        elif r.is_complete and r.llm:
            # 优先使用 LLM 生成的 chat_answer
            if r.llm.chat_answer:
                parts.append(f"**{asset_name}** — {r.llm.decision_emoji} {r.llm.decision_cn}\n\n{r.llm.chat_answer}")
            else:
                # 降级：从结构化字段拼接
                decision_cn = r.llm.decision_cn
                reasons = "；".join(r.llm.reasoning[:2]) if r.llm.reasoning else ""
                parts.append(
                    f"**{asset_name}** — {r.llm.decision_emoji} **{decision_cn}**。"
                    + (f"\n\n{reasons}。" if reasons else "")
                )
        else:
            parts.append(f"**{asset_name}**：数据加载失败，请稍后重试。")

    combined = "\n\n---\n\n".join(parts)
    suffix = "\n\n---\n*⚖️ 仅供参考，不构成投资建议。投资有风险，入市需谨慎。*"
    return combined + suffix


def _run_portfolio_intent(payload, user_input: str, pid: int):
    """
    组合级别意图执行链路：数据加载 → LLM 推理。
    适用于 PortfolioReview / AssetAllocation / PerformanceAnalysis。

    Returns:
        (DecisionResult, ai_content_str)
    """
    from decision_engine import data_loader, llm_engine
    from decision_engine.decision_flow import DecisionResult, FlowStage
    from decision_engine.types import IntentResult

    _INTENT_CONFIG = {
        "PortfolioReview":    ("portfolio_review",    "组合评估", llm_engine.review_portfolio),
        "AssetAllocation":    ("asset_allocation",    "资产配置", llm_engine.analyze_allocation),
        "PerformanceAnalysis":("performance_analysis","收益分析", llm_engine.analyze_performance),
    }
    intent_type_key, action_label, llm_fn = _INTENT_CONFIG[payload.primary_intent]

    result = DecisionResult()
    result.decision_id = f"decision_{uuid.uuid4().hex[:8]}"
    result.intent = IntentResult(
        asset=None,
        action_type=action_label,
        time_horizon="当前",
        trigger=None,
        confidence_score=payload.confidence,
        intent_type=intent_type_key,
    )
    result.stage = FlowStage.INTENT

    # 数据加载
    try:
        loaded = data_loader.load(asset_name=None, pid=pid)
    except Exception as e:
        result.stage = FlowStage.ABORTED
        result.aborted_reason = f"数据加载失败：{e}"
        return result, result.aborted_reason

    result.data = loaded
    result.stage = FlowStage.LOADED

    if loaded.has_data_errors:
        error_msgs = "\n".join(
            f"- {w.message}" for w in loaded.data_warnings if w.level == "error"
        )
        result.stage = FlowStage.ABORTED
        result.aborted_reason = f"⚠️ 数据质量问题，无法给出分析建议：\n\n{error_msgs}"
        return result, result.aborted_reason

    # LLM 推理
    generic_llm = llm_fn(user_input, loaded)
    result.generic_llm = generic_llm
    result.stage = FlowStage.DONE

    if generic_llm.is_fallback:
        ai_content = f"⚠️ AI 分析遇到问题：{generic_llm.error}\n\n请稍后重试。"
    else:
        ai_content = (
            (generic_llm.chat_answer or "分析完成，请查看右侧分析详情。")
            + "\n\n---\n*⚖️ 仅供参考，不构成投资建议。投资有风险，入市需谨慎。*"
        )
    return result, ai_content


def _process_submit(user_input: str):
    """
    路由逻辑（意图识别统一由 intent_engine 负责，不再重复调用 decision_engine.intent_parser）：

    1. intent_engine.intent_recognizer.recognize() → (IntentPayload, clarification)
    2. intent_engine.context_manager.build_context() → ExecutionContext（多轮继承）
    3. 置信度不足 → 返回澄清问题
    4. PositionDecision → 适配器 + decision_flow.run_with_intent() → DecisionResult（右侧链路不变）
    5. 其他意图 → llm_engine.chat()（暂时保持普通对话，后续按意图精细化）
    """
    from intent_engine import intent_recognizer, context_manager
    from decision_engine import decision_flow, llm_engine

    history    = st.session_state["chat_history"]
    session_id = st.session_state["ie_session_id"]

    # ── Step 1: 意图识别（唯一来源）─────────────────────────────────────────
    try:
        payload, clarification = intent_recognizer.recognize(user_input)
    except EnvironmentError as e:
        history.append({"role": "user", "content": user_input, "intent": None})
        history.append({
            "role": "assistant",
            "content": f"⚙️ 配置问题：{e}",
            "intent_type": "error",
            "decision_id": None,
        })
        return

    # ── Step 2: 构建多轮上下文 ───────────────────────────────────────────────
    ctx = context_manager.build_context(session_id, payload, portfolio_id)

    # 先把用户消息写入历史
    user_msg_idx = len(history)
    history.append({"role": "user", "content": user_input, "intent": None})

    # ── Step 3: 置信度不足 → 返回澄清问题 ───────────────────────────────────
    if clarification and payload.confidence < 0.5:
        history.append({
            "role": "assistant",
            "content": clarification,
            "intent_type": payload.primary_intent,
            "decision_id": None,
        })
        return

    # ── Step 4: 按意图类型路由 ───────────────────────────────────────────────
    if payload.primary_intent == "PositionDecision":
        multi = list(payload.entities.multi_assets or [])

        if len(multi) >= 2:
            # 多标的同操作：依次运行每个标的的完整决策链路，合并回答
            _process_multi_asset(
                payload=payload,
                ctx=ctx,
                user_input=user_input,
                multi_assets=multi,
                history=history,
                user_msg_idx=user_msg_idx,
            )
            return

        # 单标的：完整 6 步链路（数据加载 → 前置校验 → 规则 → 信号 → LLM）
        intent_result = _payload_to_intent_result(payload, ctx)
        result = decision_flow.run_with_intent(
            intent=intent_result,
            user_input=user_input,
            pid=portfolio_id,
        )
        history[user_msg_idx]["intent"] = result.intent
        ai_content = _build_chat_answer(result, user_input)
        history.append({
            "role":        "assistant",
            "content":     ai_content,
            "intent_type": "investment_decision",
            "decision_id": result.decision_id,
        })
        if result.decision_id:
            st.session_state["decision_map"][result.decision_id] = result
            st.session_state["current_decision_id"] = result.decision_id
        return

    if payload.primary_intent in ("PortfolioReview", "AssetAllocation", "PerformanceAnalysis"):
        # 组合级别链路（数据加载 → LLM，无规则/信号步骤）
        result, ai_content = _run_portfolio_intent(payload, user_input, portfolio_id)
        history[user_msg_idx]["intent"] = result.intent
        history.append({
            "role":        "assistant",
            "content":     ai_content,
            "intent_type": payload.primary_intent.lower(),
            "decision_id": result.decision_id,
        })
        if result.decision_id:
            st.session_state["decision_map"][result.decision_id] = result
            st.session_state["current_decision_id"] = result.decision_id
        return

    # ── Step 5: Education / GeneralChat → 普通对话 ───────────────────────────
    context_msgs = None
    user_msgs = [m for m in history[:-1] if m["role"] == "user"]
    ai_msgs   = [m for m in history[:-1] if m["role"] == "assistant"]
    if user_msgs and ai_msgs:
        context_msgs = [
            {"role": "user",      "content": user_msgs[-1]["content"]},
            {"role": "assistant", "content": ai_msgs[-1]["content"]},
        ]

    chat_text = llm_engine.chat(user_input, context=context_msgs)
    history.append({
        "role":        "assistant",
        "content":     chat_text or "（系统暂无回复，请重试）",
        "intent_type": "general_chat",
        "decision_id": None,
    })


# ══════════════════════════════════════════════════════════════════════════════
# 左侧 Chat 回答生成
# ══════════════════════════════════════════════════════════════════════════════

def _build_chat_answer(result, user_input: str) -> str:
    """
    生成左侧 Chat 面板的 AI 回答。

    - aborted：返回中断原因
    - investment_decision 完整结果：直接使用 llm.chat_answer（由 reason() 单次调用生成）
    - fallback：降级文本
    """
    if result.was_aborted:
        return result.aborted_reason or "分析中断，请重新描述您的投资需求。"

    if result.is_complete and result.llm:
        # chat_answer 由 reason() 在同一次 LLM 调用中生成，无需二次调用
        answer = result.llm.chat_answer
        if not answer:
            # 极少数情况：LLM 未输出 chat_answer，用简洁 fallback
            decision_cn = {
                "BUY":         "加仓",
                "HOLD":        "观望",
                "TAKE_PROFIT": "部分止盈",
                "REDUCE":      "逐步减仓",
                "SELL":        "减仓/清仓",
                "STOP_LOSS":   "止损离场",
            }.get(result.llm.decision, "观望")
            asset = result.intent.asset or "该标的" if result.intent else "该标的"
            reasons = "；".join(result.llm.reasoning[:2]) if result.llm.reasoning else ""
            answer = f"**{asset}** 当前建议**{decision_cn}**。" + (f"\n\n{reasons}。" if reasons else "")

        # 降级/修正提示追加
        suffix_parts = []
        if result.llm.is_fallback:
            suffix_parts.append(f"⚠️ *AI 推理遇到问题（{result.llm.error}），结论为降级结果。*")
        if result.llm.decision_corrected:
            suffix_parts.append(
                f"ℹ️ *AI 原始输出「{result.llm.original_decision}」不在标准选项内，"
                f"已自动修正为「{result.llm.decision_cn}」。*"
            )
        suffix = ("\n\n> " + "\n> ".join(suffix_parts)) if suffix_parts else ""
        return answer + suffix + "\n\n---\n*⚖️ 仅供参考，不构成投资建议。投资有风险，入市需谨慎。*"

    return "分析未能完成，请重试。"


# ══════════════════════════════════════════════════════════════════════════════
# 右侧：Explain Panel（完整 6 模块，紧凑样式）
# ══════════════════════════════════════════════════════════════════════════════

def _render_explain_panel():
    st.markdown("### 📊 决策链路")

    current_id   = st.session_state.get("current_decision_id")
    decision_map = st.session_state.get("decision_map", {})
    history      = st.session_state.get("chat_history", [])

    # 无历史
    if not history:
        st.markdown(
            "<div style='background:#F0F9FF;border:1px solid #BAE6FD;border-radius:8px;"
            "padding:14px;color:#0369A1;font-size:13px;margin-top:4px'>"
            "💡 开始对话后，点击 AI 回复下方的「查看决策逻辑 📊」，"
            "这里将展示完整的分析链路。</div>",
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

    intent_type = result.intent.intent_type if result.intent else "investment_decision"

    if intent_type == "investment_decision":
        _render_panel_position_decision(result)
    else:
        _render_panel_portfolio_intent(result, intent_type)


def _render_panel_position_decision(result):
    """单标的决策右侧面板（完整 6 步链路）"""
    if result.intent:
        _ep_intent(result.intent)
    if result.data:
        _ep_data(result.data)
    if result.data:
        _ep_research(result.data)
    if result.rules:
        _ep_rules(result.rules)
    if result.signals:
        _ep_signals(result.signals)
    if result.llm:
        _ep_reasoning(result.llm)
    if result.llm:
        _ep_conclusion(result.llm)
    st.caption("⚖️ 本系统输出仅供参考，不构成投资建议。")


def _render_panel_portfolio_intent(result, intent_type: str):
    """组合级别意图右侧面板（PortfolioReview / AssetAllocation / PerformanceAnalysis）"""
    _PANEL_TITLE = {
        "portfolio_review":    "📊 组合评估",
        "asset_allocation":    "🧩 资产配置",
        "performance_analysis":"📈 收益分析",
    }
    if result.intent:
        st.markdown(f"**🎯 意图解析**")
        rows = [
            _ep_row_md("类型", _PANEL_TITLE.get(intent_type, intent_type)),
            _ep_row_md("置信度", f"{result.intent.confidence_score:.0%}"),
        ]
        st.markdown(
            "<div style='line-height:1.8;padding:6px 0'>" +
            " &nbsp;·&nbsp; ".join(rows) +
            "</div>",
            unsafe_allow_html=True,
        )
        st.divider()

    if result.was_aborted:
        st.error(result.aborted_reason)
        st.caption("⚖️ 本系统输出仅供参考，不构成投资建议。")
        return

    if result.data:
        _ep_portfolio_overview(result.data)

    if result.generic_llm:
        if result.generic_llm.is_fallback:
            st.warning(f"⚠️ AI 分析遇到问题：{result.generic_llm.error}")
        elif intent_type == "portfolio_review":
            _ep_portfolio_review_result(result.generic_llm)
        elif intent_type == "asset_allocation":
            _ep_asset_allocation_result(result.generic_llm)
        elif intent_type == "performance_analysis":
            _ep_performance_analysis_result(result.generic_llm)

    st.caption("⚖️ 本系统输出仅供参考，不构成投资建议。")


# ── 紧凑子模块渲染函数 ─────────────────────────────────────────────────────────

_EP_LABEL_CSS = "color:#6B7280;font-size:11px"
_EP_VAL_CSS   = "font-weight:600;font-size:13px"


def _ep_row_md(label: str, value: str) -> str:
    """生成一行 label: value 的紧凑 HTML。"""
    return (
        f'<span style="{_EP_LABEL_CSS}">{label}</span>&nbsp;'
        f'<span style="{_EP_VAL_CSS}">{value}</span>'
    )


def _ep_intent(intent):
    st.markdown("**🎯 意图解析**")
    # 紧凑行式展示，不用 st.metric（字号太大）
    rows = [
        _ep_row_md("标的", intent.asset or "未识别"),
        _ep_row_md("操作", intent.action_type),
        _ep_row_md("时间", intent.time_horizon),
        _ep_row_md("置信度", f"{intent.confidence_score:.0%}"),
    ]
    if intent.trigger:
        rows.append(_ep_row_md("触发", intent.trigger))
    st.markdown(
        "<div style='line-height:1.8;padding:6px 0'>" +
        " &nbsp;·&nbsp; ".join(rows) +
        "</div>",
        unsafe_allow_html=True,
    )
    if intent.is_context_inherited:
        st.caption("🔗 部分字段继承自上轮对话")
    st.divider()


def _ep_data(data):
    with st.expander("📊 持仓数据", expanded=False):
        for w in (data.data_warnings or []):
            if w.level == "warning":
                st.caption(f"⚠️ {w.message}")

        st.caption(f"- 组合总市值：**¥{data.total_assets:,.0f}**，口径：聚合市值 / 组合总市值")

        if data.target_position:
            tp = data.target_position
            st.caption(
                f"- {tp.name}：仓位 **{tp.weight:.1%}**，"
                f"市值 **¥{tp.market_value_cny:,.0f}**，"
                f"收益率 **{tp.profit_loss_rate:.1%}**"
            )
            if tp.platforms:
                st.caption(f"- 持仓平台：{' / '.join(tp.platforms)}")
        else:
            st.caption("- 当前未持有该标的（新建仓）")


def _ep_research(data):
    with st.expander("📝 投研观点", expanded=False):
        if data.research:
            for v in data.research:
                st.caption(f"• {v}")
        else:
            st.caption("暂无该标的的投研观点，建议自行研究或参考市场报告。")


def _ep_rules(rule_result):
    st.markdown("**📏 规则校验**")
    if rule_result.violation:
        st.error(f"⛔ {rule_result.status_label}", icon="🚫")
    elif rule_result.warning:
        st.warning(f"⚠️ {rule_result.status_label}")
    else:
        st.success(f"✅ {rule_result.status_label}")
    for detail in rule_result.rule_details:
        st.caption(detail)
    st.divider()


def _ep_signals(signals):
    st.markdown("**📡 信号层**")
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
        "<div style='line-height:2;padding:4px 0'>" +
        "<br>".join(rows) +
        "</div>",
        unsafe_allow_html=True,
    )
    st.divider()


def _ep_reasoning(llm):
    with st.expander("🔬 AI 推理过程", expanded=False):
        if llm.reasoning:
            for item in llm.reasoning:
                st.caption(f"• {item}")
        else:
            st.caption("（无推理依据）")


def _ep_conclusion(llm):
    """最终结论 — 彩色高亮卡片（RESTORED）"""
    colors = {
        "BUY":         ("#059669", "#ECFDF5", "#D1FAE5"),  # 绿
        "HOLD":        ("#D97706", "#FFFBEB", "#FDE68A"),  # 琥珀
        "TAKE_PROFIT": ("#B45309", "#FFF7ED", "#FED7AA"),  # 橙
        "REDUCE":      ("#EA580C", "#FFF7ED", "#FDBA74"),  # 橙红
        "SELL":        ("#DC2626", "#FEF2F2", "#FECACA"),  # 红
        "STOP_LOSS":   ("#991B1B", "#FFF1F2", "#FECDD3"),  # 深红
    }
    text_c, bg_c, border_c = colors.get(llm.decision, ("#64748B", "#F8FAFC", "#E2E8F0"))

    st.markdown("**🏁 最终结论**")
    st.markdown(
        f'<div style="background:{bg_c};border:1px solid {border_c};border-radius:8px;'
        f'padding:12px 16px;margin:4px 0;">'
        f'<span style="font-size:22px">{llm.decision_emoji}</span>'
        f'<span style="font-size:18px;font-weight:700;color:{text_c};margin-left:10px">'
        f'{llm.decision_cn}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if llm.is_fallback:
        st.caption(f"⚠️ AI 推理不可用：{llm.error}")
    if llm.decision_corrected:
        st.caption(f"ℹ️ 原始输出「{llm.original_decision}」已自动修正")

    if llm.strategy:
        st.caption("**操作建议**")
        for s in llm.strategy:
            st.caption(f"• {s}")

    if llm.risk:
        st.caption("**风险提示**")
        for r in llm.risk:
            st.caption(f"• {r}")


def _ep_portfolio_overview(data):
    """持仓概览（组合级别意图用，展示全部标的）"""
    with st.expander("📊 持仓概览", expanded=False):
        for w in (data.data_warnings or []):
            if w.level == "warning":
                st.caption(f"⚠️ {w.message}")
        st.caption(
            f"- 组合总市值：**¥{data.total_assets:,.0f}**，共 **{len(data.positions)}** 个标的"
        )
        top = sorted(data.positions, key=lambda p: p.weight, reverse=True)[:8]
        for p in top:
            st.caption(
                f"- {p.name}：仓位 **{p.weight:.1%}**，"
                f"市值 **¥{p.market_value_cny:,.0f}**，"
                f"收益率 **{p.profit_loss_rate:.1%}**"
            )
        if len(data.positions) > 8:
            st.caption(f"_（仅显示前 8 大持仓）_")


def _ep_portfolio_review_result(llm):
    """组合评估结果面板"""
    payload = llm.raw_payload

    risk_level = payload.get("risk_level", "")
    if risk_level:
        risk_icon = {"高": "🔴", "中": "🟡", "低": "🟢"}.get(risk_level, "⚪")
        st.markdown(f"**组合风险等级**：{risk_icon} {risk_level}")
        st.divider()

    findings = payload.get("key_findings", [])
    if findings:
        with st.expander("🔍 核心发现", expanded=True):
            for f in findings:
                st.caption(f"• {f}")

    issues = payload.get("concentration_issues", [])
    if issues:
        with st.expander("⚠️ 集中度问题", expanded=False):
            for i in issues:
                st.caption(f"• {i}")

    rebalance_needed = payload.get("rebalance_needed", False)
    suggestions = payload.get("rebalance_suggestions", [])
    if rebalance_needed and suggestions:
        with st.expander("🔄 调仓建议", expanded=False):
            for s in suggestions:
                st.caption(f"• {s}")
    elif not rebalance_needed:
        st.caption("✅ 当前组合暂无调仓需求")


def _ep_asset_allocation_result(llm):
    """资产配置结果面板"""
    payload = llm.raw_payload

    principles = payload.get("allocation_principles", [])
    if principles:
        with st.expander("📌 配置原则", expanded=True):
            for p in principles:
                st.caption(f"• {p}")

    suggestions = payload.get("allocation_suggestions", [])
    if suggestions:
        with st.expander("🧩 配置方向", expanded=False):
            for s in suggestions:
                direction = s.get("direction", "")
                asset_class = s.get("asset_class", "")
                rationale = s.get("rationale", "")
                dir_icon = {"增加": "📈", "减少": "📉", "维持": "➡️"}.get(direction, "")
                st.caption(f"• {dir_icon} **{asset_class}**（{direction}）：{rationale}")

    risks = payload.get("risks", [])
    if risks:
        with st.expander("⚠️ 风险提示", expanded=False):
            for r in risks:
                st.caption(f"• {r}")


def _ep_performance_analysis_result(llm):
    """收益分析结果面板"""
    payload = llm.raw_payload

    summary = payload.get("summary", "")
    if summary:
        st.markdown(f"**收益概况**：{summary}")
        st.divider()

    drivers = payload.get("key_drivers", [])
    if drivers:
        with st.expander("📈 收益驱动", expanded=True):
            for d in drivers:
                st.caption(f"• {d}")

    losses = payload.get("loss_reasons", [])
    if losses:
        with st.expander("📉 亏损/拖累来源", expanded=False):
            for l in losses:
                st.caption(f"• {l}")

    improvements = payload.get("improvement_suggestions", [])
    if improvements:
        with st.expander("💡 改进建议", expanded=False):
            for i in improvements:
                st.caption(f"• {i}")
