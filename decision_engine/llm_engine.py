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

关于 realtime_market_data 字段（优先级最高）：
- 当 payload 中包含 realtime_market_data 时，其中的数字来自富途实时行情和 Alpha Vantage 结构化数据
- **优先使用 realtime_market_data 里的数字**，而非 research 里的文本描述
- 行情数据（现价/PE/PB/52周高低/涨跌幅）来源标注"（实时行情）"
- 财报数据（营收增速/净利增速/ROE/毛利率）来源标注"（AV 财报数据）"
- 分析师数据（目标价/评级/覆盖人数）来源标注"（AV 分析师数据）"
- 如果 research 中有与 realtime_market_data 冲突的数字，以 realtime_market_data 为准
- _unavailable_fields 列出的字段不可用，分析时不要提及或编造

关于纪律数据：
- 纪律校验结果中的"上限"是风险控制的硬性边界，不是建议仓位
- 如果当前仓位未超过纪律上限，不得说"超过上限"
- 目标仓位（如"建议降至 X%"）是AI建议值，必须说明推导依据（如"基于分散化原则"），禁止表述为"您设定的上限"
- 引用纪律数据时，格式为："您的单标的纪律上限是X%，当前仓位Y%，[已超出/距上限还有Z个百分点]"
- 【纪律值使用强约束】引用任何纪律数值（单标上限/权益上限/现金最低等）时，必须使用 rules 字段中的具体数值，严禁发明或引用此 prompt 模板中出现的任何示例百分比。若需AI建议的目标仓位（非纪律值），必须明确说"建议"二字且基于持仓数据推导

关于基本面和投研信息：
- research字段中有具体数字的，必须直接引用原始数字（如"净利润同比下降94%"），禁止替换为"大幅下滑"等模糊表述
- 引用优先级：[用户资料] > [第三方数据] > [联网参考]
- [用户资料]是用户自己整理审核过的观点，优先引用且不附链接
- [第三方数据]是 Alpha Vantage 等结构化第三方数据源（财报/基本面/新闻），引用时该句末尾附"（据公开数据）"文字
- 如果research字段为空或无有效内容，跳过基本面引用，不编造数字
- 分析师评级如果存在（如"大和重申买入"），在核心依据中一条带出

关于引用链接（强制执行，不得省略）：
- 有[ref:url]标注的内容，引用时该句末尾必须附 [[来源]](url)，不得省略
  例如："净利润同比下降85.8% [[来源]](https://wallstreetcn.com/...)"
- 无[ref:url]标注的[联网参考]内容，引用时该句末尾附"（据公开信息）"文字，不附链接
- 无[ref:url]标注的[第三方数据]内容，引用时该句末尾附"（据公开数据）"文字，不附链接
- 不得对没有URL的内容伪造链接或省略来源标注
- 每条引用数据最多附一个链接
- [用户资料]标注的内容不附链接
- 日期标注（如[2026-03]）不要出现在chat_answer正文中

时效性引用规则（基于每条观点末尾的"数据截至 YYYY-MM-DD"标注，对照 current_date 字段判断）：
- 7天内的数据：正常引用，无需额外说明
- 8-30天的数据：引用时在该句末尾注明"（据 YYYY-MM-DD 数据）"，提醒用户数据时效
- 30天以上的数据：只引用其中的长期逻辑和结构性判断，不引用具体数字和短期判断
- 超过90天的[第三方数据]来源新闻类数据：忽略不引用
- [用户资料]来源的数据不受以上时效规则限制

## 严禁字段名泄漏

你看到的 payload 是结构化 JSON，里面有 position_context / research /
discipline / signals / user_profile / realtime_market_data /
intent / rules 等字段名。这些都是**内部实现标识符**，**严禁**出现在
chat_answer 用户可见的输出里。

错误示范（禁止）：
- "持仓分布于老虎、雪盈、国金证券(position_context)" ❌
- "纪律校验提示接近上限(discipline)" ❌
- "做空比例创新高(research, 多条引用)" ❌
- "您的风险偏好为中高(user_profile)" ❌

正确示范：
- "持仓分布于老虎证券、雪盈证券、国金证券" ✅
- "纪律校验提示接近上限" ✅
- "做空比例创新高（综合多家媒体报道）" ✅
- "您的风险偏好为中高" ✅

