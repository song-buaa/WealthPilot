"""
资产配置模块 — AI 对话服务

负责：
1. 意图识别（初始配置 / 增量补配 / 诊断 / 解释 / 概念）
2. 根据意图构建 System Prompt
3. 调用 OpenAI GPT-4.1 生成方案
4. 后处理：纪律校验 + block 级自动修正
5. 管理 sessionContext
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

import openai

from app import state
from app.allocation.types import (
    AllocationAIResponse,
    AllocationChatRequest,
    AllocationChatResponse,
    AllocationIntentType,
    ExplainPanelData,
    SessionContext,
    ALLOC_LABEL,
)
from backend.services.allocation_service import (
    compute_increment_plan,
    compute_initial_plan,
    get_deviation,
    get_snapshot,
    get_targets,
)
from backend.services.portfolio_service import get_summary
from backend.services.profile_service import get_profile

# ── 与首页 ALLOC_CATS 一致的目标区间（用于状态描述）──────────

ALLOC_CATS = [
    {"key": "monetary",     "label": "货币", "minPct": 0.8,  "maxPct": 8.2},
    {"key": "fixed_income", "label": "固收", "minPct": 20,   "maxPct": 60},
    {"key": "equity",       "label": "权益", "minPct": 40,   "maxPct": 80},
    {"key": "alternative",  "label": "另类", "minPct": 0,    "maxPct": 10},
    {"key": "derivative",   "label": "衍生", "minPct": 0,    "maxPct": 10},
]


def _calc_health_from_summary(allocation: dict) -> dict:
    """基于 portfolio summary + ALLOC_CATS 计算健康状态，与首页完全一致"""
    worst = "on_target"
    max_abs = 0
    max_label = None
    max_text = None

    severity_map = {"on_target": 0, "mild": 1, "significant": 2, "alert": 3}

    for cat in ALLOC_CATS:
        cur = allocation.get(cat["key"], {}).get("pct", 0)
        mn, mx = cat["minPct"], cat["maxPct"]
        label = cat["label"]
        dev_text = None
        level = "on_target"
        abs_dev = 0

        if cur > mx:
            abs_dev = cur - mx
            dev_text = f"超配 +{abs_dev:.1f}%"
            level = "alert" if abs_dev > 5 else "significant" if abs_dev > 2 else "mild"
        elif mn > 0 and cur < mn:
            abs_dev = mn - cur
            dev_text = f"低配 −{abs_dev:.1f}%"
            level = "alert" if abs_dev > 5 else "significant" if abs_dev > 2 else "mild"

        if severity_map.get(level, 0) > severity_map.get(worst, 0):
            worst = level
        if abs_dev > max_abs:
            max_abs = abs_dev
            max_label = label
            max_text = dev_text

    action_hints = {
        "on_target": "暂不处理",
        "mild": "建议后续用新增资金修正",
        "significant": "建议后续用新增资金修正",
        "alert": "需要尽快关注",
    }
    status_labels = {
        "on_target": "接近目标",
        "mild": "轻微偏离",
        "significant": "明显偏离",
        "alert": "需要关注",
    }

    return {
        "level": worst,
        "status_label": status_labels[worst],
        "max_label": max_label,
        "max_text": max_text,
        "action_hint": action_hints[worst],
    }

# ── LLM 客户端 ───────────────────────────────────────────

_client: Optional[openai.OpenAI] = None
MODEL = "gpt-4.1"


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("未找到 OPENAI_API_KEY 环境变量")
        _client = openai.OpenAI(api_key=api_key)
    return _client


# ── 意图识别 ─────────────────────────────────────────────

INTENT_SYSTEM_PROMPT = """你是 WealthPilot 资产配置模块的意图识别器。
根据用户输入，判断属于以下哪种意图：

1. INITIAL_ALLOCATION — 用户想从零开始配置（如"我有100万还没做配置"、"帮我规划资产"）
2. INCREMENT_ALLOCATION — 用户有新增资金需要分配（如"下个月发30万年终奖"、"又有一笔钱"）
3. DIAGNOSIS — 用户想了解当前配置状态（如"我现在配置合理吗"、"帮我看看配置"）
4. EXPLAIN — 用户问为什么这么配（如"为什么建议这个比例"）
5. CONCEPT — 用户问概念性问题（如"什么是另类资产"、"衍生品是什么"）

