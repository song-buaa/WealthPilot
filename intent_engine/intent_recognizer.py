"""
IntentRecognizer — 意图识别模块

对应工程PRD §3.1。

职责：
    接收用户自然语言输入，调用 LLM 输出标准化 IntentPayload。

LLM 调用规格（PRD §3.1）：
    - 模型：gpt-4.1（via OpenAI API，见 _llm_client.MODEL_MAIN）
    - 超时：10s
    - 重试：最多2次（JSON 解析失败或格式校验失败时重试）
    - 输出格式：强制 JSON，不含 markdown 包裹

校验规则（PRD §3.1 输出校验规则）：
    1. JSON 格式合法
    2. primary_intent 是合法的 IntentType 枚举值
    3. confidence 在 0~1 之间
    4. subtasks 中每个值都属于 primary_intent 对应的合法 SubtaskType

异常处理（PRD §5.2）：
    重试2次后仍失败 → 默认路由至 Education Intent

置信度处理（PRD §5.1）：
    ≥ 0.75  → 正常执行
    0.5~0.74 → 执行，输出追加澄清问题（TODO Phase 2）
    < 0.5   → 不执行，返回澄清问题

TODO Phase 3: 实体标准化（SymbolSearchAPI 调用）
TODO Phase 2: 置信度澄清问题生成（0.5~0.74 区间）
"""
from __future__ import annotations

import json
import re
import traceback
from typing import Optional

import openai

from .types import (
    INTENT_SUBTASK_MAP,
    VALID_INTENTS,
    IntentEntities,
    IntentPayload,
)
from ._llm_client import MODEL_MAIN, get_client, reset_client

# ── 置信度阈值（PRD §5.1）────────────────────────────────────────────────────
CONFIDENCE_EXECUTE = 0.75       # ≥ 此值：正常执行
CONFIDENCE_CLARIFY = 0.50       # 0.5~0.74：执行 + 追加澄清（TODO Phase 2）
# < CONFIDENCE_CLARIFY: 不执行，返回澄清问题

# ── System Prompt（PRD §3.1）─────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
你是一个投资意图识别系统。你的唯一任务是将用户输入解析为标准 JSON 结构。

# 意图定义（primary_intent 必须从以下5个中选一）

1. PortfolioReview（组合评估）：用户关注的是整体组合结构本身，没有具体的单一标的操作焦点
   → 典型：「我的组合风险太高了怎么调」「整体仓位结构合不合理」「帮我看看我的持仓分布」
   → 注意：仅当用户没有明确单一标的操作意图时才选此类

2. AssetAllocation（资产配置）：明确资金规模配置（如"20万怎么投"）、比例分配

3. PositionDecision（单标的决策）：用户问题的核心操作焦点是某一个具体标的
   → 典型：「苹果要不要减持」「特斯拉加仓还是持有」「理想汽车占我仓位太高了要不要调」
   → 关键：即使用户提到了"总仓位""组合"等词语，只要操作焦点是某个单一标的，就选 PositionDecision

4. PerformanceAnalysis（收益分析）：盈亏/回撤/收益分析，无明确交易动作

5. Education（投教/通用）：投资知识性问题、非决策类

# 意图判断核心原则（替代硬性优先级）
判断依据是「用户问题的操作焦点」，而非「句子中出现了哪些词」：
- 用户明确提到某个具体标的，且问题核心是对该标的的操作 → PositionDecision（不管句子里有没有"总仓位""组合"等词）
- 用户没有具体标的，泛问整体组合结构 → PortfolioReview
- 容易混淆的判断示例：
  · 「苹果占我总仓位太高了，要不要减持」→ PositionDecision（苹果是操作焦点，"总仓位"只是描述背景）
  · 「我的组合里苹果和理想都太高了，整体怎么调」→ PortfolioReview（无单一焦点，讨论整体结构）
  · 「英伟达涨太多了，我是不是该换成债基」→ PositionDecision（英伟达是焦点，actions=[REDUCE]）

# Subtask 定义（subtasks 字段的合法值，必须属于 primary_intent 对应的集合）
PortfolioReview:     review, risk_check, concentration_check, rebalance_check
AssetAllocation:     new_cash_allocation, rebalance_allocation, goal_based_allocation
PositionDecision:    thesis_review, position_fit_check, action_evaluation
PerformanceAnalysis: pnl_breakdown, loss_reason, attribution
Education:           concept_explain, rule_explain

# Action 定义（actions 字段的合法值）
交易类：BUY, SELL, ADD, REDUCE, REBALANCE, TAKE_PROFIT, STOP_LOSS
信息类：ANALYZE, VIEW_PERFORMANCE, GET_REPORT, SET_ALERT

# 多标的处理规则（重要，分三种情况）