允许的来源标注只有：
- （富途行情）/ （实时行情）— 实时报价数据
- （AV 数据）/ （AV 财报数据）/ （AV 分析师数据）— Alpha Vantage
- [[来源]](https://...) — 网页链接（Markdown 链接语法）
- （AV 数据 + 富途行情）— 多源融合时

不允许出现任何 payload 内部字段名、"据公开数据"等虚指、或编造的来源。

**数据缺失时的规范表述**：
当某个维度的数据不可用时（如 AV 限频、港股不支持等），
不允许用模糊语言填充，必须明确说明数据不可用：

错误示范（禁止）：
- "分析师评级普遍观望（据公开信息）" ❌
- "市场情绪偏谨慎（综合多方信息）" ❌
- "据市场普遍预期，估值偏高" ❌

正确示范：
- "情绪面：（AV 分析师数据暂不可用）" ✅
- "估值面：🟡 PE 73.96（富途行情），分析师数据暂缺" ✅
- 直接跳过该评级项，不输出这一行 ✅

原则：宁可少说一项，不用模糊语言填充。"""

# ── chat_answer 格式模板（按对话轮次动态选择）────────────────────────────────

_CHAT_FORMAT_FIRST_TURN = """
{scenario_instruction}

请按以下六段式深度分析框架输出 chat_answer（Markdown 格式）。
每段都必须有真实数据支撑，不允许出现"据公开数据"等虚指。
深度优先，不要为了简洁牺牲信息量。

**严格格式要求**：
- 六段标题必须**逐字**以 `### ` 开头（三个井号加一个空格），不允许省略井号
- 即使序号"一、二、三"已经标识了段落，**也必须**保留 `### ` 前缀
- 错误示范："一、市场快照" ❌（缺 ### ）
- 正确示范："### 一、市场快照" ✅
- 错误示范："**一、市场快照**" ❌（用粗体代替标题）
- 这个要求在 Markdown 渲染时是关键的视觉锚点，违反会导致页面层级混乱

### 一、市场快照

引用 realtime_market_data 的具体数据，2-4 句话覆盖：
- 当前股价 + 今日涨跌（标注"富途行情"）
- 52 周区间和当前位置
- PE/市值/EPS 等关键估值
- 行业地位简述

### 二、多维度诊断

用 🟢（健康）/ 🟡（关注）/ 🔴（警告）分别评级，每项 1-2 句数据支撑。
**重要格式要求**：基本面/估值面/情绪面/事件面/资金面各评级**必须每个独立成段**，
即每个评级前后用空行隔开，不允许压缩在同一行或同一段内。

**基本面**: [评级 emoji] [理由，引用营收/利润同比、毛利率、ROE]

**估值面**: [评级 emoji] [理由，引用 PE/PEG、与历史和同业对比]
（若 realtime_market_data.technical 存在，在估值面末尾追加一句技术面摘要，
如"技术面：股价处于均线下方，MACD死叉，RSI=38，偏空"，不单独成段）

**情绪面**: [评级 emoji] [理由，引用分析师评级分布、目标价上行空间]

**事件面**: [评级 emoji] [理由，引用近期重大事件：财报、监管、行业]

**资金面**: [评级 emoji] [引用 realtime_market_data.capitalFlow.interpretation，
用1句话表达资金流向信号，如"今日超大单净流出，散户接盘机构出货，资金面偏空"]
（若 capitalFlow 字段不存在或 dataAsOf 为空，跳过此项不输出）

### 三、组合与纪律检查

WealthPilot 独家视角，必须包含：
- 当前仓位占比 vs 单标上限，距离警戒线多远
- 跨平台合并持仓：列出该标的在各券商/平台的持仓数量，合并后总持仓
- 纪律命中情况：命中了哪条纪律规则，什么状态（警告/接近/正常）
- 当前组合中该标的的角色（核心/卫星/投机）

### 四、压力测试

**重要**：payload 中已包含 stress_test 字段，里面有后端预计算好的损益数字。
直接引用 stress_test 里的数字，不要自己计算，不要估算。

**必须用 bullet list 格式**，每个场景独立一行：
- 若股价跌 10%：亏损 X 元（占组合 Y%）
- 若跌至 52 周低点 X.XX 美元/港元：亏损 X 元（占组合 Y%）
- 若涨至分析师目标价 X.XX 美元/港元：盈利 X 元（占组合 Y%）

若 stress_test.rise_to_target 不存在：直接跳过第三行，不输出任何文字。
若 stress_test.rise_to_target.gain_cny 为 None 且 below_current=True：
  输出"若涨至分析师目标价 {price} 美元/港元：当前价格已超过分析师目标价"
若 stress_test.rise_to_target 存在且 gain_cny 有值：正常输出盈利金额。
若 stress_test.drop_to_52w_low.loss_cny 为 None：该行简短说明原因即可。
若 stress_test 字段完全不存在，跳过整段不输出。

### 五、操作策略

给出具体可执行的方案，而非泛泛建议：
- 推荐动作：加仓 / 减仓 / 持有 / 对冲（择一）
- 目标仓位占比：从当前 A% 调整到 B%
- 分批节奏：几批、间隔条件、价格触发
- 资金来源：新增资金 vs 内部调仓
- 止损/止盈位（如适用）

### 六、风险与跟踪

- 最大风险因素 1-2 个（具体到事件）
- 关键观察指标 2-3 个
- 下次评估时点（具体到日期或事件，如下一次财报、监管决定）

---

重要约束：
- 必须引用 realtime_market_data 中的具体数字，不允许虚指
- 数字来源标注：行情类标注（富途行情）；财报/分析师标注（AV 数据）；新闻类标注链接
- 跨平台持仓合并必须基于 position_context 中的真实数据
- 压力测试必须引用 stress_test 字段的预计算数字，不允许自己计算或编造
"""

_CHAT_FORMAT_FOLLOWUP = """
这是追问场景。请根据用户当前问题、对话上下文以及可用数据，
自主决定回答的结构、深度和长度。不要预设六段式框架，
也不要刻意追求简洁——长度由问题本身决定。

**严禁重复首轮内容**：
- 不要重新输出市场快照、多维度诊断、压力测试、组合检查等段落标题
- 不要把首轮已经给出的数字、结论、评级重新罗列一遍
- 追问是在首轮基础上深入，不是重新分析一遍
- 如需引用首轮的某个数字，直接引用即可，不要重新展开那个维度的完整分析

**不要重复前面已说过的结论**：
- 不要在回答末尾加"综上所述""总之"类的重复总结
- 如果核心建议首轮已经给出，追问里直接给增量信息（更具体的价位/时机/条件）

唯一的硬约束：
- 涉及具体数字必须引用 realtime_market_data，并标注来源（富途行情/AV 数据）
- 不允许"据公开数据""市场普遍预期"等虚指
- 不允许编造 realtime_market_data 中没有的数字
- 跨平台持仓数据必须基于 position_context

其他（段落结构、是否用表格、是否用 emoji、是否分点）完全由你判断。
"""


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
引用 research 字段中与持仓最相关的[第三方数据]或[联网参考]内容，2-3条，用"-"开头。
有[ref:url]的引用必须附 [[来源]](url)，无URL的[第三方数据]附"（据公开数据）"，无URL的[联网参考]附"（据公开信息）"。
聚焦与持仓行业或大类资产直接相关的内容（如持仓有科技股则引用科技行业展望，有固收则引用债券市场展望，有黄金则引用黄金走势）。
如果 research 字段为空或无相关内容，跳过此段，不输出"市场背景"标题。

### 主要风险
结合组合结构和市场背景，推导出2-3条最实质的风险点，用"-"开头。
风险点应该是内部结构问题与外部市场信号叠加后的判断，不是单纯列结构问题。

### 调整建议
2-3条可执行的方向，说明优先级，用"-"开头。
建议必须基于前四段的分析，不得凭空给出。
如不需要调整，说明原因。

关于引用链接（强制执行）：凡引用带[ref:url]标注的内容，必须在句末附 [[来源]](url)。无[ref:url]标注的[第三方数据]，末尾附"（据公开数据）"。无[ref:url]标注的[联网参考]，末尾附"（据公开信息）"。[用户资料]标注的不附链接。"""


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

关于引用链接（强制执行）：凡引用带[ref:url]标注的内容，必须在句末附 [[来源]](url)。无[ref:url]标注的[第三方数据]，末尾附"（据公开数据）"。无[ref:url]标注的[联网参考]，末尾附"（据公开信息）"。[用户资料]标注的不附链接。"""


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
    if not chat_answer:
        print(f"[llm_engine] ⚠️ chat_answer 为空！structured keys={list(structured.keys())}", flush=True)

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
    market_data: object | None = None,
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
    # 构建输入 payload（含市场数据 + 压力测试预计算）
    payload = _build_payload(user_query, data, intent, rule_result, signals, market_data=market_data)
    _stress_test_data = payload.get("stress_test")  # 保存供注入 structured_result

    # 根据对话轮次选择 chat_answer 格式
    is_followup = bool(conversation_history)
    chat_format = _CHAT_FORMAT_FOLLOWUP if is_followup else _CHAT_FORMAT_FIRST_TURN
    # 注入场景化指令（仅首轮）
    if not is_followup:
        chat_format = chat_format.replace(
            "{scenario_instruction}",
            payload.get("scenario_instruction", ""),
        )
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
                    # 不截断 — assistant 历史完整保留，确保 LLM 在追问时
                    # 能看到上一轮的完整分析（核心依据/操作策略/具体数字），
                    # 维持对话锚点。token 成本由 max_tokens 上限自然约束。
                    messages.append({"role": "assistant", "content": turn["content"]})
        messages.append({"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)})

        # 防御性日志：监控 messages 总长度
        total_chars = sum(len(m.get("content", "")) for m in messages)
        if total_chars > 30000:
            print(f"[reason] ⚠️ messages 总长 {total_chars} 字符，接近 context 上限", flush=True)

        response = client.chat.completions.create(
            model="gpt-4.1",
            max_tokens=4096,
            timeout=60,
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

def calculate_stress_test(
    market_value_cny: float,
    total_assets_cny: float,
    current_price: float,
    low_52w: float | None,
    analyst_target_price: float | None,
    currency: str = "USD",
) -> dict:
    """
    后端计算压力测试结果，返回结构化数字供 LLM 直接引用。
    所有金额单位为人民币（CNY）。
    价格比值计算时单位自然抵消，不需要汇率转换。
    """
    try:
        if not market_value_cny or market_value_cny <= 0 or not current_price or current_price <= 0:
            return {"data_available": False}

        result: dict = {
            "data_available": True,
            "position_value_cny": round(market_value_cny, 0),
            "currency": currency,
        }

        # 场景1：跌10%
        loss_10pct = market_value_cny * 0.10
        result["drop_10pct"] = {
            "loss_cny": round(loss_10pct, 0),
            "portfolio_pct": round(loss_10pct / total_assets_cny * 100, 2)
            if total_assets_cny > 0 else None,
        }

        # 场景2：跌至52周低点
        if low_52w and low_52w > 0 and low_52w < current_price:
            drop_ratio = (current_price - low_52w) / current_price
            loss_52w = market_value_cny * drop_ratio
            result["drop_to_52w_low"] = {
                "loss_cny": round(loss_52w, 0),
                "portfolio_pct": round(loss_52w / total_assets_cny * 100, 2)
                if total_assets_cny > 0 else None,
                "price": low_52w,
                "drop_pct": round(drop_ratio * 100, 1),
            }
        else:
            result["drop_to_52w_low"] = {"loss_cny": None, "note": "当前价格已接近或低于52周低点"}

        # 场景3：涨至分析师目标价
        if analyst_target_price and analyst_target_price > current_price:
            upside_ratio = (analyst_target_price - current_price) / current_price
            gain_target = market_value_cny * upside_ratio
            result["rise_to_target"] = {
                "gain_cny": round(gain_target, 0),
                "portfolio_pct": round(gain_target / total_assets_cny * 100, 2)
                if total_assets_cny > 0 else None,
                "price": analyst_target_price,
                "upside_pct": round(upside_ratio * 100, 1),
            }
        else:
            if analyst_target_price and analyst_target_price <= current_price:
                # 目标价低于或等于现价
                result["rise_to_target"] = {
                    "gain_cny": None,
                    "price": analyst_target_price,
                    "below_current": True,
                }
            elif not analyst_target_price:
                # 无目标价数据时不设置 rise_to_target，prompt 检查到字段缺失直接跳过
                pass
            else:
                pass  # 其他异常情况也静默跳过

        return result

    except Exception:
        return {"data_available": False}


def determine_scenario(
    position_signal: str,
    fundamental_signal: str,
    profit_loss_rate: float | None,
    rule_violated: bool = False,
) -> str:
    """
    根据四维信号判断当前分析场景，返回对应的侧重指令字符串。
    注入到 _CHAT_FORMAT_FIRST_TURN 的 {scenario_instruction} 占位符。

    优先级：重仓减仓 > 浮亏止损 > 加仓 > 长期持有（默认）
    """
    plr = profit_loss_rate or 0.0

    # 场景1：重仓减仓
    if position_signal == "偏高" and plr > -0.15:
        return """
**本次分析场景：重仓减仓**
用户核心关切是"如何安全减仓"，请按以下侧重点展开：
- 一、市场快照：简短（2-3句），快速交代现价和估值区间即可
- 三、组合与纪律检查：重点展示当前仓位距纪律上限的距离，强调集中度风险
- 五、操作策略：**本场景最重要的段落**，必须包含：
  * 目标仓位（具体百分比）
  * 分几批执行（2-3批）
  * 每批减仓的触发条件（价格区间或时间节点）
  * 减出来的资金建议去向（再平衡方向）
- 六、风险与跟踪：简短，聚焦"减仓过程中"的风险（踏空风险/流动性风险）
"""

    # 场景2：浮亏止损
    if plr < -0.20:
        return """
**本次分析场景：浮亏评估**
用户核心关切是"这个亏损还能回来吗，什么时候止损"，请按以下侧重点展开：
- 二、多维度诊断：**本场景最重要的段落**，重点判断基本面有无改善迹象：
  * 基本面是否有转机信号（营收/利润趋势）
  * 估值面在当前价位是否已经合理
  * 情绪面分析师是否还在覆盖/是否下调目标价
- 四、压力测试：重点段，帮用户直观感受"如果继续持有继续跌"的最大损失
- 五、操作策略：必须包含：
  * 明确的止损位（什么价格/什么信号出现时离场）
  * 继续持有的前提条件（基本面需要满足什么条件）
  * 机会成本分析（持有这个浮亏标的 vs 换仓其他标的）
- 一、市场快照：简短，快速交代现价在52周区间的位置即可
"""

    # 场景3：加仓
    if position_signal == "偏低" and fundamental_signal in ("正面", "中性", "N/A"):
        return """
**本次分析场景：加仓评估**
用户核心关切是"现在是不是好的买入时机，加多少合适"，请按以下侧重点展开：
- 二、多维度诊断：重点判断当前买入时机：
  * 估值面是否处于合理/低估区间（PE/PB历史分位）
  * 基本面增长趋势是否持续
  * 情绪面分析师目标价相对现价的上行空间
- 三、组合与纪律检查：重点展示还有多少加仓空间（距纪律上限的余量）
- 五、操作策略：**本场景最重要的段落**，必须包含：
  * 建议加仓的目标仓位（从当前X%到Y%）
  * 分批计划（几批、间隔多久、每批触发条件）
  * 建议的资金来源（新增资金 vs 内部调仓）
- 四、压力测试：展示加仓后如果继续下跌的风险，帮用户评估风险承受度
"""

    # 默认场景：持有评估
    return """
**本次分析场景：持有评估**
用户核心关切是"当前持仓是否值得继续拿"，请均衡展开六段式分析：
- 各段均衡，不特别侧重某一段
- 六、风险与跟踪：稍作强调，给出明确的长期观察指标和下次评估时点
- 操作策略：如无明显操作信号，"继续持有+设置观察条件"也是有效建议
"""


def _interpret_capital_flow(cf) -> str:
    """
    资金流向解读：以超大单+大单（聪明钱）为主信号判断资金面方向。
    小单（散户）作为辅助参考，不影响主方向判断。
    """
    if cf is None:
        return "数据不足"

    # 计算主力方向（超大单+大单合计）
    smart_money = 0.0
    if cf.super_net is not None:
        smart_money += cf.super_net
    if cf.big_net is not None:
        smart_money += cf.big_net

    signals = []

    # 货币单位
    currency = "港元" if ".HK" in (cf.symbol or "") else "美元"

    def fmt(val):
        return f"{abs(val) / 10000:.1f}万{currency}"

    # 机构资金信号（超大单）
    if cf.super_net is not None:
        if cf.super_net < -50000:
            signals.append(f"今日机构资金净卖出约 {fmt(cf.super_net)}")
        elif cf.super_net > 50000:
            signals.append(f"今日机构资金净买入约 {fmt(cf.super_net)}")

    # 散户资金信号（小单）
    if cf.small_net is not None:
        if cf.small_net > 50000:
            signals.append(f"散户资金净买入约 {fmt(cf.small_net)}")
        elif cf.small_net < -50000:
            signals.append(f"散户资金净卖出约 {fmt(cf.small_net)}")

    # 背离特征
    if cf.small_net is not None and cf.super_net is not None:
        if cf.small_net > 50000 and cf.super_net < -50000:
            signals.append("呈现散户接盘、机构出货特征")
        elif cf.small_net < -50000 and cf.super_net > 50000:
            signals.append("呈现散户出逃、机构抄底特征")

    # 主方向判断（以聪明钱为准）
    if smart_money < -100000:
        overall = "资金面偏空"
    elif smart_money > 100000:
        overall = "资金面偏多"
    else:
        overall = "资金面中性"

    if signals:
        return "、".join(signals) + f"，{overall}"
    return overall


def _interpret_technical(td) -> str:
    """
    把技术指标解读为自然语言，供 LLM 直接引用。
    """
    if td is None:
        return "数据不足"

    parts = []

    if td.ma_position == "above_both":
        parts.append(f"股价({td.current_price})处于MA5({td.ma5})/MA20({td.ma20})上方")
    elif td.ma_position == "below_both":
        parts.append(f"股价({td.current_price})处于MA5({td.ma5})/MA20({td.ma20})下方")
    elif td.ma_position == "between":
        parts.append(f"股价({td.current_price})处于MA5({td.ma5})与MA20({td.ma20})之间")

    if td.rsi14 is not None:
        if td.rsi14 > 70:
            parts.append(f"RSI={td.rsi14}(超买区间)")
        elif td.rsi14 < 30:
            parts.append(f"RSI={td.rsi14}(超卖区间)")
        else:
            parts.append(f"RSI={td.rsi14}(中性)")

    if td.macd_hist is not None:
        if td.macd_hist > 0:
            parts.append("MACD金叉")
        else:
            parts.append("MACD死叉")

    trend_map = {"bullish": "技术面偏多", "bearish": "技术面偏空", "neutral": "技术面中性"}
    summary = trend_map.get(td.trend_signal, "技术面中性")

    if parts:
        return "、".join(parts) + f"，{summary}"
    return summary


def _build_payload(
    user_query: str,
    data: LoadedData,
    intent: IntentResult,
    rule_result: RuleResult,
    signals: SignalResult,
    market_data: object | None = None,
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

    from datetime import datetime as _dt
    payload = {
        "user_query": user_query,
        "current_date": _dt.now().strftime("%Y-%m-%d"),
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
        "realtime_market_data": (
            {k: v for k, v in market_data.to_snapshot_dict().items()
             if v is not None and k != "missingFields"}
            | ({"_unavailable_fields": market_data.to_snapshot_dict().get("missingFields", [])}
               if market_data and market_data.to_snapshot_dict().get("missingFields") else {})
        ) if market_data and hasattr(market_data, "to_snapshot_dict") else None,
        "research": data.research,
        "user_profile": {
            "risk_level": data.profile.risk_level,
            "goal": data.profile.goal,
        },
    }

    # 资金流向解读注入
    if (market_data and hasattr(market_data, 'capital_flow')
            and market_data.capital_flow
            and payload.get("realtime_market_data")
            and payload["realtime_market_data"].get("capitalFlow")):
        payload["realtime_market_data"]["capitalFlow"]["interpretation"] = \
            _interpret_capital_flow(market_data.capital_flow)

    # 技术面解读注入
    if (market_data and hasattr(market_data, 'technical')
            and market_data.technical
            and payload.get("realtime_market_data")):
        td = market_data.technical
        payload["realtime_market_data"]["technical"] = {
            "ma5": td.ma5,
            "ma20": td.ma20,
            "rsi14": td.rsi14,
            "macdHist": td.macd_hist,
            "maPosition": td.ma_position,
            "trendSignal": td.trend_signal,
            "interpretation": _interpret_technical(td),
            "dataAsOf": td.data_as_of,
        }

    # 场景判断（用于动态调整六段式侧重点）
    _plr = data.target_position.profit_loss_rate if data.target_position else None
    payload["scenario_instruction"] = determine_scenario(
        position_signal=signals.position_signal if signals else "合理",
        fundamental_signal=signals.fundamental_signal if signals else "中性",
        profit_loss_rate=_plr,
        rule_violated=rule_result.violation if rule_result else False,
    )

    # 压力测试（后端预计算，不让 LLM 自己算）
    if market_data and hasattr(market_data, 'has_quote') and market_data.has_quote:
        q = market_data.quote
        f = getattr(market_data, 'fundamentals', None)
        a = getattr(f, 'analyst', None) if f else None
        tp = data.target_position

        if tp and tp.market_value_cny > 0 and data.total_assets > 0:
            st = calculate_stress_test(
                market_value_cny=tp.market_value_cny,
                total_assets_cny=data.total_assets,
                current_price=q.current_price,
                low_52w=q.low_52w,
                analyst_target_price=a.target_price_avg if a else None,
                currency=getattr(q, 'currency', 'USD'),
            )
            if st.get("data_available"):
                payload["stress_test"] = st

    return payload


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

# WealthPilot 资产配置理念（原 Allocation.tsx 前端"配置原则说明"卡片内容）
# 资产配置模块下线后，这段内容作为 Education 意图的固定背景知识注入 system prompt。
# 未来接入 RAG 时，可迁移到知识库作为可检索文档。
WEALTHPILOT_ALLOCATION_PRINCIPLES = """
## WealthPilot 资产配置理念

**多元资产配置**：货币保流动性，固收稳底盘，权益求增长，另类分散风险，衍生作战术工具，不同资产解决不同问题。

**目标区间管理**：每类资产都有自己的目标区间，资产配置的重点不是判断短期涨跌，而是让整体结构长期保持在合理范围内。

**动态再平衡**：当配置出现偏离时，优先通过新增资金自然修正，减少不必要的卖出操作，只有偏离明显时才考虑主动调整。
"""

_ALLOCATION_PRINCIPLES_GUIDE = (
    "当用户问及资产配置相关概念（如多元资产配置、目标区间管理、动态再平衡）时，"
    "请优先采用以下 WealthPilot 自有定义来回答，保持产品语言一致性。"
)


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
            messages=[{"role": "system", "content": (
                _BASE_PROMPT + "\n\n" + _GENERAL_CHAT_PROMPT
                + "\n\n" + _ALLOCATION_PRINCIPLES_GUIDE
                + "\n\n" + WEALTHPILOT_ALLOCATION_PRINCIPLES
            )}] + messages,
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

    from datetime import datetime as _dt
    return {
        "user_query": user_query,
        "current_date": _dt.now().strftime("%Y-%m-%d"),
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
    conversation_history: list[dict] | None = None,
) -> GenericLLMResult:
    """通用 LLM 调用，供组合级别意图共用。"""
    try:
        client = _get_client()
        messages = [{"role": "system", "content": _BASE_PROMPT + "\n\n" + prompt}]
        if conversation_history:
            for turn in conversation_history:
                if turn.get("role") in ("user", "assistant") and turn.get("content"):
                    content = turn["content"]
                    # 截断过长的 assistant 消息，避免 token 溢出
                    if turn["role"] == "assistant" and len(content) > 500:
                        content = content[:300] + "\n…（省略）…\n" + content[-200:]
                    messages.append({"role": turn["role"], "content": content})
        messages.append({"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)})
        response = client.chat.completions.create(
            model="gpt-4.1",
            max_tokens=2048,
            timeout=30,
            messages=messages,
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


def review_portfolio(
    user_query: str,
    data: LoadedData,
    extra_instruction: str = "",
    conversation_history: list[dict] | None = None,
) -> GenericLLMResult:
    """组合评估 LLM 推理（PortfolioReview）"""
    payload = _build_portfolio_payload(user_query, data)
    prompt = _PORTFOLIO_REVIEW_PROMPT
    if extra_instruction:
        prompt = _PORTFOLIO_REVIEW_PROMPT + "\n\n" + extra_instruction
    return _call_generic_llm("portfolio_review", prompt, payload, conversation_history)


def analyze_allocation(
    user_query: str,
    data: LoadedData,
    capital_amount: float | None = None,
    portfolio_id: int | None = None,
    conversation_history: list[dict] | None = None,
) -> GenericLLMResult:
    """资产配置 LLM 推理（AssetAllocation）"""
    payload = _build_allocation_payload(user_query, data, capital_amount, portfolio_id)
    return _call_generic_llm("asset_allocation", _ASSET_ALLOCATION_PROMPT, payload, conversation_history)


def analyze_performance(
    user_query: str,
    data: LoadedData,
    conversation_history: list[dict] | None = None,
) -> GenericLLMResult:
    """收益分析 LLM 推理（PerformanceAnalysis）"""
    payload = _build_portfolio_payload(user_query, data)
    return _call_generic_llm("performance_analysis", _PERFORMANCE_ANALYSIS_PROMPT, payload, conversation_history)


# ── 多标的横向对比（P2 新增）─────────────────────────────────────────────────

_MULTI_ASSET_COMPARE_PROMPT = """你是专业投资顾问,正在为客户撰写多标的横向对比报告。

【你的任务】
基于客户提供的多个标的的初步分析数据,生成一份专业的横向对比决策报告。
**核心要求: 这是一份"对比"报告,不是"汇总"报告。**

【输出结构】

## 综合判断

(以 200 字以内段落形式给出)

包含 3 个核心要素:
1. **排序结论**: 直接回答客户问题,明确给出标的优先级排序及理由
2. **资金分配建议**: 如果客户有可投资金,建议按什么比例配置(如 6:4 或 5:3:2)
3. **核心理由**: 为什么这样排序? 一句话点明各标的之间的"差异化定位"或"互补关系"

## 维度对比

(从 3-5 个**对比维度**展开,每个维度独立小节,每节都要含一个 markdown 表格)

**重要原则**:
- **维度由你根据具体标的特点自由选择**,不预设固定维度
- 每个维度的对比表格必须包含: 第一列是"维度"(或具体子项),后续每列对应一个标的
- 表格至少 3 行(子项),避免过于简陋
- 表格行内容要**直接对比**,不能用"详见上文"等回避表述
- 选择的维度应**直接支撑你在'综合判断'中给出的排序结论**

**维度选择参考**(不限于此,你应根据标的特点灵活选择):
- 仓位/估值类: 当前仓位、距离上限、估值水平、加仓空间
- 基本面类: 营收/利润增长、行业地位、护城河、业务可见度
- 风险类: 主要风险、风险性质、对冲难度、不确定性
- 操作类: 加仓方式、触发节点、止损纪律
- 时机类: 当前买入时机、催化剂、关键监控点
- 配置类: 与组合现有持仓的相关性、对组合贡献

## 风险提示

仅供参考,不构成投资建议。投资有风险,入市需谨慎。

【关键约束】
1. 必须使用 markdown 表格做对比,不能用纯文字罗列
2. 必须先给排序结论再展开对比维度
3. 资金分配建议要具体(给出比例),不能笼统说"均衡配置"
4. 对比维度的子项要有数据支撑,直接引用客户提供的标的数据
5. 不允许复制"独立分析"中的整段内容——必须重新组织为对比形式
6. 表格列数 = 标的数量(2 标 2 列, 3 标 3 列, 不含"维度"列)
7. **纪律上限统一性**: 单一标的仓位上限/权益类上限等纪律值是全局统一的,对所有标的相同。**严禁在对比表中为不同标的显示不同的纪律上限值**(这是事实错误)。如果要展示纪律,只展示 **当前仓位 vs 全局纪律上限** 的对比维度。
8. **数据引用**: 对比维度的具体数据(EPS / 营收 / 增长率等)优先从 summaries 的 research 字段中提取,research 是一个文本列表,包含真实的财务和投研数据。如果某个维度数据缺失,使用 reasoning 中的自然语言描述代替,**严禁写"未提供数据"/"暂无数据"等表述**(用户体验差)。
9. **不发明事实**: 不要发明 summaries 和 global_rules 中未明确提供的数值。特别是不要发明任何投资纪律值,只使用前置信息块中明确告知的数值。
"""


def compare_multi_assets(
    user_query: str,
    summaries: list[dict],
    global_rules: dict | None = None,
) -> str:
    """
    多标的横向对比 LLM 调用。

    Args:
        user_query: 用户原始问句
        summaries: 标的摘要列表,每个元素 dict 含 name/weight_pct/pnl_rate_pct/decision/reasoning/risk/signals/research
        global_rules: 全局投资纪律(从 data.rules 单一数据源取,不硬编码 default)

    Returns:
        综合对比报告(markdown 格式)
    """
    global_rules = global_rules or {}

    # 构造纪律前置信息块(只展示真实存在的纪律,不发明 default)
    global_info_lines = []
    if global_rules.get("max_single_position_pct") is not None:
        global_info_lines.append(
            f"- 单一标的最大仓位上限: {global_rules['max_single_position_pct']}%"
            f"(硬性纪律,对所有标的统一适用)"
        )
    if global_rules.get("max_equity_pct") is not None:
        global_info_lines.append(
            f"- 权益类资产上限: {global_rules['max_equity_pct']}%"
        )
    if global_rules.get("min_cash_pct") is not None:
        global_info_lines.append(
            f"- 现金最低比例: {global_rules['min_cash_pct']}%"
        )

    if global_info_lines:
        global_info = (
            "\n【全局投资纪律(对所有标的统一适用,不要在对比表中显示不同的纪律值)】\n"
            + "\n".join(global_info_lines) + "\n"
        )
    else:
        global_info = ""

    summaries_text = json.dumps(summaries, ensure_ascii=False, indent=2)
    user_prompt = f"客户问题: {user_query}\n{global_info}\n需要对比的标的数据:\n{summaries_text}\n\n请按系统提示词的格式生成横向对比报告。"

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=2048,
            timeout=30,
            temperature=0.3,
            messages=[
                {"role": "system", "content": _BASE_PROMPT + "\n\n" + _MULTI_ASSET_COMPARE_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[llm_engine] compare_multi_assets 失败: {e}", flush=True)
        raise


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
