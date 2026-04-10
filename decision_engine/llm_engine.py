"""
LLM 推理模块 (LLM Engine)

职责：将结构化信号 + 规则 + 投研观点送入 LLM，获取最终投资建议。

使用模型：gpt-4.1（由 PRD 指定）

输出格式（强约束）：
    {
        "decision": "BUY / HOLD / TAKE_PROFIT / REDUCE / SELL / STOP_LOSS",
        "reasoning": ["..."],
        "risk": ["..."],
        "strategy": ["..."]
    }

UI 映射：
    BUY         → 加仓
    HOLD        → 观望
    TAKE_PROFIT → 部分止盈
    REDUCE      → 逐步减仓
    SELL        → 减仓/清仓
    STOP_LOSS   → 止损离场

异常处理：
    - API 调用失败 → 返回默认 HOLD 结果 + 提示
    - JSON 解析失败 → 重试提取，仍失败则降级
    - 超时 → 返回"系统繁忙，请稍后再试"
"""

from __future__ import annotations

import json
import os
import re
import traceback
from dataclasses import dataclass, field
from typing import Optional

import openai

from .data_loader import LoadedData
from .decision_context import build_decision_context, format_context_prompt
from .types import IntentResult
from .rule_engine import RuleResult
from .signal_engine import SignalResult


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class GenericLLMResult:
    """
    非 PositionDecision 意图的 LLM 结果。
    适用于：PortfolioReview / AssetAllocation / PerformanceAnalysis
    """
    intent_type: str                              # portfolio_review / asset_allocation / performance_analysis
    chat_answer: str                              # 左侧对话框展示文本
    raw_payload: dict = field(default_factory=dict)  # 完整 JSON 解析结果，供右侧面板使用
    raw_output: str = ""
    error: Optional[str] = None

    @property
    def is_fallback(self) -> bool:
        return self.error is not None


@dataclass
class LLMResult:
    """LLM 推理结果"""
    decision: str              # BUY / HOLD / SELL
    reasoning: list[str]       # 推理依据列表
    risk: list[str]            # 风险提示列表
    strategy: list[str]        # 操作策略建议列表
    chat_answer: str = ""      # 面向用户的自然语言对话回答（左侧面板直接展示）
    raw_output: str = ""       # LLM 原始输出（调试用）
    error: Optional[str] = None  # 异常时的错误描述
    # Phase 1: 结构化 DecisionResult（parse 成功时填充，失败时为 None）
    structured_result: Optional[dict] = None
    # BUG-04 修复：记录决策是否经过自动修正
    decision_corrected: bool = False     # True 表示原始输出非标准，已被自动修正
    original_decision: Optional[str] = None  # 修正前的原始决策值

    @property
    def decision_cn(self) -> str:
        """决策结论的中文映射。"""
        return {
            "BUY":         "加仓",
            "HOLD":        "观望",
            "TAKE_PROFIT": "部分止盈",
            "REDUCE":      "逐步减仓",
            "SELL":        "减仓/清仓",
            "STOP_LOSS":   "止损离场",
        }.get(self.decision, "观望")

    @property
    def decision_emoji(self) -> str:
        return {
            "BUY":         "📈",
            "HOLD":        "🔍",
            "TAKE_PROFIT": "💰",
            "REDUCE":      "📉",
            "SELL":        "🚨",
            "STOP_LOSS":   "🛑",
        }.get(self.decision, "🔍")

    @property
    def is_fallback(self) -> bool:
        """是否为降级结果（API 失败时）。"""
        return self.error is not None


# ── 基础 Prompt（所有意图共用）───────────────────────────────────────────────

_BASE_PROMPT = """你是 WealthPilot 的私人投资顾问，帮助用户基于真实持仓数据做出更理性的投资决策。

通用规范（所有意图均适用）：
1. 不得使用绝对性表达（如"必须买入"、"一定会涨"）
2. 建议必须有依据，不得凭空推断
3. 涉及具体操作时，必须说明风险
4. 输出语言为中文
5. 分析只能基于系统提供的数据，数据中未提供的内容不得推测或补全
6. 本系统输出仅供参考，不构成投资建议
7. markdown 加粗规范：只对关键数字和结论词加粗（如 **34.9%**、**观望**），禁止对完整句子加粗；加粗标记前后须紧邻空格或标点，禁止直接贴合中文字符（错误：浮亏**-31.4%**，正确：浮亏 **-31.4%** ，）"""


# ── 意图专属 Prompt ───────────────────────────────────────────────────────────
# 调用时拼接：_BASE_PROMPT + "\n\n" + _XXXX_PROMPT
# ─────────────────────────────────────────────────────────────────────────────

_POSITION_DECISION_PROMPT = """当前任务：单标的决策（PositionDecision）
判断用户对某一具体标的的操作是否合适（加仓/减仓/买入/卖出/持有），输出结构化建议。

输出格式（严格 JSON，第一个字符必须是 {，不含任何其他文字）：
{
  "decisionType": "buy_init | buy_more | hold | trim | exit | wait | need_info",
  "coreSuggestion": "一句话核心判断（≤40字）",
  "rationale": ["依据1", "依据2", "依据3（1-3条，每条不超过60字）"],
  "riskPoints": ["风险点1", "风险点2（1-2条，每条不超过60字）"],
  "recommendedAction": {
    "action": "与 decisionType 相同的值",
    "detail": "具体操作说明，包含仓位方向或节奏，不编造系统数据之外的具体数字"
  },
  "confidence": 0.0到1.0之间的小数,
  "confidenceReason": "置信度原因 + 建议适用前提 + 主要不确定因素",
  "infoNeeded": ["若 confidence < 0.5 必须填写，否则为空数组"],
  "evidenceSources": ["从以下枚举中选择：profile | position | discipline | research | recent_records | news | user_message"],
  "chat_answer": "见下方写作要求"
}

decisionType 从以下6个选项中选一个，选最精准的那个：
- buy_init：基本面向好，尚未持仓，适合新建仓
- buy_more：已持仓且仓位有空间，适合加仓
- hold：信号中性，当前仓位合理，建议维持观望
- trim：风险上升或仓位过重，建议分批降低仓位
- exit：基本面恶化或超出纪律上限，建议大幅减仓乃至清仓
- wait：信息不足或时机未到，建议等待明确信号

chat_answer 输出格式：

语气：用"您"直接对用户说，像私人投顾在当面解释，不是AI在出具报告。禁止用"综合来看""根据系统""综合分析"等套话开场。

{chat_format_block}

数据引用规则（严格遵守）：

关于纪律数据：
- 纪律校验结果中的"上限"是风险控制的硬性边界，不是建议仓位
- 如果当前仓位未超过纪律上限，不得说"超过上限"
- 目标仓位（如"建议降至15%"）是AI建议值，必须说明推导依据（如"基于分散化原则"），禁止表述为"您设定的上限"
- 引用纪律数据时，格式为："您的单标的纪律上限是X%，当前仓位Y%，[已超出/距上限还有Z个百分点]"

关于基本面和投研信息：
- research字段中有具体数字的，必须直接引用原始数字（如"净利润同比下降94%"），禁止替换为"大幅下滑"等模糊表述
- [用户资料]标注的内容优先引用，[联网参考]标注的内容作为补充
- 如果research字段为空或无有效内容，跳过基本面引用，不编造数字
- 分析师评级如果存在（如"大和重申买入"），在核心依据中一条带出

关于引用链接（强制执行，不得省略）：
- 有[ref:url]标注的联网参考，引用时该句末尾必须附 [[来源]](url)，不得省略
  例如："净利润同比下降85.8% [[来源]](https://wallstreetcn.com/...)"
- 无[ref:url]标注的联网参考，引用时该句末尾附"（据公开信息）"文字，不附链接
- 不得对没有URL的内容伪造链接或省略来源标注
- 每条联网参考数据最多附一个链接
- [用户资料]标注的内容不附链接
- 日期标注（如[2026-03]）不要出现在chat_answer正文中"""

