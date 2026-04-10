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

1. PortfolioReview（组合评估）：用户关注的是整体组合结构本身的健康度、
   风险分布、集中度、抗跌性、是否需要再平衡，没有具体的单一标的操作焦点。
   核心问题是"我的组合结构有没有问题"、"风险分布合不合理"、"抗跌性怎么样"。
   → 典型：「我的组合风险太高了怎么调」「整体仓位结构合不合理」
           「帮我看看我的持仓分布」「我的组合在震荡市里跌得比较多，问题出在哪」
           「我的组合调整过几次了，现在整体是什么状态」
           「我的持仓集中度是不是太高了」
   → 判断关键：用户问的是"为什么结构有问题"或"风险/稳定性怎么样"，
     即使句子里出现"跌""亏"等字，只要核心是结构归因而非收益数字，就选此类
   → 注意：仅当用户没有明确单一标的操作意图时才选此类

2. AssetAllocation（资产配置）：用户有明确的资金要做配置规划，
   核心是"这笔钱怎么分配"，而不是"我的组合怎么样"。
   核心动作：规划、分配、计算具体比例和金额。
   分两种场景：
   - 新增资金：有一笔新钱要做初始配置或重新投入
     → 「我有100万准备投资，怎么分」「一笔理财到期了，重新配置怎么分」
   - 再平衡目标：有明确的调整目标，想知道某类资产加多少/减多少
     → 「我想调整到更稳健的结构，固收应该加多少」「我想增加固收比例，怎么操作」

3. PositionDecision（单标的决策）：用户问题的核心操作焦点是某一个具体标的
   → 典型：「苹果要不要减持」「特斯拉加仓还是持有」「理想汽车占我仓位太高了要不要调」
   → 关键：即使用户提到了"总仓位""组合"等词语，只要操作焦点是某个单一标的，就选 PositionDecision

4. PerformanceAnalysis（收益分析）：用户关注的是具体的盈亏数字、
   收益率表现、和基准的比较、哪些标的贡献了收益或造成了亏损。
   核心问题是"我赚了多少"、"跑赢了吗"、"哪里拖累了收益"。
   → 典型：「这段时间大盘还行，但我的组合收益明显跑输了，为什么」
           「我有几笔投资一直是正收益，但整体算下来并不好看，哪里出了问题」
           「从我现在的持仓来看，哪些标的在拖累整体表现」
   → 判断关键：用户在问收益率数字、跑赢跑输的比较、归因到具体标的的盈亏贡献
   → 注意：如果用户问的是"为什么跌/亏"但焦点是组合结构问题，
     应选 PortfolioReview 而非此类

5. Education（投教/通用）：用户问的是"怎么想/怎么理解/什么方法论"，
   核心是认知、行为习惯、投资方法论，而不是"我的持仓该怎么操作"。
   → 典型：「再平衡怎么做」「分散和集中持仓哪个好」「我总是追涨杀跌怎么破」
           「止损的逻辑是什么」「什么是夏普比率」
   → 判断关键：即使问题中有"对我来说"或可以结合持仓回答，
     只要核心是认知/行为习惯/方法论，不是"某标的该买卖"，就选 Education
   → 注意：行为偏差类（"我总是...""我习惯..."）一律归 Education，
     不得因为持仓中有涨跌标的而误识别为 PositionDecision

# 意图判断核心原则（替代硬性优先级）
判断依据是「用户问题的操作焦点」，而非「句子中出现了哪些词」：
- 用户明确提到某个具体标的，且问题核心是对该标的的操作 → PositionDecision（不管句子里有没有"总仓位""组合"等词）
- 用户没有具体标的，泛问整体组合结构 → PortfolioReview
- 容易混淆的判断示例：
  · 「苹果占我总仓位太高了，要不要减持」→ PositionDecision（苹果是操作焦点，"总仓位"只是描述背景）
  · 「我的组合里苹果和理想都太高了，整体怎么调」→ PortfolioReview（无单一焦点，讨论整体结构）
  · 「英伟达涨太多了，我是不是该换成债基」→ PositionDecision（英伟达是焦点，actions=[REDUCE]）
  · 「我的组合在震荡市里跌得比较多，问题出在哪」→ PortfolioReview（问的是结构问题，"跌得多"是背景，"问题出在哪"是结构归因）
  · 「大盘涨了10%但我只涨了3%，为什么跑输了」→ PerformanceAnalysis（有明确收益率对比，问的是跑赢跑输）
  · 「我的组合波动太大了，怎么降低风险」→ PortfolioReview（关注风险结构，不是收益数字）
  · 「过去这段时间哪些持仓亏损最多」→ PerformanceAnalysis（关注具体标的盈亏归因）
  · 「我现在大部分钱都在股票上，固收留得很少，这样合理吗」→ PortfolioReview（评估现有结构是否合理，没有新增资金要分配）
  · 「我的海外持仓和A股比例有点失衡，需要调整吗」→ PortfolioReview（评估现有持仓的结构问题，不是资金配置规划）
  · 「我有100万准备开始投资，应该怎么分配」→ AssetAllocation（有明确的新增资金要做初始配置）
  · 「我有一笔30万的理财到期了，重新配置怎么分」→ AssetAllocation（有明确的资金要重新分配）
  · 「我想把组合调整到更稳健的结构，固收应该加多少」→ AssetAllocation（有明确配置目标，问的是"加多少"的规划问题）
  · 「我总是在股票涨了之后才后悔没多买，跌了又舍不得止损，怎么破」→ Education（行为偏差类，核心是投资心理和纪律方法论，不是在问某个具体标的该怎么操作）
  · 「分散投资和集中持仓我一直没想清楚，对我来说哪种更适合」→ Education（方法论类，"对我来说"是希望个性化解释，不是在评估组合结构问题，不选 PortfolioReview）
  · 「再平衡是什么，什么情况下该做」→ Education（概念+方法论，不涉及具体操作决策）