只返回意图标识，不要其他内容。如果不确定，返回 DIAGNOSIS。"""


async def _detect_intent(
    message: str,
    session_context: Optional[SessionContext],
) -> AllocationIntentType:
    """通过 LLM 识别用户意图"""
    # 快捷关键词匹配
    if any(kw in message for kw in ["从零", "初始", "还没做配置", "帮我规划", "开始配置"]):
        return AllocationIntentType.INITIAL_ALLOCATION
    if any(kw in message for kw in ["新增", "年终奖", "又有", "多出", "补配", "加钱"]):
        return AllocationIntentType.INCREMENT_ALLOCATION
    if any(kw in message for kw in ["为什么", "为啥", "什么原因", "逻辑是"]):
        return AllocationIntentType.EXPLAIN
    if any(kw in message for kw in ["什么是", "是什么", "解释一下"]):
        return AllocationIntentType.CONCEPT

    # LLM 兜底
    try:
        client = _get_client()
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model=MODEL,
            messages=[
                {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            max_tokens=50,
            temperature=0,
        )
        intent_str = resp.choices[0].message.content.strip().upper()
        return AllocationIntentType(intent_str)
    except Exception:
        return AllocationIntentType.DIAGNOSIS


# ── 主入口 ───────────────────────────────────────────────

async def handle_chat(req: AllocationChatRequest) -> AllocationChatResponse:
    """处理资产配置 AI 对话请求"""
    session_ctx = req.session_context or SessionContext()
    intent = await _detect_intent(req.message, session_ctx)

    if intent == AllocationIntentType.INITIAL_ALLOCATION:
        return await _handle_initial(req, session_ctx)
    elif intent == AllocationIntentType.INCREMENT_ALLOCATION:
        return await _handle_increment(req, session_ctx)
    elif intent == AllocationIntentType.DIAGNOSIS:
        return await _handle_diagnosis(req, session_ctx)
    elif intent == AllocationIntentType.EXPLAIN:
        return await _handle_explain(req, session_ctx)
    elif intent == AllocationIntentType.CONCEPT:
        return await _handle_concept(req, session_ctx)
    else:
        return await _handle_diagnosis(req, session_ctx)


# ── 流程 A：初始配置 ─────────────────────────────────────

async def _handle_initial(
    req: AllocationChatRequest,
    session_ctx: SessionContext,
) -> AllocationChatResponse:
    """从零开始的初始配置方案"""
    pid = state.portfolio_id

    # 检查是否已有持仓
    snapshot = get_snapshot(pid)
    has_positions = snapshot.total_investable_assets > 0

    # 尝试从消息中提取金额
    amount = _extract_amount(req.message) or session_ctx.confirmed_increment_amount

    if amount is None:
        # 需要追问金额
        return AllocationChatResponse(
            intent_type=AllocationIntentType.INITIAL_ALLOCATION.value,
            response=AllocationAIResponse(
                diagnosis="需要了解你的规划金额",
                logic="请告诉我你打算规划多少资金？这样我才能给出具体的配置方案。",
                explain_panel=ExplainPanelData(
                    tools_called=[],
                    key_data={"status": "等待用户输入规划金额"},
                    reasoning="需要用户提供规划金额后才能生成配置方案",
                ),
            ),
            updated_session_context=session_ctx,
        )

    session_ctx.confirmed_increment_amount = amount

    # 获取用户画像和目标区间
    profile = get_profile()
    targets = get_targets()

    # 计算初始方案
    if has_positions and not session_ctx.confirmed_replanning:
        # 已有持仓，询问是基于当前补配还是重新规划
        return AllocationChatResponse(
            intent_type=AllocationIntentType.INITIAL_ALLOCATION.value,
            response=AllocationAIResponse(
                diagnosis="你已有一些持仓",
                logic=f"检测到你当前已有 {snapshot.total_investable_assets:,.0f} 元的投资资产。\n\n"
                      "你想：\n"
                      "1. 基于当前持仓，规划这笔新增资金（增量补配）\n"
                      "2. 不考虑当前持仓，从头规划整体配置\n\n"
                      "请选择 1 或 2，或直接告诉我你的想法。",
                explain_panel=ExplainPanelData(
                    tools_called=["getCurrentAllocation"],
                    key_data={"totalAssets": snapshot.total_investable_assets, "incrementAmount": amount},
                    reasoning="检测到已有持仓，需要确认是增量补配还是重新规划",
                ),
            ),
            updated_session_context=session_ctx,
        )

    result = compute_initial_plan(amount)

    # 构建 AI 回复
    tools_called = ["getUserProfile", "getDisciplineParams"]
    key_data = {"totalAmount": amount}

    if profile:
        key_data["riskType"] = getattr(profile, "risk_type", "未设置")
        key_data["aiStyle"] = getattr(profile, "ai_style", "未设置")

    system_prompt = _build_initial_system_prompt(amount, profile, targets, None)
    ai_text = await _call_llm(system_prompt, req.message, req.conversation_history)

    return AllocationChatResponse(
        intent_type=AllocationIntentType.INITIAL_ALLOCATION.value,
        response=AllocationAIResponse(
            diagnosis=f"为 {amount/10000:.0f} 万元规划初始配置",
            logic=ai_text,
            plan={
                "type": "initial",
                "table": [item.model_dump() for item in result.plan_items],
                "totalAmount": amount,
                "discipline": result.discipline_check.model_dump() if result.discipline_check else None,
            },
            risk_note="以上方案已通过纪律校验。建议分批建仓，不必一次性全部买入。",
            explain_panel=ExplainPanelData(
                tools_called=tools_called,
                key_data=key_data,
                reasoning="基于稳健偏进取画像，货币类按下限配置，衍生类不纳入初始方案",
            ),
        ),
        updated_session_context=session_ctx,
    )


# ── 流程 B：增量补配 ─────────────────────────────────────

async def _handle_increment(
    req: AllocationChatRequest,
    session_ctx: SessionContext,
) -> AllocationChatResponse:
    """增量资金补配方案"""
    pid = state.portfolio_id

    amount = _extract_amount(req.message) or session_ctx.confirmed_increment_amount

    if amount is None:
        return AllocationChatResponse(
            intent_type=AllocationIntentType.INCREMENT_ALLOCATION.value,
            response=AllocationAIResponse(
                diagnosis="需要了解新增资金金额",
                logic="你这次打算规划多少资金？请告诉我具体金额。",
                explain_panel=ExplainPanelData(
                    tools_called=[],
                    key_data={"status": "等待用户输入新增资金金额"},
                    reasoning="需要用户提供新增资金金额后才能生成补配方案",
                ),
            ),
            updated_session_context=session_ctx,
        )

    session_ctx.confirmed_increment_amount = amount

    # 检查用户是否提到衍生品
    user_requested_deriv = session_ctx.user_requested_deriv or any(
        kw in req.message for kw in ["衍生", "期权", "期货", "对冲"]
    )
    session_ctx.user_requested_deriv = user_requested_deriv

    snapshot = get_snapshot(pid)
    deviation = get_deviation(pid)
    targets = get_targets()
    profile = get_profile()

    # 用与首页一致的状态计算
    summary = get_summary(pid)
    health = _calc_health_from_summary(summary.get("allocation", {}) if isinstance(summary, dict) else {})

    result = compute_increment_plan(
        portfolio_id=pid,
        increment_amount=amount,
        user_requested_deriv=user_requested_deriv,
    )

    tools_called = ["getUserProfile", "getDisciplineParams", "getCurrentAllocation", "getDeviationSnapshot"]
    key_data = {
        "incrementAmount": amount,
        "overallStatus": health["status_label"],
        "cashStatus": deviation.cash.status.value,
    }

    system_prompt = _build_increment_system_prompt(
        amount, snapshot, deviation, targets, profile, None,
    )
    ai_text = await _call_llm(system_prompt, req.message, req.conversation_history)

    return AllocationChatResponse(
        intent_type=AllocationIntentType.INCREMENT_ALLOCATION.value,
        response=AllocationAIResponse(
            diagnosis=f"为 {amount/10000:.0f} 万元新增资金制定补配方案",
            logic=ai_text,
            plan={
                "type": "increment",
                "table": [item.model_dump() for item in result.plan_items],
                "totalAmount": amount,
                "discipline": result.discipline_check.model_dump() if result.discipline_check else None,
            },
            risk_note="以上方案已通过纪律校验。超配类别不建议继续加仓，已有持仓可加仓标的优先。",
            explain_panel=ExplainPanelData(
                tools_called=tools_called,
                key_data=key_data,
                reasoning="基于当前偏离度，按优先级分配新增资金：货币下限 → 另类补足 → 固收权益平衡",
            ),
        ),
        updated_session_context=session_ctx,
    )


# ── 流程 C：诊断咨询 ─────────────────────────────────────

async def _handle_diagnosis(
    req: AllocationChatRequest,
    session_ctx: SessionContext,
) -> AllocationChatResponse:
    """当前配置状态诊断"""
    pid = state.portfolio_id

    snapshot = get_snapshot(pid)

    if snapshot.total_investable_assets <= 0:
        return AllocationChatResponse(
            intent_type=AllocationIntentType.DIAGNOSIS.value,
            response=AllocationAIResponse(
                status_conclusion="暂无投资持仓",
                deviation_detail="当前没有可投资资产记录，无法进行配置诊断。",
                action_direction={
                    "level": "no_action",
                    "description": "请先在账户总览中导入你的持仓数据，或者直接开始规划初始配置。",
                },
                explain_panel=ExplainPanelData(
                    tools_called=["getCurrentAllocation"],
                    key_data={"totalAssets": 0, "overallStatus": "暂无持仓"},
                    reasoning="未检测到投资持仓数据，无法进行配置诊断",
                ),
            ),
            updated_session_context=session_ctx,
        )

    deviation = get_deviation(pid)

    # 用与首页一致的状态计算（基于 portfolio summary + ALLOC_CATS）
    summary = get_summary(pid)
    health = _calc_health_from_summary(summary.get("allocation", {}) if isinstance(summary, dict) else {})

    tools_called = ["getCurrentAllocation", "getDeviationSnapshot"]
    key_data = {
        "totalAssets": snapshot.total_investable_assets,
        "overallStatus": health["status_label"],
        "actionHint": health["action_hint"],
    }

    # 构建偏离详情（基于 ALLOC_CATS 区间）
    deviation_details = []
    alloc = summary.get("allocation", {}) if isinstance(summary, dict) else {}
    for cat in ALLOC_CATS:
        cur = alloc.get(cat["key"], {}).get("pct", 0)
        mn, mx = cat["minPct"], cat["maxPct"]
        label = cat["label"]
        if cur > mx:
            deviation_details.append(f"{label}: {cur:.1f}% (目标 {mn}%~{mx}%) — 超配 +{cur-mx:.1f}%")
        elif mn > 0 and cur < mn:
            deviation_details.append(f"{label}: {cur:.1f}% (目标 {mn}%~{mx}%) — 低配 −{mn-cur:.1f}%")
        else:
            deviation_details.append(f"{label}: {cur:.1f}% (目标 {mn}%~{mx}%) — 在区间内")

    targets = get_targets()
    system_prompt = _build_diagnosis_system_prompt(deviation, targets)
    ai_text = await _call_llm(system_prompt, req.message, req.conversation_history)

    return AllocationChatResponse(
        intent_type=AllocationIntentType.DIAGNOSIS.value,
        response=AllocationAIResponse(
            status_conclusion=health["status_label"],
            deviation_detail=ai_text or "\n".join(deviation_details),
            action_direction={
                "level": health["level"],
                "description": health["action_hint"],
            },
            explain_panel=ExplainPanelData(
                tools_called=tools_called,
                key_data=key_data,
                reasoning="诊断基于当前持仓占比与目标区间的偏离度计算，与首页看板数据口径一致",
            ),
        ),
        updated_session_context=session_ctx,
    )


# ── 流程：解释 ───────────────────────────────────────────

async def _handle_explain(
    req: AllocationChatRequest,
    session_ctx: SessionContext,
) -> AllocationChatResponse:
    """解释配置逻辑"""
    system_prompt = (
        "你是 WealthPilot 资产配置顾问。用户问为什么这么配。\n\n"
        "关键原则：\n"
        "- 五大类资产各有分工：货币保流动性、固收降波动、权益求增长、另类分散风险、衍生品只做战术工具\n"
        "- 目标区间由用户画像和投资纪律共同决定\n"
        "- 市场信息只影响同类内部优先顺序和建仓节奏，不改变大类配置\n"
        "- 另类是战略配置（应该有），衍生是战术工具（可以用但要控制）\n\n"
        "输出格式（严格遵守，不得改变结构）：\n"
        "每个概念按以下固定结构输出，概念之间用 \"---\" 分隔：\n\n"
        "**[概念名称]**\n"
        "[一句话定义，≤30字]\n\n"
        "- [要点1]\n"
        "- [要点2]\n"
        "- [要点3]（可选）\n\n"
        "概念之间必须有 \"---\" 分隔线。\n"
        "全程只使用无序列表（\"-\"），不使用有序列表（\"1. 2. 3.\"）。\n"
        "不使用\"含义：\"\"作用：\"\"好处：\"等子标题，直接用列表展开要点。\n"
        "结尾用一句话总结，前面加\"**总结：**\"。"
    )

    ai_text = await _call_llm(system_prompt, req.message, req.conversation_history)

    return AllocationChatResponse(
        intent_type=AllocationIntentType.EXPLAIN.value,
        response=AllocationAIResponse(
            diagnosis=None,
            logic=ai_text,
            explain_panel=ExplainPanelData(
                tools_called=[],
                key_data={"type": "解释类问答"},
                reasoning="本次为解释类问答，未调用持仓数据",
            ),
        ),
        updated_session_context=session_ctx,
    )


# ── 流程：概念问答 ───────────────────────────────────────

async def _handle_concept(
    req: AllocationChatRequest,
    session_ctx: SessionContext,
) -> AllocationChatResponse:
    """概念性问答"""
    system_prompt = (
        "你是 WealthPilot 资产配置顾问。回答用户关于投资概念的问题。\n"
        "用简洁清晰的语言回答，结合 WealthPilot 五大类资产框架（货币/固收/权益/另类/衍生）。\n"
        "不调用工具，直接回答。\n\n"
        "输出格式（严格遵守，不得改变结构）：\n"
        "每个概念按以下固定结构输出，概念之间用 \"---\" 分隔：\n\n"
        "**[概念名称]**\n"
        "[一句话定义，≤30字]\n\n"
        "- [要点1]\n"
        "- [要点2]\n"
        "- [要点3]（可选）\n\n"
        "概念之间必须有 \"---\" 分隔线。\n"
        "全程只使用无序列表（\"-\"），不使用有序列表（\"1. 2. 3.\"）。\n"
        "不使用\"含义：\"\"作用：\"\"好处：\"等子标题，直接用列表展开要点。\n"
        "结尾用一句话总结，前面加\"**总结：**\"。"
    )

    ai_text = await _call_llm(system_prompt, req.message, req.conversation_history)

    return AllocationChatResponse(
        intent_type=AllocationIntentType.CONCEPT.value,
        response=AllocationAIResponse(
            logic=ai_text,
            explain_panel=ExplainPanelData(
                tools_called=[],
                key_data={"type": "概念问答"},
                reasoning="本次为概念问答，未调用持仓数据",
            ),
        ),
        updated_session_context=session_ctx,
    )


# ── System Prompt 构建 ───────────────────────────────────

def _build_initial_system_prompt(amount, profile, targets, market_ctx):
    target_desc = _format_targets(targets)
    profile_desc = _format_profile(profile)

    return f"""你是 WealthPilot 的资产配置顾问。用户需要一个初始配置方案。