## 情况A：多标的同操作（用户对N个标的执行相同操作）→ 使用 multi_assets
识别特征：用户对2个或以上明确不同的标的，描述相同的操作动作（同为卖/减仓，或同为买/加仓）
处理方式：
- primary_intent = PositionDecision
- entities.asset = null（不强行选一个）
- entities.multi_assets = [标的1, 标的2, ...]（每个标的名称独立列出）
- 示例："招行和建行的稳健理财要不要卖" → asset=null, multi_assets=["招商银行稳健理财", "建设银行稳健理财"], actions=[SELL]
- 示例："苹果和特斯拉都该加仓吗" → asset=null, multi_assets=["苹果", "特斯拉"], actions=[ADD]

## 情况B：换仓操作（卖A买B）→ 以SELL侧为主，BUY侧不处理
识别特征：明确的换仓意图，SELL侧可能有多个标的
处理方式：
- SELL侧多个标的 → multi_assets = SELL侧标的列表，asset=null，actions=[SELL]
- BUY侧标的不进入multi_assets（当前流程只处理SELL侧）
- 示例："卖掉招行和建行的稳健理财，加仓到特斯拉" → multi_assets=["招商银行稳健理财", "建设银行稳健理财"], asset=null, actions=[SELL]

## 情况C：单焦点标的（背景中有其他标的名称）→ 保持原有规则
识别特征：其他标的只作为宏观背景/参照/触发条件，不是操作对象
处理方式：
- entities.asset = 操作焦点标的，entities.multi_assets = []
- 示例："Coinbase 亏了40%，如果比特币继续下跌该不该割肉" → asset="Coinbase", multi_assets=[]
- 示例："英伟达涨了，AMD 要不要买" → asset="AMD", multi_assets=[]
- 即使用户没有说明是否在持仓中，只要能明确识别出操作主体标的，confidence 就应 ≥ 0.8

# 输出格式要求
- 必须输出合法 JSON，不含任何解释文字和 markdown 包裹
- primary_intent 必须且只能有1个
- secondary_intents 最多2个，可为空数组
- subtasks 必须属于 primary_intent 对应的合法值
- confidence 范围 0~1（标的清晰+意图明确→0.8~1.0；标的清晰但意图模糊→0.5~0.7；完全不明→0.1~0.4）
- 所有字段必须存在
- multi_assets：单标的场景填空数组 []；多标的同操作时填标的列表

# 输出示例（单标的）
{
  "primary_intent": "PositionDecision",
  "secondary_intents": [],
  "subtasks": ["thesis_review", "position_fit_check", "action_evaluation"],
  "actions": ["SELL"],
  "entities": {
    "asset": "理想汽车",
    "asset_normalized": null,
    "capital": null,
    "capital_amount": null,
    "portfolio_id": null,
    "time_horizon": null,
    "multi_assets": []
  },
  "confidence": 0.9
}

# 输出示例（多标的同操作）
{
  "primary_intent": "PositionDecision",
  "secondary_intents": [],
  "subtasks": ["position_fit_check", "action_evaluation"],
  "actions": ["SELL"],
  "entities": {
    "asset": null,
    "asset_normalized": null,
    "capital": null,
    "capital_amount": null,
    "portfolio_id": null,
    "time_horizon": null,
    "multi_assets": ["招商银行稳健理财", "建设银行稳健理财"]
  },
  "confidence": 0.9
}
"""

# ── 兜底 payload（PRD §5.2：连续失败后路由至 Education）─────────────────────
def _fallback_payload(user_input: str) -> IntentPayload:
    return IntentPayload(
        primary_intent="Education",
        secondary_intents=[],
        subtasks=["concept_explain"],
        actions=["ANALYZE"],
        entities=IntentEntities(),
        confidence=0.3,
    )


# ── 核心函数 ──────────────────────────────────────────────────────────────────

def recognize(user_input: str) -> tuple[IntentPayload, Optional[str]]:
    """
    识别用户意图，返回 (IntentPayload, clarification_question)。

    clarification_question 仅在以下情况非空：
    - confidence < 0.5 → 不执行，仅返回澄清问题
    - 0.5~0.74 → 执行，但 clarification 为 TODO 占位（Phase 2 实现）

    Returns:
        (payload, clarification_question)
        若 clarification_question 非空且 confidence < 0.5，调用方应中断执行。
    """
    if not user_input or not user_input.strip():
        return _fallback_payload(user_input), "请描述您的投资问题，例如：'理想汽车要不要卖？'"

    last_error: Optional[Exception] = None

    for attempt in range(3):  # 最多3次（初始1次 + 重试2次，PRD §3.1）
        try:
            client = get_client()
            response = client.chat.completions.create(
                model=MODEL_MAIN,
                max_tokens=512,
                timeout=10,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_input},
                ],
            )
            raw = response.choices[0].message.content.strip()
            data = _extract_json(raw)
            payload = _build_payload(data)
            _validate(payload)  # 校验失败会抛出 ValueError，触发重试

            # 置信度判断（PRD §5.1）
            clarification: Optional[str] = None
            if payload.confidence < CONFIDENCE_CLARIFY:
                # confidence < 0.5：不执行，返回澄清问题
                clarification = _make_clarification_question(payload, user_input)
            elif payload.confidence < CONFIDENCE_EXECUTE:
                # 0.5~0.74：执行但标记（TODO Phase 2：生成真实澄清问题）
                pass  # TODO Phase 2: generate clarification question for low-confidence intents

            return payload, clarification

        except EnvironmentError:
            raise  # 无 API Key，直接向上传递

        except (ValueError, KeyError, json.JSONDecodeError) as e:
            # JSON 解析失败或格式校验失败 → 重试（PRD §3.1）
            last_error = e
            reset_client()
            continue

        except openai.APITimeoutError as e:
            last_error = e
            break

        except Exception as e:
            last_error = e
            reset_client()
            continue

    # 重试2次后仍失败 → 默认 Education（PRD §5.2）
    tb = traceback.format_exc()
    print(f"[IntentRecognizer] 连续失败，路由至 Education。最后错误:\n{tb}", flush=True)
    return _fallback_payload(user_input), (
        "我没能完全理解你的问题，你可以换一种方式描述，"
        "或者告诉我你想分析哪个标的/组合？"
    )


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """从 LLM 输出中稳健提取 JSON（兼容 markdown 代码块包裹）。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 提取 ```json ... ``` 代码块
    block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if block:
        return json.loads(block.group(1))

    # 提取第一个 { ... } 块
    brace = re.search(r'\{.*\}', text, re.DOTALL)
    if brace:
        return json.loads(brace.group())

    raise ValueError(f"无法从 LLM 输出中提取 JSON: {text[:200]}")