# ── chat_answer 格式模板（按对话轮次动态选择）────────────────────────────────

_CHAT_FORMAT_FIRST_TURN = """使用以下四个固定标题段落输出，每个标题单独一行，用 ### 标注：

### 结论
1-2句话，先给明确判断（建议减仓/持有/加仓），再用一句话说最关键的理由。
开头直接进入正题，禁止套话。示例："仓位已达28.2%，明显偏重，建议适度减仓。"

### 核心依据
3-5条要点，每条一行，用"-"开头。
必须包含：
- 持仓现状（仓位比例、浮盈亏、距纪律上限空间）
- 基本面关键数字（直接引用research字段中的具体数据，禁止模糊表述）
- 分析师观点（如有评级/目标价，一句话带出）

### 主要风险
2-3条要点，每条一行，用"-"开头。
聚焦最实质的风险，不泛泛而谈。

### 操作建议
2-3条可执行的具体建议，每条一行，用"-"开头。
必须包含操作节奏（分批/一次性）和触发条件（财报/价格区间/事件节点）。
如果用户不想减仓，给出替代路径（如用新增资金做再平衡）。
操作建议只基于系统已有数据，不编造具体价格。"""

_CHAT_FORMAT_FOLLOWUP = """不使用任何标题，直接用对话语气回答用户的新问题。
- 禁止重复上一轮已说过的结论、数据和建议，用户已经知道了
- 第一句直接回应用户的新问题，不要用"正如上面提到的"或重述上轮结论开头
- 如果用户问的是系统没有的数据（如实时价格、具体买卖点），诚实说明数据局限，然后给出基于现有数据能给的最有价值的替代建议（如操作节奏、分批策略、触发条件）
- 长度以能回答清楚新问题为准，不需要面面俱到"""


_PORTFOLIO_REVIEW_PROMPT = """当前任务：组合评估（PortfolioReview）
评估用户整体投资组合的结构健康度，包括集中度、风险敞口、资产配比、是否需要再平衡。

重要：资产占比数据必须直接使用系统传入的 asset_breakdown 字段中的数字，
禁止自行计算或估算各类资产占比。
系统已提供精确的五大类占比数据，直接引用即可，格式如：
"权益类资产占 64.7%"（引用 asset_breakdown.权益 的值）。

输出格式（严格 JSON，第一个字符必须是 {，不含任何其他文字）：
{
  "risk_level": "高 或 中 或 低",
  "key_findings": ["整体组合的核心发现，2-4条，每条不超过60字"],
  "concentration_issues": ["集中度问题，有则列出，无则空数组，每条不超过60字"],
  "rebalance_needed": true 或 false,
  "rebalance_suggestions": ["调仓方向建议，有则列出（上限3条），无则空数组，每条不超过60字"],
  "conclusion_type": "healthy | rebalance_needed | high_risk | low_defense",
  "chat_answer": "见下方写作要求"
}

conclusion_type 从以下4个选项中选一个：
- healthy：结构健康，维持现状
- rebalance_needed：局部偏重，建议再平衡
- high_risk：风险偏高，建议降仓
- low_defense：防御不足，建议补充固收或现金

chat_answer 输出格式：

语气：用"您"直接对用户说，像私人投顾在当面解释。禁止用"综合来看""根据系统"等套话开场。

使用以下五个固定标题段落输出，每个标题单独一行，用 ### 标注：

### 组合现状
整体健康度判断 + 最突出的1-2个问题点（2-3句）

### 结构分析
大类资产占比情况、集中度风险、主要持仓表现分化（3-4条要点，用"-"开头）
每条必须引用 asset_breakdown 中的精确数字，禁止模糊表述。
如果某类资产占比为0或无持仓，跳过该类，不提。
持仓前三（top3_by_weight）必须至少提到仓位最重的一只，说明其占比和对组合风险的影响。

### 市场背景
引用 research 字段中与持仓最相关的联网参考内容，2-3条，用"-"开头。
每条引用必须附 [[来源]](url)。
聚焦与持仓行业或大类资产直接相关的内容（如持仓有科技股则引用科技行业展望，有固收则引用债券市场展望，有黄金则引用黄金走势）。
如果 research 字段为空或无相关内容，跳过此段，不输出"市场背景"标题。

### 主要风险
结合组合结构和市场背景，推导出2-3条最实质的风险点，用"-"开头。
风险点应该是内部结构问题与外部市场信号叠加后的判断，不是单纯列结构问题。

### 调整建议
2-3条可执行的方向，说明优先级，用"-"开头。
建议必须基于前四段的分析，不得凭空给出。
如不需要调整，说明原因。

关于引用链接（强制执行）：凡引用带[ref:url]标注的联网内容，必须在句末附 [[来源]](url)。无[ref:url]标注的联网参考，末尾附"（据公开信息）"。[用户资料]标注的不附链接。"""