已读取信息：
- 用户画像：{profile_desc}
- 五大类目标区间：{target_desc}

用户规划金额：{amount:,.0f} 元

输出要求（用自然语言，简洁专业）：
1. 说明配置逻辑（为什么这么分，2-3 段）
2. 提及关键约束（货币类按下限配，衍生类不纳入初始方案）
3. 给出执行节奏建议（是否分批建仓）

约束：
- 方案已由系统计算生成，你只需说明逻辑和风险提示
- 衍生类不得出现在方案中
- 货币类只分配到下限，不追求中值
- 市场信息只影响建仓节奏建议

格式规范：
- 列表统一使用无序列表（-），不要使用有序列表（1. 2. 3.）
- 仅当内容有明确步骤顺序时才可用有序列表，且有序列表和无序列表不能在同一层级混用"""


def _build_increment_system_prompt(amount, snapshot, deviation, targets, profile, market_ctx):
    target_desc = _format_targets(targets)
    profile_desc = _format_profile(profile)
    snapshot_desc = _format_snapshot(snapshot)
    deviation_desc = _format_deviation(deviation, targets)

    return f"""你是 WealthPilot 的资产配置顾问。用户有一笔新增资金需要分配。

已读取信息：
- 用户画像：{profile_desc}
- 当前配置：{snapshot_desc}
- 偏离情况：{deviation_desc}
- 新增金额：{amount:,.0f} 元
- 五大类目标区间：{target_desc}