- 操作意图优先原则：只要句子中同时出现【可识别的标的名称】+【明确的操作动词】（加仓/减仓/买入/卖出/持有/止损/清仓/落袋/建仓/配置），无论句式是陈述句、疑问句还是假设句（含"如果""假设""要是""考虑"等前缀），一律判断为 PositionDecision，confidence ≥ 0.85

- 假设语气不降低置信度：
  · 「如果新增资金，加仓XX合适吗」→ PositionDecision，confidence ≥ 0.85
  · 「假设我要买XX，现在时机对吗」→ PositionDecision，confidence ≥ 0.85
  · 「考虑减仓XX，你怎么看」→ PositionDecision，confidence ≥ 0.85
  以上均为 PositionDecision，不得降级为 Education 或其他意图

- PortfolioReview vs AssetAllocation 的判断标准：
  · 用户在问"我的组合怎么样/合不合理/有没有问题" → PortfolioReview
    （核心是诊断现有持仓结构，输出是现状判断+风险识别+调整方向）
  · 用户在问"我的钱怎么配/应该怎么分/加多少" → AssetAllocation
    （核心是规划资金配置，输出是具体比例+金额+执行方案）
  · 关键词参考：出现"有X万""到期了""怎么分""加多少""配多少"等资金规划词 → AssetAllocation
  · 关键词参考：出现"合理吗""有问题吗""健不健康""失衡吗"等评估诊断词 → PortfolioReview

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

def recognize(
    user_input: str,
    conversation_history: list[dict] | None = None,
    position_names: list[str] | None = None,
) -> tuple[IntentPayload, Optional[str]]:
    """
    识别用户意图，返回 (IntentPayload, clarification_question)。

    clarification_question 仅在以下情况非空：
    - confidence < 0.5 → 不执行，仅返回澄清问题
    - 0.5~0.74 → 执行，但 clarification 为 TODO 占位（Phase 2 实现）

    Args:
        user_input: 当前用户输入
        conversation_history: 最近几轮对话记录（可选），用于多轮意图理解
        position_names: 用户当前持仓标的名称列表（可选），辅助意图判断

    Returns:
        (payload, clarification_question)
        若 clarification_question 非空且 confidence < 0.5，调用方应中断执行。
    """
    if not user_input or not user_input.strip():
        return _fallback_payload(user_input), "请描述您的投资问题，例如：'理想汽车要不要卖？'"

    # 构造 system prompt（如有历史或持仓列表，动态拼入上下文）
    sys_prompt = _SYSTEM_PROMPT

    if position_names:
        sys_prompt += (
            "\n\n## 用户当前持仓标的（供意图判断参考）\n"
            + "\n".join(f"- {n}" for n in position_names)
            + "\n\n判断规则补充："
            "\n- 如果用户提到的标的名称在上述持仓列表中，优先判断为基于持仓的操作意图"
            "\n- 如果用户提到的标的不在持仓列表中，可能是询问新建仓，仍可判断为 PositionDecision"
        )

    if conversation_history:
        history_lines = []
        for turn in conversation_history:
            role_tag = "[User]" if turn["role"] == "user" else "[Assistant]"
            text = turn["content"]
            if turn["role"] == "assistant" and len(text) > 100:
                text = text[:100] + "…"
            history_lines.append(f"{role_tag}: {text}")
        sys_prompt += (
            "\n\n## 对话历史（最近几轮，供意图判断参考）\n"
            + "\n".join(history_lines)
            + "\n\n请基于对话历史理解当前用户输入的上下文。"
            "如果用户的追问明显是延续上一轮的标的或话题，"
            "应继承上一轮的 asset 和 intent，confidence 应 ≥ 0.8。"
        )

    last_error: Optional[Exception] = None

    for attempt in range(3):  # 最多3次（初始1次 + 重试2次，PRD §3.1）
        try:
            client = get_client()
            response = client.chat.completions.create(
                model=MODEL_MAIN,
                max_tokens=512,
                timeout=10,
                messages=[
                    {"role": "system", "content": sys_prompt},
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