_ASSET_ALLOCATION_PROMPT = """当前任务：资产配置（AssetAllocation）
根据用户的资金规模、风险偏好、当前持仓结构，给出具体的资产配置方案。

首先判断当前场景属于哪种主线：
- 主线A（新增资金配置）：用户有一笔新钱要投入或重新配置，问"怎么分"
- 主线B（再平衡调整）：用户想调整现有组合结构，问"加多少/减多少"

输出格式（严格 JSON，第一个字符必须是 {，不含任何其他文字）：
{
  "allocation_type": "new_cash | rebalance",
  "capital_amount": "用户提到的资金金额，如'30万'，未提及则为null",
  "current_gaps": ["当前配置与合理目标之间的偏差，2-3条，每条不超过60字"],
  "allocation_plan": [
    {
      "asset_class": "资产类别（权益/固收/货币/另类/衍生）",
      "current_pct": "当前占比，来自asset_breakdown",
      "target_range": "目标区间，来自target_ranges，如'20%~60%'，无则填null",
      "deviation": "偏离度，来自deviation_from_target，如'-14.9%'，无则填null",
      "direction": "增加 | 维持 | 减少",
      "suggested_pct": "基于target_ranges推算的建议目标占比",
      "rationale": "理由，不超过60字"
    }
  ],
  "priority_order": ["执行优先级，1-3条，说明先做什么后做什么"],
  "risks": ["主要风险点，1-2条，每条不超过60字"],
  "chat_answer": "见下方写作要求"
}

重要数据引用规则：
- asset_breakdown字段：当前各类资产实际占比（精确值）
- target_ranges字段：各类资产的目标区间（floor~ceiling），这是用户设定的配置目标
- deviation_from_target字段：当前占比与目标中值的偏离度（正值=超配，负值=欠配）

如果target_ranges和deviation_from_target有数据，必须基于这些精确数据分析：
- 当前缺口 = 偏离度为负的资产类别，说明"当前X%，目标区间Y%~Z%，欠配约N%"
- 超配风险 = 偏离度为正的资产类别，说明"当前X%，已超目标中值Z%"
- 禁止凭空估算目标比例，目标区间以target_ranges为准

如果target_ranges为空（用户未设置配置目标），基于通用稳健配置原则给建议，
并提示用户可以在"资产配置"模块设置个人目标区间。

- computed_plan字段：如果存在，是系统精确计算的配置方案，包含每类资产的建议金额和比例
  - 当computed_plan有数据时，allocation_plan必须以computed_plan.plan_items为基准，
    直接引用其中的suggested_amount和suggested_ratio，不得自行估算
  - chat_answer中的分配方案段必须使用computed_plan的精确数字，
    格式：- {asset_class}：建议配置{suggested_ratio}，约{suggested_amount/10000}万元
  - discipline_passed=false时，说明该方案已通过纪律校验自动修正，直接使用修正后的数字
- capital_amount字段：用户本次配置的资金金额（元），展示时转换为万元
  - 如capital_amount为null，说明用户未明确金额，只给比例方向，不编造金额

chat_answer 输出格式：

语气：用"您"直接对用户说，像私人投顾在当面解释。禁止用"综合来看""根据系统"等套话开场。

根据主线类型使用不同的标题结构：

【主线A：新增资金配置（allocation_type = new_cash）】

### 配置原则
基于用户风险偏好和当前持仓缺口，说明本次配置的首要逻辑（2-3句）

### 当前缺口
引用asset_breakdown精确数据，说明哪些资产类别配置不足或过重（2-3条，用"-"开头）
每条必须有具体数字（如"固收类占25.1%，低于建议的30-40%区间"）

### 分配方案
针对用户提到的资金金额，给出每类资产的建议配置比例和金额（3-4条，用"-"开头）
格式：- {资产类别}：建议配置{X%}，约{金额}万，{一句话理由}
如用户未提及金额，只给比例方向，不编造金额

### 执行建议
分批执行的节奏和优先顺序（2-3条，用"-"开头）
说明先配哪类、后配哪类，以及时间节奏参考

【主线B：再平衡调整（allocation_type = rebalance）】

### 调整目标
用户想达到的结构目标是什么，一句话说清楚（1-2句）

### 当前偏差
引用asset_breakdown精确数据，说明哪里偏了、偏了多少（2-3条，用"-"开头）
每条必须有具体数字（如"固收占25.1%，目标区间30-40%，需增加约5-15%"）

### 调整方案
具体的增减方向和幅度（3-4条，用"-"开头）
格式：- {资产类别}：{增加/减少}{X%}，约{金额}，{一句话理由}

### 执行建议
调整节奏和注意事项（2-3条，用"-"开头）
如有触发条件（市场时机、财报节点等），说明

多轮对话规则：有对话历史时不使用标题结构，直接用对话语气回答追问，不重复上轮已说过的内容。

关于引用链接（强制执行）：凡引用带[ref:url]标注的联网内容，必须在句末附 [[来源]](url)。无[ref:url]标注的联网参考，末尾附"（据公开信息）"。[用户资料]标注的不附链接。"""


_PERFORMANCE_ANALYSIS_PROMPT = """当前任务：收益分析（PerformanceAnalysis）
分析用户投资组合的盈亏现状，找出收益来源和亏损来源，给出结构性判断。

输出格式（严格 JSON，第一个字符必须是 {，不含任何其他文字）：
{
  "overall_pnl": "整体盈亏状态，一句话，如'整体盈利，但结构性问题明显'",
  "profit_drivers": [
    {
      "name": "标的名称",
      "pnl_amount": "盈亏金额（元）",
      "pnl_pct": "盈亏百分比",
      "weight": "仓位占比",
      "note": "贡献逻辑，不超过30字"
    }
  ],
  "loss_drivers": [
    {
      "name": "标的名称",
      "pnl_amount": "盈亏金额（元）",
      "pnl_pct": "盈亏百分比",
      "weight": "仓位占比",
      "note": "拖累逻辑，不超过30字"
    }
  ],
  "structural_issue": "结构性问题一句话概括，如'集中度过高导致单标的拖累放大'",
  "diagnosis_type": "concentration | asset_mix | stock_selection | healthy | low_defense",
  "chat_answer": "见下方写作要求"
}

profit_drivers 和 loss_drivers：
- 按盈亏绝对金额排序（profit_drivers降序，loss_drivers升序）
- 各取前3条，不足3条则全部列出
- 只列出实际对组合有显著影响的标的（盈亏绝对金额较大或仓位较重）
- 必须基于 performance 字段中的 profit_top3 和 loss_top3 填写，不得自行估算

diagnosis_type 从以下5个选项中选一个：
- concentration：集中度过高，单标的拖累放大了整体波动
- asset_mix：资产配比问题，某类资产占比不合理导致整体表现偏弱
- stock_selection：个股选择问题，选股表现分化明显
- healthy：收益结构合理，整体表现健康
- low_defense：防御资产不足，组合在市场波动中缺乏缓冲

数据引用规则：
- performance字段包含精确的盈亏数据，profit_drivers和loss_drivers必须基于
  performance.profit_top3和performance.loss_top3填写，不得自行估算
- total_pnl_display是整体盈亏的显示值，收益概览段必须引用这个数字
- 所有金额引用以元为单位，超过1万元时转换为万元显示（保留1位小数）

chat_answer 输出格式：

语气：用"您"直接对用户说，像私人投顾在当面解释。禁止用"综合来看""根据系统"等套话开场。

使用以下四个固定标题段落输出，每个标题单独一行，用 ### 标注：

### 收益概览
整体盈亏状态一句话定性 + 最关键的1个结构性问题点（2-3句）
必须引用整体盈亏金额或收益率数字。

### 盈利来源
列出主要正贡献标的（2-3条，用"-"开头）
格式：- {标的名}：盈利{金额}元（+{%}），仓位{%}，{一句话说贡献逻辑}
按盈亏绝对金额从大到小排列，说明是靠涨幅、靠仓位重、还是两者兼有。

### 亏损来源
列出主要负贡献标的（2-3条，用"-"开头）
格式：- {标的名}：亏损{金额}元（{%}），仓位{%}，{一句话说拖累逻辑}
按亏损绝对金额从大到小排列。
如果当前无亏损标的，说明"当前所有持仓均处于盈利状态"，跳过此段。

### 结构性判断
说明跑输/跑赢的根本原因是什么（集中度、资产配比、个股选择中的哪个）（2-3句）
最后一句给出后续方向性建议，指向具体行动（如"可在单标的决策模块评估理想汽车减仓时机"或"可在资产配置模块调整固收比例"），不在此给出具体操作指令。

多轮对话规则：有对话历史时不使用标题结构，直接用对话语气回答追问，不重复上轮已说过的内容。

关于引用链接：收益分析不引用联网数据，不需要附来源链接。"""