输出要求（用自然语言，简洁专业）：
1. 说明补配逻辑（基于偏离度，为什么优先补这几类）
2. 提及关键约束和风险点
3. 给出执行节奏建议

约束：
- 方案已由系统计算生成，你只需说明逻辑和风险提示
- 衍生类默认不参与补配
- 超配类别不建议继续加仓
- 已有持仓中可加仓标的优先于新建仓

格式规范：
- 列表统一使用无序列表（-），不要使用有序列表（1. 2. 3.）
- 仅当内容有明确步骤顺序时才可用有序列表，且有序列表和无序列表不能在同一层级混用"""


def _build_diagnosis_system_prompt(deviation, targets=None):
    deviation_desc = _format_deviation(deviation, targets)

    return f"""你是 WealthPilot 的资产配置顾问。用户想了解当前配置状态。

当前各类资产配置情况：
{deviation_desc}

输出格式（严格遵守，不得改变结构）：

第一段：一句话说明整体状态，直接说结论，例如：
"你当前的五大类配置整体处于正常区间，无需主动调整。"

第二段：逐条列出各类状态，只列出需要关注的项，在区间内的可以合并一句带过：
- 货币：X万元，充足/略低/不足
- 固收：XX%，目标区间X%~X%，在区间内/超出上限Xpp/低于下限Xpp
- 权益：同上格式
- 另类：同上格式
- 衍生：同上格式