def _build_payload(data: dict) -> IntentPayload:
    """将 LLM 输出的 dict 转换为 IntentPayload 数据类。"""
    ent = data.get("entities") or {}
    raw_multi = ent.get("multi_assets") or []
    entities = IntentEntities(
        asset=ent.get("asset"),
        asset_normalized=ent.get("asset_normalized"),
        capital=ent.get("capital"),
        capital_amount=ent.get("capital_amount"),
        portfolio_id=ent.get("portfolio_id"),
        time_horizon=ent.get("time_horizon"),
        multi_assets=[str(a) for a in raw_multi if a] if isinstance(raw_multi, list) else [],
    )
    # TODO Phase 3: 调用 SymbolSearchAPI 标准化 entities.asset → asset_normalized
    # TODO Phase 3: 调用本地中文数字解析库标准化 entities.capital → capital_amount

    return IntentPayload(
        primary_intent=str(data.get("primary_intent", "")),
        secondary_intents=list(data.get("secondary_intents") or []),
        subtasks=list(data.get("subtasks") or []),
        actions=list(data.get("actions") or []),
        entities=entities,
        confidence=float(data.get("confidence", 0.5)),
    )


def _validate(payload: IntentPayload) -> None:
    """
    校验 IntentPayload（PRD §3.1 输出校验规则）。
    校验失败抛出 ValueError，触发重试。
    """
    # 规则1：primary_intent 必须是合法枚举值
    if payload.primary_intent not in VALID_INTENTS:
        raise ValueError(f"非法 primary_intent: {payload.primary_intent!r}")

    # 规则2：confidence 在 0~1 之间
    if not (0.0 <= payload.confidence <= 1.0):
        raise ValueError(f"confidence 超出范围: {payload.confidence}")

    # 规则3：subtasks 中每个值都属于 primary_intent 对应的合法集合
    valid_subtasks = INTENT_SUBTASK_MAP.get(payload.primary_intent, set())
    for st in payload.subtasks:
        if st not in valid_subtasks:
            raise ValueError(
                f"subtask {st!r} 不属于 {payload.primary_intent} 的合法 Subtask"
            )


def _make_clarification_question(payload: IntentPayload, user_input: str) -> str:
    """
    生成澄清问题（confidence < 0.5 时调用，PRD §5.1）。
    Phase 1 使用固定模板；TODO Phase 2: 使用 LLM 动态生成封闭式澄清问题。
    """
    # TODO Phase 2: 根据当前识别到的 Intent 候选，调用 LLM 生成1个封闭式问题
    # 示例："你是想了解理想汽车这个股票本身，还是想看它在你组合里的情况？"
    asset = payload.entities.asset
    if asset:
        return f"你是想了解 {asset} 的投资分析，还是想对它做具体的买入/卖出决策？"
    return "您能描述一下您想分析哪个标的，以及想做什么操作（买入、卖出、还是持有判断）吗？"