_NOT_IN_PORTFOLIO_PROMPT = """当前情况：用户询问了某只股票的投资操作（卖出/止损/持有判断），但系统在他的持仓记录中未找到该标的。

你的任务：生成一段自然、有帮助的引导回复。

回复结构（三段，不输出段落标题）：

第一段（1-2句）：确认你理解了用户的问题，说清楚他问的是哪只股票以及他描述的情况（亏损/涨跌等）。

第二段（2-3句）：说明在他的持仓记录里没有找到这只股票的数据，给出两条路径——
路径一：如果已经持有但尚未录入，引导去「投资账户总览」页面添加持仓，录入后系统就能基于实际成本和仓位给出准确分析。
路径二：如果想做通用参考分析，可以直接告诉我持仓数量和成本价，我可以帮你推演。

第三段（1句）：简短收尾，引导用户选择一条路径继续。

要求：
- 语气直接友好，用"您"
- 不使用套话（"很遗憾"、"根据系统数据"、"综合来看"）
- 不在没有持仓数据的情况下给出具体买卖结论
- 字数控制在 150-250 字"""


_GENERAL_CHAT_PROMPT = """当前任务：通用问答（GeneralChat / Education）
回答用户的投资知识问题或日常对话，不进入结构化决策流程，不输出 JSON。

规则：
- 回答自然、友好、有帮助，适当引用知识背景或市场常识举例说明
- 如果问题涉及用户的具体持仓操作（加仓/减仓/买入/卖出），引导用户直接描述操作意图，系统会自动进入决策流程
- 禁止输出结构化 JSON 或模板化结论
- 不提供针对具体持仓的买卖建议（当前上下文中没有持仓数据）
- 如果是投教类问题，结合实际例子解释，帮助用户建立认知
- 回答长度以问题复杂度为准，能简则简，不凑字数
- 个性化要求（重要）：回答必须结合用户当前持仓数据做个性化举例，不得给纯教科书式的通用答案。方式：先说通用原则或方法论，然后用"以您当前持仓为例"引出具体数据，让用户感受到答案是针对他自己情况说的。例如：解释再平衡时，结合用户当前权益占64.7%、目标区间等实际数据举例；解释分散投资时，结合用户理想汽车占28.2%的集中持仓现状举例。
- 如果问题涉及可以具体操作的场景（如"止损""减仓""加仓"），结尾用一句话引导用户进入对应的决策模块：例如"如果您想针对某个具体标的做决策，可以直接告诉我标的名称，我来帮您分析。"
- 禁止用###标题把回答切割成模块化结构，保持对话自然流"""


# ── 客户端（懒加载）──────────────────────────────────────────────────────────

_client: Optional[openai.OpenAI] = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("未找到 OPENAI_API_KEY 环境变量。")
        _client = openai.OpenAI(api_key=api_key)
    return _client


# ── DecisionResult 结构化解析（Phase 1）───────────────────────────────────────

# 新 decisionType → 旧 decision 枚举映射
_NEW_TO_OLD_DECISION = {
    'buy_init':  'BUY',
    'buy_more':  'BUY',
    'hold':      'HOLD',
    'trim':      'REDUCE',
    'exit':      'SELL',
    'wait':      'HOLD',
    'need_info': 'HOLD',
}


_VALID_DECISION_TYPES = ['buy_init', 'buy_more', 'hold', 'trim', 'exit', 'wait', 'need_info']
_VALID_EVIDENCE_SOURCES = ['profile', 'position', 'discipline', 'research', 'recent_records', 'news', 'user_message']


def validate_decision_result(result: dict) -> bool:
    """
    校验 LLM 返回的 DecisionResult JSON 结构是否合法。
    返回 True 表示校验通过，False 表示不合法。
    注意：evidenceSources 中的非法值会被过滤而不是直接拒绝整个结果。
    """
    try:
        if result.get('decisionType') not in _VALID_DECISION_TYPES:
            return False
        if not isinstance(result.get('rationale'), list) or not (1 <= len(result['rationale']) <= 5):
            return False
        if not isinstance(result.get('riskPoints'), list) or not (1 <= len(result['riskPoints']) <= 3):
            return False
        if not isinstance(result.get('confidence'), (int, float)) or not (0 <= result['confidence'] <= 1):
            return False
        if result['confidence'] < 0.5:
            if not (isinstance(result.get('infoNeeded'), list) and len(result['infoNeeded']) > 0):
                return False
        if not isinstance(result.get('evidenceSources'), list):
            return False

        # evidenceSources: 过滤非法值（宽容处理），过滤后至少保留 1 个
        valid_sources = [s for s in result['evidenceSources'] if s in _VALID_EVIDENCE_SOURCES]
        if len(valid_sources) == 0 and len(result['evidenceSources']) > 0:
            # 全部非法但有值 → 仍通过，后续清洗
            pass
        # 就地修正为合法值
        result['evidenceSources'] = valid_sources if valid_sources else result['evidenceSources']

        return True
    except Exception:
        return False