第三段：一句话建议方向，直接说人话，例如：
"目前无需操作，有新增资金时优先补货币和另类。"

禁止：
- 禁止输出"状态结论""偏离详情""建议方向"等子标题
- 禁止在建议方向段落前后重复出现"建议方向：暂不处理"这类系统词
- 禁止将目标中值作为唯一参照，必须说明区间范围
- 禁止使用有序列表（1. 2. 3.），只使用无序列表（-）
- 不给具体操作指令，只给方向
- 不推荐具体标的"""


# ── LLM 调用 ─────────────────────────────────────────────

async def _call_llm(system_prompt: str, user_message: str, history: list = None) -> str:
    """调用 OpenAI GPT-4.1"""
    messages = [{"role": "system", "content": system_prompt}]

    if history:
        for msg in history[-6:]:  # 最近 6 条历史
            messages.append({
                "role": msg.role if hasattr(msg, "role") else msg.get("role", "user"),
                "content": msg.content if hasattr(msg, "content") else msg.get("content", ""),
            })

    messages.append({"role": "user", "content": user_message})

    try:
        client = _get_client()
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model=MODEL,
            messages=messages,
            max_tokens=2000,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"AI 服务暂时不可用，请稍后重试。（错误：{str(e)[:100]}）"


# ── 工具函数 ─────────────────────────────────────────────

def _extract_amount(text: str) -> Optional[float]:
    """从用户消息中提取金额"""
    import re

    # 匹配 "100万"、"30万"、"50w"、"200000"
    patterns = [
        (r'(\d+(?:\.\d+)?)\s*万', 10_000),
        (r'(\d+(?:\.\d+)?)\s*[wW]', 10_000),
        (r'(\d+(?:\.\d+)?)\s*千', 1_000),
        (r'(\d+(?:\.\d+)?)\s*[kK]', 1_000),
        (r'(\d{5,})', 1),  # 5 位以上数字直接当元
    ]

    for pattern, multiplier in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1)) * multiplier

    return None


def _format_targets(targets) -> str:
    from app.allocation.types import AllocAssetClass
    lines = []
    for t in targets:
        label = ALLOC_LABEL.get(t.asset_class, t.asset_class.value)
        if t.asset_class == AllocAssetClass.CASH:
            lines.append(f"{label}: {t.cash_min_amount/10000:.0f}万~{t.cash_max_amount/10000:.0f}万 (绝对金额)")
        elif t.floor_ratio is not None:
            lines.append(f"{label}: {t.floor_ratio:.0%}~{t.ceiling_ratio:.0%} (中值 {t.mid_ratio:.0%})")
        else:
            lines.append(f"{label}: 上限 {t.ceiling_ratio:.0%}，无下限")
    return " | ".join(lines)


def _format_profile(profile) -> str:
    if not profile:
        return "未设置"
    parts = []
    if hasattr(profile, "risk_type") and profile.risk_type:
        parts.append(f"风险类型: {profile.risk_type}")
    if hasattr(profile, "ai_style") and profile.ai_style:
        parts.append(f"投资风格: {profile.ai_style}")
    if hasattr(profile, "investment_horizon") and profile.investment_horizon:
        parts.append(f"投资期限: {profile.investment_horizon}")
    if hasattr(profile, "target_return") and profile.target_return:
        parts.append(f"收益目标: {profile.target_return}")
    return " | ".join(parts) if parts else "未设置"


def _format_snapshot(snapshot) -> str:
    from app.allocation.types import AllocAssetClass
    parts = [f"总资产: {snapshot.total_investable_assets:,.0f}元"]
    for cls in ["cash", "fixed", "equity", "alt", "deriv"]:
        ca = snapshot.by_class.get(cls)
        if ca:
            label = ALLOC_LABEL.get(AllocAssetClass(cls), cls)
            parts.append(f"{label}: {ca.amount:,.0f}元({ca.ratio:.1%})")
    return " | ".join(parts)


def _format_deviation(deviation, targets=None) -> str:
    """格式化偏离数据，包含完整目标区间（floor~ceiling），不只是 mid"""
    from app.allocation.types import AllocAssetClass
    target_map = {}
    if targets:
        target_map = {t.asset_class.value: t for t in targets}

    cash_status_cn = {"sufficient": "充足", "low": "略低", "insufficient": "不足"}
    parts = []
    parts.append(f"货币: {deviation.cash.current_amount/10000:.1f}万元, "
                 f"目标{deviation.cash.min_amount/10000:.0f}万~{deviation.cash.max_amount/10000:.0f}万, "
                 f"{cash_status_cn.get(deviation.cash.status.value, deviation.cash.status.value)}")

    for cls_key, dev in deviation.by_class.items():
        try:
            label = ALLOC_LABEL.get(AllocAssetClass(cls_key), cls_key)
        except ValueError:
            label = cls_key
        t = target_map.get(cls_key)
        floor_pct = f"{t.floor_ratio:.0%}" if t and t.floor_ratio is not None else "0%"
        ceiling_pct = f"{t.ceiling_ratio:.0%}" if t else "?"
        in_range = "在区间内" if dev.is_in_range else ("超出上限" if dev.deviation > 0 else "低于下限")
        parts.append(f"{label}: {dev.current_ratio:.1%}, 目标区间{floor_pct}~{ceiling_pct}, {in_range}")

    return "\n".join(parts)