def parse_decision_result(raw_response: str) -> dict | None:
    """
    尝试解析 LLM 返回的 DecisionResult JSON。
    返回 dict 表示解析成功，返回 None 表示解析失败（fallback 到纯文本）。
    多层容错：直接解析 → 控制字符清洗 → chat_answer 专项修复 → 占位符兜底。
    """
    # 清理可能的 markdown 代码块标记
    text = raw_response.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    # 尝试 1: 直接解析
    try:
        result = json.loads(text)
        if validate_decision_result(result):
            return result
    except (json.JSONDecodeError, KeyError):
        pass

    # 尝试 2: 控制字符清洗后重试
    try:
        sanitized = _sanitize_json_strings(text)
        result = json.loads(sanitized)
        if validate_decision_result(result):
            print("[llm_engine] DecisionResult 经控制字符清洗后解析成功", flush=True)
            return result
    except (json.JSONDecodeError, KeyError):
        pass

    # 尝试 3: 全文换行符转义后重试
    try:
        cleaned = text.replace('\r\n', '\\n').replace('\r', '\\r')
        # 只替换 JSON 字符串值内的换行（用 _sanitize_json_strings）
        result = json.loads(_sanitize_json_strings(cleaned))
        if validate_decision_result(result):
            print("[llm_engine] DecisionResult 经全文换行清洗后解析成功", flush=True)
            return result
    except (json.JSONDecodeError, KeyError):
        pass

    # 尝试 4: chat_answer 占位符法 — 先剥离 chat_answer，解析其余字段
    try:
        import re as _re
        # 用非贪婪匹配定位 chat_answer 字段（可能跨多行）
        placeholder = '"chat_answer":"__PLACEHOLDER__"'
        # 匹配 "chat_answer": "..." 直到找到未转义引号+逗号或大括号
        text_no_chat = _re.sub(
            r'"chat_answer"\s*:\s*"(?:[^"\\]|\\.)*"',
            placeholder,
            _sanitize_json_strings(text),
        )
        result = json.loads(text_no_chat)
        # 从原始文本中单独提取 chat_answer
        chat_match = _re.search(
            r'"chat_answer"\s*:\s*"((?:[^"\\]|\\.)*)"',
            _sanitize_json_strings(text),
        )
        if chat_match:
            result['chat_answer'] = chat_match.group(1).replace('\\n', '\n').replace('\\r', '')
        else:
            result['chat_answer'] = ''
        if validate_decision_result(result):
            print("[llm_engine] DecisionResult 经占位符法解析成功", flush=True)
            return result
    except Exception:
        pass

    # 尝试 5: 括号计数法提取 JSON 边界后重试
    try:
        candidate = _bracket_extract(text)
        if candidate:
            result = json.loads(_sanitize_json_strings(candidate))
            if validate_decision_result(result):
                print("[llm_engine] DecisionResult 经括号计数法解析成功", flush=True)
                return result
    except Exception:
        pass

    return None


def _structured_to_llm_result(structured: dict, raw: str) -> LLMResult:
    """
    将新 DecisionResult 结构映射为旧 LLMResult，保证后端管道兼容。
    structured_result 字段存储完整的新格式 dict。
    """
    decision_type = structured.get('decisionType', 'hold')
    old_decision = _NEW_TO_OLD_DECISION.get(decision_type, 'HOLD')

    chat_answer = str(structured.get('chat_answer', '') or '')

    # 去掉 chat_answer 后的纯结构化数据，供前端卡片化使用
    decision_result_clean = {k: v for k, v in structured.items() if k != 'chat_answer'}

    return LLMResult(
        decision=old_decision,
        reasoning=structured.get('rationale', []),
        risk=structured.get('riskPoints', []),
        strategy=[structured.get('recommendedAction', {}).get('detail', '')],
        chat_answer=chat_answer,
        raw_output=raw,
        structured_result=decision_result_clean,
    )


# ── 核心函数 ───────────────────────────────────────────────────────────────────

def reason(
    user_query: str,
    data: LoadedData,
    intent: IntentResult,
    rule_result: RuleResult,
    signals: SignalResult,
    conversation_history: list[dict] | None = None,
) -> LLMResult:
    """
    调用 LLM 进行投资推理。

    Args:
        user_query: 用户原始输入
        data: 加载的数据（含持仓、规则、投研）
        intent: 意图解析结果
        rule_result: 规则校验结果
        signals: 信号层结果
        conversation_history: 最近几轮对话记录（可选），用于多轮推理上下文

    Returns:
        LLMResult
    """
    # 构建输入 payload
    payload = _build_payload(user_query, data, intent, rule_result, signals)

    # 根据对话轮次选择 chat_answer 格式
    is_followup = bool(conversation_history)
    chat_format = _CHAT_FORMAT_FOLLOWUP if is_followup else _CHAT_FORMAT_FIRST_TURN
    position_prompt = _POSITION_DECISION_PROMPT.replace("{chat_format_block}", chat_format)

    # Phase 2: 构建 DecisionContext 并注入 system prompt
    try:
        pid = data.raw_portfolio.id if data.raw_portfolio and hasattr(data.raw_portfolio, 'id') else 1
        decision_ctx = build_decision_context(user_query, data, portfolio_id=pid)
        context_prompt = format_context_prompt(decision_ctx)
        system_prompt = _BASE_PROMPT + "\n\n" + context_prompt + "\n\n" + position_prompt
        print(f"[llm_engine] DecisionContext 注入成功，prompt 长度={len(system_prompt)} 字符, followup={is_followup}", flush=True)
    except Exception as e:
        print(f"[llm_engine] DecisionContext 构建失败，降级到无上下文: {e}", flush=True)
        system_prompt = _BASE_PROMPT + "\n\n" + position_prompt

    try:
        client = _get_client()

        # 构造 messages 列表（含多轮历史）
        messages = [{"role": "system", "content": system_prompt}]
        if conversation_history:
            for turn in conversation_history:
                if turn["role"] == "user":
                    messages.append({"role": "user", "content": turn["content"]})
                elif turn["role"] == "assistant":
                    # assistant 内容截取前200字，避免 token 超限
                    text = turn["content"]
                    if len(text) > 200:
                        text = text[:200] + "…"
                    messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)})

        response = client.chat.completions.create(
            model="gpt-4.1",
            max_tokens=2048,
            timeout=30,
            messages=messages,
        )
        raw = response.choices[0].message.content.strip()

        # Phase 1: 优先尝试新 DecisionResult 格式解析
        structured = parse_decision_result(raw)
        if structured is not None:
            print(f"[llm_engine] ✅ DecisionResult 结构化解析成功: decisionType={structured.get('decisionType')}, confidence={structured.get('confidence')}")
            return _structured_to_llm_result(structured, raw)

        # Fallback: 旧格式解析
        print(f"[llm_engine] ⚠️ DecisionResult 结构化解析失败，fallback 到旧格式")
        try:
            parsed = _extract_json(raw)
            return _build_result(parsed, raw)
        except (json.JSONDecodeError, ValueError):
            # 旧格式也失败 → 尝试从 raw 中直接提取 chat_answer 作为纯文本回复
            print(f"[llm_engine] ⚠️ 旧格式也解析失败，提取 chat_answer 作为纯文本", flush=True)
            chat_match = re.search(r'"chat_answer"\s*:\s*"((?:[^"\\]|\\.)*)"', _sanitize_json_strings(raw))
            chat_text = chat_match.group(1).replace('\\n', '\n') if chat_match else raw
            # 尝试从 raw 中提取 decisionType
            dt_match = re.search(r'"decisionType"\s*:\s*"(\w+)"', raw)
            decision_type = dt_match.group(1) if dt_match else "hold"
            old_decision = _NEW_TO_OLD_DECISION.get(decision_type, "HOLD")
            return LLMResult(
                decision=old_decision,
                reasoning=[],
                risk=[],
                strategy=[],
                chat_answer=chat_text,
                raw_output=raw,
            )

    except EnvironmentError as e:
        return _fallback_result(str(e), "HOLD")

    except openai.APITimeoutError:
        return _fallback_result("系统繁忙，请稍后再试。", "HOLD")

    except openai.APIError as e:
        return _fallback_result(f"API 调用失败：{e}", "HOLD")

    except Exception as e:
        return _fallback_result(f"未知错误：{type(e).__name__}：{e}", "HOLD")


# ── 辅助函数 ───────────────────────────────────────────────────────────────────

def _build_payload(
    user_query: str,
    data: LoadedData,
    intent: IntentResult,
    rule_result: RuleResult,
    signals: SignalResult,
) -> dict:
    """拼接送给 LLM 的结构化 payload（PRD 指定格式）。"""

    # 持仓摘要（TOP5，已聚合，每标的唯一一条）
    top_positions = sorted(data.positions, key=lambda p: p.weight, reverse=True)[:5]
    position_summary = [
        {
            "name": p.name,
            "weight": f"{p.weight:.1%}",
            "asset_class": p.asset_class,
            "platforms": p.platforms if p.platforms else [],
        }
        for p in top_positions
    ]

    # 目标持仓信息（聚合后，包含跨平台合并市值）
    target_info = None
    if data.target_position:
        tp = data.target_position
        target_info = {
            "name": tp.name,
            "current_weight": f"{tp.weight:.1%}",   # 聚合后占比，与规则校验完全一致
            "market_value_cny": f"¥{tp.market_value_cny:,.0f}",
            "profit_loss_rate": f"{tp.profit_loss_rate:.1%}",
            "platforms": tp.platforms if tp.platforms else [],
        }

    return {
        "user_query": user_query,
        "intent": {
            "asset": intent.asset,
            "action_type": intent.action_type,
            "time_horizon": intent.time_horizon,
            "trigger": intent.trigger,
        },
        "position_context": {
            "target_asset": target_info,
            "top_holdings": position_summary,
            "total_assets_cny": f"¥{data.total_assets:,.0f}",
        },
        "rules": {
            "max_single_position": f"{data.rules.max_single_position:.0%}",
            "min_cash_pct": f"{data.rules.min_cash_pct:.0%}",
            "rule_check": {
                "violation": rule_result.violation,
                "warning": rule_result.warning,
            },
        },
        "signals": signals.to_dict(),
        "research": data.research,
        "user_profile": {
            "risk_level": data.profile.risk_level,
            "goal": data.profile.goal,
        },
    }


def _sanitize_json_strings(text: str) -> str:
    """
    将 JSON 字符串值内的原生控制字符转义。

    LLM 有时在 chat_answer 等字段里写入真实换行符/制表符，
    这在 JSON 规范中是非法的，会导致 json.loads 失败。
    此函数只处理字符串值内部，不影响 JSON 结构字符。
    """
    result = []
    in_string = False
    escape_next = False
    _ESCAPE = {'\n': '\\n', '\r': '\\r', '\t': '\\t'}
    for ch in text:
        if escape_next:
            escape_next = False
            result.append(ch)
            continue
        if ch == '\\' and in_string:
            escape_next = True
            result.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string and ch in _ESCAPE:
            result.append(_ESCAPE[ch])
            continue
        result.append(ch)
    return ''.join(result)


def _bracket_extract(text: str) -> Optional[str]:
    """
    用括号计数法从文本中定位第一个完整 JSON 对象的字符串范围。
    返回该子串，或 None（找不到平衡的 {}）。
    支持任意嵌套深度。
    """
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _extract_json(text: str) -> dict:
    """
    从 LLM 输出中稳健提取 JSON，兼容多种输出格式。

    解析优先级：
    1. 直接解析
    2. 去掉 ```json``` 包装后解析
    3. 括号计数法定位 JSON 边界后解析
    4. 上述任一步骤失败时，对字符串内控制字符转义后重试
    """
    def _try_loads(s: str) -> Optional[dict]:
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
        # 控制字符转义后重试（处理 chat_answer 里的原生换行符等）
        try:
            return json.loads(_sanitize_json_strings(s))
        except json.JSONDecodeError:
            return None

    # Step 1: 直接解析
    result = _try_loads(text)
    if result is not None:
        return result

    # Step 2: 去掉 ```json ... ``` 包装
    block = re.search(r'```(?:json)?\s*(\{.*?})\s*```', text, re.DOTALL)
    if block:
        result = _try_loads(block.group(1))
        if result is not None:
            return result

    # Step 3: 括号计数法定位 JSON 边界
    candidate = _bracket_extract(text)
    if candidate:
        result = _try_loads(candidate)
        if result is not None:
            return result

    # Step 4: chat_answer 占位符法兜底
    try:
        src = _sanitize_json_strings(candidate or text)
        placeholder = '"chat_answer":"__PLACEHOLDER__"'
        text_no_chat = re.sub(
            r'"chat_answer"\s*:\s*"(?:[^"\\]|\\.)*"',
            placeholder,
            src,
        )
        result = json.loads(text_no_chat)
        chat_match = re.search(r'"chat_answer"\s*:\s*"((?:[^"\\]|\\.)*)"', src)
        if chat_match:
            result['chat_answer'] = chat_match.group(1).replace('\\n', '\n')
        else:
            result['chat_answer'] = ''
        return result
    except Exception:
        pass

    raise ValueError(f"无法提取 JSON，原始输出：{text[:300]}")


def _build_result(parsed: dict, raw: str) -> LLMResult:
    """从解析后的 dict 构建 LLMResult。"""
    raw_decision = str(parsed.get("decision", "HOLD")).strip()
    decision = raw_decision.upper()

    # BUG-04 修复：检测并记录非标准决策被自动修正的情况
    _VALID_DECISIONS = {"BUY", "HOLD", "TAKE_PROFIT", "REDUCE", "SELL", "STOP_LOSS"}
    decision_corrected = False
    original_decision: Optional[str] = None
    if decision not in _VALID_DECISIONS:
        decision_corrected = True
        original_decision = raw_decision
        decision = "HOLD"

    def _to_list(v) -> list[str]:
        if isinstance(v, list):
            return [str(x) for x in v]
        if isinstance(v, str):
            return [v] if v else []
        return []

    return LLMResult(
        decision=decision,
        reasoning=_to_list(parsed.get("reasoning", [])),
        risk=_to_list(parsed.get("risk", [])),
        strategy=_to_list(parsed.get("strategy", [])),
        chat_answer=str(parsed.get("chat_answer", "") or ""),
        raw_output=raw,
        decision_corrected=decision_corrected,
        original_decision=original_decision,
    )


# ── general_chat 普通对话 ───────────────────────────────────────────────────────

# _GENERAL_CHAT_PROMPT 已在上方意图专属 Prompt 区统一定义


def chat(user_query: str, context: Optional[list] = None) -> str:
    """
    普通对话模式（intent_type=general_chat），不进入决策流程，不输出结构化结论。

    Args:
        user_query: 用户当前输入
        context:    最近 1 轮对话记录（[{"role": "user", "content": ...}, {"role": "assistant", ...}]）

    Returns:
        纯文本回复
    """
    messages: list = []
    if context:
        for msg in context[-2:]:  # 最多保留最近 1 轮（2 条）
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_query})

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=512,
            timeout=20,
            messages=[{"role": "system", "content": _BASE_PROMPT + "\n\n" + _GENERAL_CHAT_PROMPT}] + messages,
        )
        return response.choices[0].message.content.strip()
    except EnvironmentError:
        return "⚙️ 未配置 API Key，无法回复。"
    except Exception as e:
        tb_lines = traceback.format_exc()
        print(f"[llm_engine.chat] 失败:\n{tb_lines}", flush=True)
        return "抱歉，系统暂时繁忙，请稍后再试。"



def _build_portfolio_payload(user_query: str, data: LoadedData) -> dict:
    """构建组合级别 LLM payload（PortfolioReview / AssetAllocation / PerformanceAnalysis 共用）"""
    top_positions = sorted(data.positions, key=lambda p: p.weight, reverse=True)[:10]
    holdings = [
        {
            "name": p.name,
            "weight": f"{p.weight:.1%}",
            "asset_class": p.asset_class,
            "profit_loss_rate": f"{p.profit_loss_rate:.1%}",
            "market_value_cny": f"¥{p.market_value_cny:,.0f}",
        }
        for p in top_positions
    ]
    # 计算五大类资产占比
    cats: dict[str, dict] = {}
    total_mv = data.total_assets or 1.0
    for p in data.positions:
        ac = getattr(p, 'asset_class', '其他') or "其他"
        if ac not in cats:
            cats[ac] = {"market_value": 0.0, "pct": 0.0, "count": 0}
        cats[ac]["market_value"] += p.market_value_cny
        cats[ac]["count"] += 1
    for c in cats.values():
        c["pct"] = round(c["market_value"] / total_mv * 100, 1)

    return {
        "user_query": user_query,
        "portfolio": {
            "total_assets_cny": f"¥{data.total_assets:,.0f}",
            "holding_count": len(data.positions),
            "holdings": holdings,
        },
        "asset_breakdown": {
            cat: f"{info['pct']}%（{info['count']}只）"
            for cat, info in cats.items()
        },
        "rules": {
            "max_single_position": f"{data.rules.max_single_position:.0%}",
            "max_equity_pct": f"{data.rules.max_equity_pct:.0%}",
            "min_cash_pct": f"{data.rules.min_cash_pct:.0%}",
        },
        "research": data.research,
        "user_profile": {
            "risk_level": data.profile.risk_level,
            "goal": data.profile.goal,
        },
        "performance": _build_performance_data(data),
    }


def _build_performance_data(data: LoadedData) -> dict:
    """收益分析专用：计算盈亏绝对金额并排序。"""
    pnl_data = []
    total_pnl = 0.0
    for p in data.positions:
        pnl = p.market_value_cny - p.cost_price
        total_pnl += pnl
        pnl_data.append({
            "name": p.name,
            "pnl_amount": round(pnl),
            "pnl_pct": f"{p.profit_loss_rate:.1%}",
            "weight": f"{p.weight:.1%}",
            "market_value_cny": round(p.market_value_cny),
        })
    profit_top3 = sorted([x for x in pnl_data if x["pnl_amount"] > 0],
                         key=lambda x: x["pnl_amount"], reverse=True)[:3]
    loss_top3 = sorted([x for x in pnl_data if x["pnl_amount"] < 0],
                       key=lambda x: x["pnl_amount"])[:3]
    return {
        "total_pnl": round(total_pnl),
        "total_pnl_display": f"{'+'if total_pnl>=0 else ''}{total_pnl/10000:.1f}万元",
        "profit_top3": profit_top3,
        "loss_top3": loss_top3,
    }


def _build_allocation_payload(
    user_query: str,
    data: LoadedData,
    capital_amount: float | None = None,
    portfolio_id: int | None = None,
) -> dict:
    """
    构建资产配置专用 payload，在组合基础上叠加目标区间、偏离度和计算引擎结果。
    """
    base = _build_portfolio_payload(user_query, data)
    base["capital_amount"] = capital_amount

    pid = portfolio_id or (data.raw_portfolio.id if data.raw_portfolio and hasattr(data.raw_portfolio, "id") else 1)

    try:
        from backend.services.allocation_service import (
            get_targets, get_deviation, compute_initial_plan, compute_increment_plan,
        )

        # 五大类目标区间
        targets = get_targets()
        _LABEL_MAP = {"cash": "货币", "fixed": "固收", "equity": "权益", "alt": "另类", "deriv": "衍生"}
        target_ranges = {}
        for t in targets:
            label = _LABEL_MAP.get(t.asset_class.value, t.asset_class.value)
            floor = f"{t.floor_ratio:.0%}" if t.floor_ratio is not None else "无"
            ceiling = f"{t.ceiling_ratio:.0%}" if t.ceiling_ratio is not None else "无"
            mid = f"{t.mid_ratio:.0%}" if t.mid_ratio is not None else "无"
            target_ranges[label] = f"{floor}~{ceiling}（中值{mid}）"

        # 当前偏离度
        dev = get_deviation(pid)
        deviation_data = {}
        for key, label in _LABEL_MAP.items():
            cls_dev = dev.by_class.get(key)
            if cls_dev:
                deviation_data[label] = (
                    f"当前{cls_dev.current_ratio:.1%}，"
                    f"目标中值{cls_dev.target_mid:.0%}，"
                    f"偏离{cls_dev.deviation:+.1%}（{cls_dev.deviation_level.value}）"
                )
        cash = dev.cash
        deviation_data["货币"] = (
            f"当前¥{cash.current_amount:,.0f}，"
            f"区间¥{cash.min_amount:,.0f}~¥{cash.max_amount:,.0f}，"
            f"状态：{cash.status.value}"
        )

        base["target_ranges"] = target_ranges
        base["deviation_from_target"] = deviation_data

        # 计算引擎：如有金额则调用精确计算
        computed_plan = None
        if capital_amount and capital_amount > 0:
            has_positions = data.total_assets > 0
            if has_positions:
                result = compute_increment_plan(pid, capital_amount)
            else:
                result = compute_initial_plan(capital_amount)

            computed_plan = {
                "total_amount": result.total_amount,
                "plan_items": [
                    {
                        "asset_class": item.label,
                        "current_ratio": f"{item.current_ratio:.1%}",
                        "target_mid": f"{item.target_mid:.1%}",
                        "deviation": f"{item.deviation:+.1%}",
                        "suggested_amount": round(item.suggested_amount),
                        "suggested_ratio": f"{item.suggested_ratio:.1%}",
                    }
                    for item in result.plan_items
                    if item.suggested_amount > 0
                ],
                "discipline_passed": result.discipline_check.passed if result.discipline_check else True,
            }

        base["computed_plan"] = computed_plan
        print(
            f"[llm_engine] allocation payload 注入成功: targets={len(target_ranges)}, "
            f"deviations={len(deviation_data)}, "
            f"computed_plan={'有' if computed_plan else '无'}, "
            f"capital={capital_amount}",
            flush=True,
        )

    except Exception as e:
        print(f"[llm_engine] allocation_service 不可用，降级: {e}", flush=True)
        base["target_ranges"] = None
        base["deviation_from_target"] = None
        base["computed_plan"] = None

    return base


def _call_generic_llm(
    intent_type: str,
    prompt: str,
    payload: dict,
) -> GenericLLMResult:
    """通用 LLM 调用，供组合级别意图共用。"""
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4.1",
            max_tokens=2048,
            timeout=30,
            messages=[
                {"role": "system", "content": _BASE_PROMPT + "\n\n" + prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
            ],
        )
        raw = response.choices[0].message.content.strip()
        parsed = _extract_json(raw)
        return GenericLLMResult(
            intent_type=intent_type,
            chat_answer=str(parsed.get("chat_answer", "") or ""),
            raw_payload=parsed,
            raw_output=raw,
        )
    except EnvironmentError as e:
        return _fallback_generic(intent_type, str(e))
    except openai.APITimeoutError:
        return _fallback_generic(intent_type, "系统繁忙，请稍后再试。")
    except openai.APIError as e:
        return _fallback_generic(intent_type, f"API 调用失败：{e}")
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        return _fallback_generic(intent_type, f"推理结果解析失败。（{type(e).__name__}）")
    except Exception as e:
        return _fallback_generic(intent_type, f"未知错误：{type(e).__name__}：{e}")


def review_portfolio(user_query: str, data: LoadedData) -> GenericLLMResult:
    """组合评估 LLM 推理（PortfolioReview）"""
    payload = _build_portfolio_payload(user_query, data)
    return _call_generic_llm("portfolio_review", _PORTFOLIO_REVIEW_PROMPT, payload)


def analyze_allocation(
    user_query: str,
    data: LoadedData,
    capital_amount: float | None = None,
    portfolio_id: int | None = None,
) -> GenericLLMResult:
    """资产配置 LLM 推理（AssetAllocation）"""
    payload = _build_allocation_payload(user_query, data, capital_amount, portfolio_id)
    return _call_generic_llm("asset_allocation", _ASSET_ALLOCATION_PROMPT, payload)


def analyze_performance(user_query: str, data: LoadedData) -> GenericLLMResult:
    """收益分析 LLM 推理（PerformanceAnalysis）"""
    payload = _build_portfolio_payload(user_query, data)
    return _call_generic_llm("performance_analysis", _PERFORMANCE_ANALYSIS_PROMPT, payload)


def _fallback_generic(intent_type: str, error_msg: str) -> GenericLLMResult:
    """组合级别意图的降级结果。"""
    return GenericLLMResult(
        intent_type=intent_type,
        chat_answer="",
        raw_payload={},
        error=error_msg,
    )


def respond_not_in_portfolio(user_query: str, asset_name: str) -> str:
    """
    生成"标的不在持仓中"的智能引导回复。

    用于用户询问一个未录入持仓的标的时（通常是卖出/止损/持有类操作），
    代替硬编码的错误信息，给出有帮助的引导。
    """
    context = f"用户原始问题：{user_query}\n识别到的标的：{asset_name}"
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=512,
            timeout=20,
            messages=[
                {"role": "system", "content": _BASE_PROMPT + "\n\n" + _NOT_IN_PORTFOLIO_PROMPT},
                {"role": "user", "content": context},
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return (
            f"我在您的持仓记录中没有找到 **{asset_name}** 的数据。\n\n"
            f"如果您已在其他平台持有但尚未录入，可以先在「投资账户总览」中添加持仓信息，"
            f"之后系统就能基于您的实际成本和仓位给出更准确的分析。\n\n"
            f"或者，您可以直接告诉我持仓数量和成本价，我可以帮您做参考推演。"
        )


def _fallback_result(error_msg: str, decision: str = "HOLD") -> LLMResult:
    """API 异常或解析失败时的降级结果（PRD §3.8：数据缺失→默认HOLD）。"""
    return LLMResult(
        decision=decision,
        reasoning=["当前无法完成 AI 推理，建议保持观望。"],
        risk=["请稍后重试，或手动评估当前持仓风险。"],
        strategy=["维持当前仓位，等待更多信息后再做决策。"],
        chat_answer="",
        raw_output="",
        error=error_msg,
    )
