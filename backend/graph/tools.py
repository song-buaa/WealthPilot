"""
WealthPilot Tool Layer (v2.6 M2)

7 个核心 Tool，每个 Tool 包含：
  1. Pydantic 输入/输出 Schema
  2. JSON Schema 描述（供 LLM Function Calling 使用）
  3. execute() 函数（包装现有逻辑，不改底层实现）

分类：
  静态 Tool（业务规则触发）：fetch_holdings / check_discipline_rules /
                              calc_allocation_deviation / propose_increment_plan
  动态 Tool（Agent 自主决定）：query_viewpoint_cards / fetch_realtime_research /
                               web_search
"""

from __future__ import annotations
from typing import Optional, Any
from pydantic import BaseModel


# ══════════════════════════════════════════════════════════════════
# Tool 1：fetch_holdings（静态）
# ══════════════════════════════════════════════════════════════════

class FetchHoldingsInput(BaseModel):
    portfolio_id: int = 1

class HoldingItem(BaseModel):
    name: str
    ticker: str
    asset_class: str
    market_value_cny: float
    pl_rate: float          # 百分比数值，如 99.9 = +99.9%
    weight: float           # 0~1 小数
    platforms: list[str]

class FetchHoldingsOutput(BaseModel):
    positions: list[HoldingItem]
    total_assets_cny: float
    count: int

FETCH_HOLDINGS_SCHEMA = {
    "name": "fetch_holdings",
    "description": "拉取用户当前持仓快照，返回各持仓的市值、盈亏率、仓位占比等数据。"
                   "是所有决策分析的基础数据源，必须在分析前调用。",
    "parameters": {
        "type": "object",
        "properties": {
            "portfolio_id": {
                "type": "integer",
                "description": "Portfolio ID，默认为 1",
                "default": 1,
            }
        },
        "required": [],
    },
}

def execute_fetch_holdings(portfolio_id: int = 1) -> FetchHoldingsOutput:
    from app.utils.position_aggregator import aggregate_investment_positions
    positions, total = aggregate_investment_positions(portfolio_id)
    items = [
        HoldingItem(
            name=p.name,
            ticker=p.ticker,
            asset_class=p.asset_class,
            market_value_cny=p.market_value_cny,
            pl_rate=p.pl_rate,
            weight=p.weight,
            platforms=p.platforms or [],
        )
        for p in positions
    ]
    return FetchHoldingsOutput(
        positions=items,
        total_assets_cny=total,
        count=len(items),
    )


# ══════════════════════════════════════════════════════════════════
# Tool 2：check_discipline_rules（静态）
# ══════════════════════════════════════════════════════════════════

class CheckDisciplineInput(BaseModel):
    asset_name: str
    portfolio_id: int = 1
    action_type: str = "HOLD"   # BUY / HOLD / SELL / REDUCE 等

class DisciplineCheckOutput(BaseModel):
    violation: bool
    warning: Optional[str]
    current_weight: float
    max_position: float
    position_ratio: float
    rule_details: list[str]

CHECK_DISCIPLINE_SCHEMA = {
    "name": "check_discipline_rules",
    "description": "对指定标的的拟议操作进行纪律校验，检查是否超过仓位上限等投资纪律规则。"
                   "任何涉及买入/加仓的决策前必须调用。",
    "parameters": {
        "type": "object",
        "properties": {
            "asset_name": {
                "type": "string",
                "description": "标的名称，如'贵州茅台'",
            },
            "portfolio_id": {
                "type": "integer",
                "description": "Portfolio ID，默认为 1",
                "default": 1,
            },
            "action_type": {
                "type": "string",
                "description": "拟议操作类型：BUY / HOLD / SELL / REDUCE / TAKE_PROFIT",
                "default": "HOLD",
            },
        },
        "required": ["asset_name"],
    },
}

# action 枚举 → IntentResult.action_type 中文映射
_ACTION_TO_CN = {
    "BUY": "买入判断",
    "ADD": "加仓判断",
    "SELL": "卖出判断",
    "STOP_LOSS": "卖出判断",
    "TAKE_PROFIT": "减仓判断",
    "REDUCE": "减仓判断",
    "HOLD": "持有评估",
    "ANALYZE": "持有评估",
}

def execute_check_discipline(
    asset_name: str,
    portfolio_id: int = 1,
    action_type: str = "HOLD",
) -> DisciplineCheckOutput:
    from decision_engine import data_loader, rule_engine
    from decision_engine.types import IntentResult

    data = data_loader.load(asset_name=asset_name, pid=portfolio_id)

    # 构造最小 IntentResult 供 rule_engine.check 使用
    intent = IntentResult(
        asset=asset_name,
        action_type=_ACTION_TO_CN.get(action_type.upper(), "持有评估"),
        time_horizon="未知",
        trigger=None,
        confidence_score=1.0,
    )

    result = rule_engine.check(data, intent)
    return DisciplineCheckOutput(
        violation=result.violation,
        warning=result.warning,
        current_weight=result.current_weight,
        max_position=result.max_position,
        position_ratio=result.position_ratio,
        rule_details=result.rule_details or [],
    )


# ══════════════════════════════════════════════════════════════════
# Tool 3：calc_allocation_deviation（静态）
# ══════════════════════════════════════════════════════════════════

class CalcDeviationInput(BaseModel):
    portfolio_id: int = 1

class ClassDeviationItem(BaseModel):
    asset_class: str
    current_ratio: float
    target_mid: float
    deviation: float
    deviation_level: str    # "normal" / "mild" / "significant"

class CalcDeviationOutput(BaseModel):
    by_class: list[ClassDeviationItem]
    summary: str            # 人类可读摘要

CALC_DEVIATION_SCHEMA = {
    "name": "calc_allocation_deviation",
    "description": "计算当前组合相对目标区间的偏离情况，返回各资产类别的偏离度。"
                   "用于组合评估和资产配置建议。",
    "parameters": {
        "type": "object",
        "properties": {
            "portfolio_id": {
                "type": "integer",
                "description": "Portfolio ID，默认为 1",
                "default": 1,
            }
        },
        "required": [],
    },
}

def execute_calc_deviation(portfolio_id: int = 1) -> CalcDeviationOutput:
    from backend.services.allocation_service import get_deviation
    snapshot = get_deviation(portfolio_id)
    items = []
    for cls_name, dev in (snapshot.by_class or {}).items():
        items.append(ClassDeviationItem(
            asset_class=cls_name,
            current_ratio=getattr(dev, 'current_ratio', 0),
            target_mid=getattr(dev, 'target_mid', 0),
            deviation=getattr(dev, 'deviation', 0),
            deviation_level=getattr(dev, 'deviation_level', type('', (), {'value': 'normal'})()).value
                if hasattr(getattr(dev, 'deviation_level', None), 'value')
                else str(getattr(dev, 'deviation_level', 'normal')),
        ))
    over = [i for i in items if i.deviation_level in ("significant", "mild")]
    under = [i for i in items if i.deviation < 0 and i.deviation_level in ("significant", "mild")]
    over_only = [i for i in items if i.deviation > 0 and i.deviation_level in ("significant", "mild")]
    parts = []
    if over_only:
        parts.append(f"超配：{', '.join(i.asset_class for i in over_only)}")
    if under:
        parts.append(f"欠配：{', '.join(i.asset_class for i in under)}")
    summary = "；".join(parts) if parts else "各类别配置均在目标区间内"
    return CalcDeviationOutput(by_class=items, summary=summary)


# ══════════════════════════════════════════════════════════════════
# Tool 4：propose_increment_plan（静态）
# ══════════════════════════════════════════════════════════════════

class IncrementPlanInput(BaseModel):
    portfolio_id: int = 1
    increment_amount: float     # 新增资金金额（元）

class PlanItemOut(BaseModel):
    asset_class: str            # 中文标签，如"权益"/"固收"/"货币"
    suggested_amount: float
    suggested_ratio: float

class IncrementPlanOutput(BaseModel):
    total_amount: float
    plan_items: list[PlanItemOut]
    summary: str

PROPOSE_INCREMENT_SCHEMA = {
    "name": "propose_increment_plan",
    "description": "基于当前组合偏离情况，生成新增资金的分配建议方案。"
                   "用于'我有一笔新资金怎么配'类的资产配置请求。",
    "parameters": {
        "type": "object",
        "properties": {
            "portfolio_id": {
                "type": "integer",
                "description": "Portfolio ID，默认为 1",
                "default": 1,
            },
            "increment_amount": {
                "type": "number",
                "description": "新增资金金额，单位元，如 200000 表示 20 万",
            },
        },
        "required": ["increment_amount"],
    },
}

def execute_propose_increment(
    portfolio_id: int = 1,
    increment_amount: float = 0,
) -> IncrementPlanOutput:
    from backend.services.allocation_service import compute_increment_plan
    result = compute_increment_plan(portfolio_id, increment_amount)
    items = []
    for item in (result.plan_items or []):
        items.append(PlanItemOut(
            asset_class=item.label,
            suggested_amount=item.suggested_amount,
            suggested_ratio=item.suggested_ratio,
        ))
    summary = f"建议将 {increment_amount:,.0f} 元按以下比例分配：" + \
              "、".join(f"{i.asset_class} {i.suggested_ratio:.0%}" for i in items)
    return IncrementPlanOutput(
        total_amount=increment_amount,
        plan_items=items,
        summary=summary,
    )


# ══════════════════════════════════════════════════════════════════
# Tool 5：query_viewpoint_cards（动态）
# ══════════════════════════════════════════════════════════════════

class QueryViewpointInput(BaseModel):
    asset_name: str
    portfolio_id: int = 1

class ViewpointCardOut(BaseModel):
    content: str            # 格式化文本
    source: str = "local"

class QueryViewpointOutput(BaseModel):
    cards: list[ViewpointCardOut]
    count: int
    asset_name: str

QUERY_VIEWPOINT_SCHEMA = {
    "name": "query_viewpoint_cards",
    "description": "查询指定标的的本地投研卡片（已确认的研究观点）。"
                   "优先查本地库，本地无数据时返回空列表（由调用方决定是否触发实时拉取）。",
    "parameters": {
        "type": "object",
        "properties": {
            "asset_name": {
                "type": "string",
                "description": "标的名称，如'贵州茅台'、'AAPL'",
            },
            "portfolio_id": {
                "type": "integer",
                "default": 1,
            },
        },
        "required": ["asset_name"],
    },
}

def execute_query_viewpoint(
    asset_name: str,
    portfolio_id: int = 1,
) -> QueryViewpointOutput:
    from decision_engine import data_loader
    # _load_research 是模块级私有函数，签名: _load_research(session, pid, asset_name)
    # 通过 data_loader.load() 间接调用更安全
    try:
        loaded = data_loader.load(asset_name=asset_name, pid=portfolio_id)
        cards_text = loaded.research or []
    except Exception:
        cards_text = []
    cards = [ViewpointCardOut(content=c, source="local") for c in cards_text]
    return QueryViewpointOutput(
        cards=cards,
        count=len(cards),
        asset_name=asset_name,
    )


# ══════════════════════════════════════════════════════════════════
# Tool 6：fetch_realtime_research（动态）
# ══════════════════════════════════════════════════════════════════

class FetchRealtimeInput(BaseModel):
    asset_name: str

class FetchRealtimeOutput(BaseModel):
    results: list[str]
    count: int
    asset_name: str
    source: str = "perplexity"

FETCH_REALTIME_SCHEMA = {
    "name": "fetch_realtime_research",
    "description": "联网搜索指定标的的最新投资研究信息（新闻、研报摘要、分析师观点）。"
                   "当本地投研卡片不足或过期时调用。有 30 分钟缓存，避免重复搜索。",
    "parameters": {
        "type": "object",
        "properties": {
            "asset_name": {
                "type": "string",
                "description": "标的名称，如'贵州茅台'、'微软'",
            },
        },
        "required": ["asset_name"],
    },
}

def execute_fetch_realtime(asset_name: str) -> FetchRealtimeOutput:
    from decision_engine.data_loader import _search_research_online
    try:
        results = _search_research_online(asset_name)
    except Exception:
        results = []
    return FetchRealtimeOutput(
        results=results or [],
        count=len(results or []),
        asset_name=asset_name,
    )


# ══════════════════════════════════════════════════════════════════
# Tool 7：web_search（动态）
# ══════════════════════════════════════════════════════════════════
# 注：项目没有通用 web_search，复用投资领域专化的联网搜索。
# 面试叙事：这是金融场景下的领域专化搜索，比通用搜索更精准。

class WebSearchInput(BaseModel):
    query: str              # 搜索关键词（会被包装成投资研究 prompt）
    asset_name: str = ""    # 可选，标的名称（有则用单标的搜索）

class WebSearchOutput(BaseModel):
    results: list[str]
    count: int
    query: str

WEB_SEARCH_SCHEMA = {
    "name": "web_search",
    "description": "联网搜索补充投资研究证据。当决策置信度低、需要最新市场信息时调用。"
                   "搜索结果是投资领域专化的（新闻、研报、分析师观点），非通用网页。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，如'茅台最新财报'、'新能源板块政策'",
            },
            "asset_name": {
                "type": "string",
                "description": "可选。若有明确标的，传入标的名称以获得更精准的搜索结果",
                "default": "",
            },
        },
        "required": ["query"],
    },
}

def execute_web_search(query: str, asset_name: str = "") -> WebSearchOutput:
    from decision_engine.data_loader import _search_research_online
    target = asset_name or query
    try:
        results = _search_research_online(target)
    except Exception:
        results = []
    return WebSearchOutput(
        results=results or [],
        count=len(results or []),
        query=query,
    )


# ══════════════════════════════════════════════════════════════════
# Tool 8：generate_signals（静态，v3.0 补充）
# ══════════════════════════════════════════════════════════════════

class GenerateSignalsInput(BaseModel):
    asset_name: str
    portfolio_id: int = 1
    action_type: str = "持有评估"

class GenerateSignalsOutput(BaseModel):
    asset_name: str
    position_signal: str        # 偏高 / 合理 / 偏低
    fundamental_signal: str     # 正面 / 中性 / 负面 / N/A
    sentiment_signal: str       # 中性
    event_uncertainty: str
    event_direction: str
    error: Optional[str] = None

GENERATE_SIGNALS_SCHEMA = {
    "name": "generate_signals",
    "description": "生成 4 维度市场信号（仓位/基本面/事件/情绪），作为 LLM 推理的环境因子。"
                   "内部自动加载持仓数据和纪律校验结果。",
    "parameters": {
        "type": "object",
        "properties": {
            "asset_name": {
                "type": "string",
                "description": "标的名称，如'贵州茅台'",
            },
            "portfolio_id": {
                "type": "integer",
                "description": "Portfolio ID，默认为 1",
                "default": 1,
            },
            "action_type": {
                "type": "string",
                "description": "操作类型：持有评估 / 加仓判断 / 减仓判断 / 买入判断 / 卖出判断",
                "default": "持有评估",
            },
        },
        "required": ["asset_name"],
    },
}

def execute_generate_signals(
    asset_name: str,
    portfolio_id: int = 1,
    action_type: str = "持有评估",
) -> GenerateSignalsOutput:
    """生成 4 维度市场信号。内部组装 LoadedData + IntentResult + RuleResult。"""
    try:
        from decision_engine import data_loader, rule_engine, signal_engine
        from decision_engine.types import IntentResult

        loaded = data_loader.load(asset_name=asset_name, pid=portfolio_id)

        intent = IntentResult(
            asset=asset_name,
            action_type=action_type,
            time_horizon="未知",
            trigger=None,
            confidence_score=1.0,
        )

        rule_result = rule_engine.check(loaded, intent)
        signal_result = signal_engine.generate(loaded, intent, rule_result)

        return GenerateSignalsOutput(
            asset_name=asset_name,
            position_signal=signal_result.position_signal,
            fundamental_signal=signal_result.fundamental_signal,
            sentiment_signal=signal_result.sentiment_signal,
            event_uncertainty=signal_result.event_signal.uncertainty if signal_result.event_signal else "未知",
            event_direction=signal_result.event_signal.direction if signal_result.event_signal else "未知",
        )

    except Exception as e:
        return GenerateSignalsOutput(
            asset_name=asset_name,
            position_signal="未知",
            fundamental_signal="N/A",
            sentiment_signal="中性",
            event_uncertainty="未知",
            event_direction="未知",
            error=str(e),
        )


# ══════════════════════════════════════════════════════════════════
# Tool 9：load_decision_context（组合 Skill，v3.0）
# ══════════════════════════════════════════════════════════════════

class LoadDecisionContextOutput(BaseModel):
    """load_decision_context Tool 的结构化输出。"""
    loaded_data: Optional[object] = None   # decision_engine.data_loader.LoadedData
    has_required_data: bool = False
    has_data_errors: bool = True
    has_ambiguous_matches: bool = False
    target_position_found: bool = False
    error: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True

LOAD_DECISION_CONTEXT_SCHEMA = {
    "name": "load_decision_context",
    "description": "加载完整决策上下文（组合 Skill）。封装持仓+画像+纪律+target查找+投研(含M7)+告警。",
    "parameters": {
        "type": "object",
        "properties": {
            "asset_name": {
                "type": "string",
                "description": "标的名称（可选，组合级评估时为空）",
                "default": "",
            },
            "portfolio_id": {
                "type": "integer",
                "default": 1,
            },
            "user_query": {
                "type": "string",
                "description": "用户原始问句（M7 三层判别需要）",
                "default": "",
            },
        },
        "required": [],
    },
}

def execute_load_decision_context(
    asset_name: Optional[str] = None,
    portfolio_id: int = 1,
    user_query: str = "",
) -> LoadDecisionContextOutput:
    """加载完整决策上下文。内部调用 data_loader.load()。"""
    try:
        from decision_engine import data_loader

        loaded = data_loader.load(
            asset_name=asset_name or None,
            pid=portfolio_id,
            user_query=user_query,
        )

        return LoadDecisionContextOutput(
            loaded_data=loaded,
            has_required_data=loaded.has_required_data,
            has_data_errors=loaded.has_data_errors,
            has_ambiguous_matches=bool(loaded.ambiguous_matches),
            target_position_found=loaded.target_position is not None,
        )

    except Exception as e:
        return LoadDecisionContextOutput(
            loaded_data=None,
            has_required_data=False,
            has_data_errors=True,
            has_ambiguous_matches=False,
            target_position_found=False,
            error=str(e),
        )


# ══════════════════════════════════════════════════════════════════
# 盈米 MCP Tools（v2.6 M7）
# ══════════════════════════════════════════════════════════════════
# 6 个 v2.1 范围的工具，通过 MCP 协议调用盈米基金数据平台
# 设计原则：薄包装，把 MCP 返回的 raw_text 透传给上层，
#          让 LLM 直接消费盈米的结构化文本输出
# ══════════════════════════════════════════════════════════════════

from backend.mcp_client import call_yingmi_tool


# ─── Tool 8：fetch_fund_detail ─────────────────────────────────────

class FetchFundDetailInput(BaseModel):
    fund_codes: list[str]

class FetchFundDetailOutput(BaseModel):
    success: bool
    raw_text: str
    fund_count: int
    error: Optional[str] = None

FETCH_FUND_DETAIL_SCHEMA = {
    "name": "fetch_fund_detail",
    "description": "获取基金的基础资料：名称、类型、风险等级、规模、基金经理、近期业绩等。"
                   "用于用户提到具体基金代码或名称时，作为分析的起点。"
                   "数据源：盈米 MCP。最多支持 20 只基金一次查询。",
    "parameters": {
        "type": "object",
        "properties": {
            "fund_codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "6 位基金代码列表，如 ['000001', '110011']",
            }
        },
        "required": ["fund_codes"],
    },
}

def execute_fetch_fund_detail(fund_codes: list[str]) -> FetchFundDetailOutput:
    result = call_yingmi_tool("BatchGetFundsDetail", {"fundCodes": fund_codes})
    return FetchFundDetailOutput(
        success=result.get("success", False),
        raw_text=result.get("raw_text", "")[:5000],
        fund_count=len(fund_codes),
        error=result.get("error"),
    )


# ─── Tool 9：fetch_fund_nav_history ────────────────────────────────

class FetchFundNavHistoryInput(BaseModel):
    fund_codes: list[str]
    is_desc: bool = True
    dimension_type: str = "DAILY"

class FetchFundNavHistoryOutput(BaseModel):
    success: bool
    raw_text: str
    error: Optional[str] = None

FETCH_FUND_NAV_HISTORY_SCHEMA = {
    "name": "fetch_fund_nav_history",
    "description": "获取基金的历史净值序列。用于绘制净值曲线、计算最大回撤、分析波动率等场景。"
                   "数据源：盈米 MCP。",
    "parameters": {
        "type": "object",
        "properties": {
            "fund_codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "6 位基金代码列表",
            },
            "is_desc": {
                "type": "boolean",
                "description": "是否按时间倒序，默认 True",
                "default": True,
            },
            "dimension_type": {
                "type": "string",
                "description": "DAILY / WEEKLY / MONTHLY，默认 DAILY",
                "default": "DAILY",
            },
        },
        "required": ["fund_codes"],
    },
}

def execute_fetch_fund_nav_history(
    fund_codes: list[str],
    is_desc: bool = True,
    dimension_type: str = "DAILY",
) -> FetchFundNavHistoryOutput:
    result = call_yingmi_tool("BatchGetFundNavHistory", {
        "fundCodes": fund_codes,
        "isDesc": is_desc,
        "dimensionType": dimension_type,
    })
    return FetchFundNavHistoryOutput(
        success=result.get("success", False),
        raw_text=result.get("raw_text", "")[:5000],
        error=result.get("error"),
    )


# ─── Tool 10：fetch_fund_performance ───────────────────────────────

class FetchFundPerformanceInput(BaseModel):
    fund_codes: list[str]

class FetchFundPerformanceOutput(BaseModel):
    success: bool
    raw_text: str
    error: Optional[str] = None

FETCH_FUND_PERFORMANCE_SCHEMA = {
    "name": "fetch_fund_performance",
    "description": "获取基金的多时段业绩指标：收益率（近1月/3月/1年/3年/成立以来）、波动率、夏普比率等。"
                   "用于业绩归因、对比分析。数据源：盈米 MCP。",
    "parameters": {
        "type": "object",
        "properties": {
            "fund_codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "6 位基金代码列表",
            }
        },
        "required": ["fund_codes"],
    },
}

def execute_fetch_fund_performance(fund_codes: list[str]) -> FetchFundPerformanceOutput:
    result = call_yingmi_tool("GetBatchFundPerformance", {"fundCodes": fund_codes})
    return FetchFundPerformanceOutput(
        success=result.get("success", False),
        raw_text=result.get("raw_text", "")[:5000],
        error=result.get("error"),
    )


# ─── Tool 11：diagnose_fund ────────────────────────────────────────

class DiagnoseFundInput(BaseModel):
    fund_name_or_code: str

class DiagnoseFundOutput(BaseModel):
    success: bool
    raw_text: str
    error: Optional[str] = None

DIAGNOSE_FUND_SCHEMA = {
    "name": "diagnose_fund",
    "description": "对单只基金进行全维度诊断：业绩、风险、持仓集中度、基金经理、规模变化、同类排名等。"
                   "WealthPilot 基金侧最强工具，等同于专业基金分析师的 1 页诊断报告。"
                   "数据源：盈米 MCP。支持基金代码或基金名称作为输入。",
    "parameters": {
        "type": "object",
        "properties": {
            "fund_name_or_code": {
                "type": "string",
                "description": "基金名称或 6 位代码，如 '华夏成长' 或 '000001'",
            }
        },
        "required": ["fund_name_or_code"],
    },
}

def execute_diagnose_fund(fund_name_or_code: str) -> DiagnoseFundOutput:
    result = call_yingmi_tool("GetFundDiagnosis", {
        "fundNameOrCode": fund_name_or_code,
    })
    return DiagnoseFundOutput(
        success=result.get("success", False),
        raw_text=result.get("raw_text", "")[:8000],
        error=result.get("error"),
    )


# ─── Tool 12：search_financial_news ────────────────────────────────

class SearchFinancialNewsInput(BaseModel):
    keyword: str
    page: int = 1
    page_size: int = 10

class SearchFinancialNewsOutput(BaseModel):
    success: bool
    raw_text: str
    error: Optional[str] = None

SEARCH_FINANCIAL_NEWS_SCHEMA = {
    "name": "search_financial_news",
    "description": "搜索财经资讯。用于在用户问及具体行业、板块、热点事件时补充时事背景。"
                   "数据源：盈米 MCP。",
    "parameters": {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词，如 '半导体'、'美联储加息'",
            },
            "page": {"type": "integer", "default": 1},
            "page_size": {"type": "integer", "default": 10},
        },
        "required": ["keyword"],
    },
}

def execute_search_financial_news(
    keyword: str, page: int = 1, page_size: int = 10
) -> SearchFinancialNewsOutput:
    result = call_yingmi_tool("SearchFinancialNews", {
        "keyword": keyword,
        "page": page,
        "pageSize": page_size,
    })
    return SearchFinancialNewsOutput(
        success=result.get("success", False),
        raw_text=result.get("raw_text", "")[:5000],
        error=result.get("error"),
    )


# ─── Tool 13：get_latest_quotations ────────────────────────────────

class GetLatestQuotationsInput(BaseModel):
    cal_date: str = ""

class GetLatestQuotationsOutput(BaseModel):
    success: bool
    raw_text: str
    error: Optional[str] = None

GET_LATEST_QUOTATIONS_SCHEMA = {
    "name": "get_latest_quotations",
    "description": "获取市场最新行情：A股/港股/美股主要指数、基金市场温度、市场情绪指标等。"
                   "用于决策前对市场环境做整体感知。数据源：盈米 MCP。",
    "parameters": {
        "type": "object",
        "properties": {
            "cal_date": {
                "type": "string",
                "description": "可选。指定交易日，格式 YYYY-MM-DD。不传则取最新交易日。",
                "default": "",
            }
        },
        "required": [],
    },
}

def execute_get_latest_quotations(cal_date: str = "") -> GetLatestQuotationsOutput:
    args = {}
    if cal_date:
        args["calDate"] = cal_date
    result = call_yingmi_tool("GetLatestQuotations", args)
    return GetLatestQuotationsOutput(
        success=result.get("success", False),
        raw_text=result.get("raw_text", "")[:5000],
        error=result.get("error"),
    )


# ══════════════════════════════════════════════════════════════════
# v3.6: wp-retrieve-principles Tool
# ══════════════════════════════════════════════════════════════════

def execute_retrieve_principles(
    query: str = "",
    source_types: list[str] | None = None,
    top_k: int = 5,
) -> dict:
    """v3.6: wp-retrieve-principles Tool — 从知识库检索用户原则类知识。"""
    import logging as _rp_log
    _rp_logger = _rp_log.getLogger("tools.retrieve_principles")

    if not query or not query.strip():
        return {"chunks": [], "total_retrieved": 0}

    if source_types is None:
        source_types = [
            "investment_principles",
            "investment_style",
            "allocation_principles",
        ]

    try:
        from backend.knowledge.store import KnowledgeStore
        store = KnowledgeStore.get_instance()
        if not store.is_ready():
            _rp_logger.warning("KnowledgeStore 未就绪，返回空结果")
            return {"chunks": [], "total_retrieved": 0, "warning": "knowledge_store_not_ready"}

        _decay_on = store._config.get("decay", {}).get("enabled", False)
        chunks = store.retrieve(
            query=query,
            source_types=source_types,
            top_k=top_k,
            apply_decay=_decay_on,
        )
        for c in chunks:
            c.source_channel = "local_principles"

        return {
            "chunks": [c.model_dump() for c in chunks],
            "total_retrieved": len(chunks),
        }
    except Exception as e:
        _rp_logger.exception("retrieve_principles 调用失败")
        return {"chunks": [], "total_retrieved": 0, "error": str(e)}


RETRIEVE_PRINCIPLES_SCHEMA = {
    "name": "retrieve_principles",
    "description": "从知识库检索用户原则类知识（投资纪律、投资理念、资产配置原则）。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索 query",
            },
            "source_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "知识类型过滤",
            },
            "top_k": {
                "type": "integer",
                "description": "返回结果数量",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}


# ══════════════════════════════════════════════════════════════════
# wp-generate-execution-plan (v3.11)
# ══════════════════════════════════════════════════════════════════


def execute_generate_execution_plan(**kwargs) -> dict:
    """确定性 orchestrator: factors → rule_engine → LLM(仅解释) → [validator 留接口]。

    所有数字由 rule_engine 产出,LLM 只写 rationale/risk_notes。
    """
    import json
    import logging
    from backend.services.execution_plan.factors import build_factor_snapshot
    from backend.services.execution_plan.rule_engine import generate_plan, PlanInput

    _logger = logging.getLogger("wp-generate-execution-plan")

    symbol = kwargs["symbol"]
    market = kwargs["market"]
    side = kwargs["side"]

    _logger.info("[orchestrator] Step 1: factors for %s", symbol)

    # ── Step 1: 因子 ──
    factor_snap = build_factor_snapshot(symbol, market)
    factor_dict = factor_snap.to_dict()

    _logger.info("[orchestrator] Step 2: rule_engine for %s", symbol)

    # ── Step 2: 规则引擎(数字在此定死) ──
    plan_input = PlanInput(
        symbol=symbol,
        market=market,
        side=side,
        target_position_pct=kwargs.get("target_position_pct", 0.0),
        current_position_pct=kwargs.get("current_position_pct", 0.0),
        current_price=factor_snap.current_price or kwargs.get("current_price", 0.0),
        total_assets=kwargs.get("total_assets", 0.0),
        user_anchor_prices=kwargs.get("user_anchor_prices") or [],
        quick_mode=kwargs.get("quick_mode", False),
        batch_count_override=kwargs.get("batch_count_override"),
        atr14=factor_snap.atr14,
        volatility_annual=factor_snap.volatility_annual,
        price_percentile=factor_snap.price_percentile,
        drawdown_from_high=factor_snap.drawdown_from_high,
    )
    plan_result = generate_plan(plan_input, factor_dict)

    # ── 全因子降级拒绝 ──
    if plan_result.insufficient_data:
        _logger.warning("[orchestrator] 数据不足,拒绝生成: %s", plan_result.insufficient_data_reason)
        return {
            "insufficient_data": True,
            "insufficient_data_reason": plan_result.insufficient_data_reason,
            "factor_snapshot": factor_dict,
        }

    _logger.info("[orchestrator] Step 3: LLM rationale for %s", symbol)

    # ── Step 3: LLM 只写解释文案 ──
    rationale, risk_notes = _llm_write_rationale(
        symbol=symbol,
        side=side,
        plan_summary=plan_result.plan_summary_block,
        factor_snapshot=factor_dict,
        constraints=plan_result.constraints_applied,
        warnings=plan_result.warnings,
        violations=plan_result.violations,
    )

    _logger.info("[orchestrator] Step 4: validator for %s", symbol)

    # ── Step 4: validator — 结构化比对(hard) + 文案白名单(soft) ──
    from backend.graph.decision_validator import validate_execution_plan
    import copy

    # plan_dict_frozen: 规则引擎原始产出的冻结副本(LLM 之前锁定)
    plan_dict_frozen = copy.deepcopy(plan_result.plan_summary_block)

    validation = validate_execution_plan(
        plan_dict=plan_result.plan_summary_block,
        llm_rationale=rationale,
        llm_risk_notes=risk_notes,
        factor_snapshot=factor_dict,
        plan_dict_frozen=plan_dict_frozen,
    )

    # soft warnings 记录但不阻断
    soft_warnings = [f.message for f in validation.failures if f.severity == "soft"]
    if soft_warnings:
        _logger.info("[orchestrator] validator soft warnings: %s", soft_warnings)

    if not validation.passed:
        failure_msgs = [f.message for f in validation.failures if f.severity == "hard"]
        _logger.warning("[orchestrator] validator 拦截: %s", failure_msgs)
        return {
            "error": "plan_value_mismatch",
            "validation_failures": failure_msgs,
            "plan_summary_block": plan_result.plan_summary_block,
        }

    _logger.info("[orchestrator] validator 通过")

    return {
        "plan_summary_block": plan_result.plan_summary_block,
        "factor_snapshot": factor_dict,
        "constraints_applied": plan_result.constraints_applied,
        "rationale": rationale,
        "risk_notes": risk_notes,
        "warnings": plan_result.warnings + soft_warnings,
        "violations": plan_result.violations,
        "tick_degraded": plan_result.tick_degraded,
        "source_decision_ref": kwargs.get("source_decision_ref", ""),
    }


def _llm_write_rationale(
    symbol: str,
    side: str,
    plan_summary: dict,
    factor_snapshot: dict,
    constraints: dict,
    warnings: list,
    violations: list,
) -> tuple[str, str]:
    """调 LLM 写 rationale + risk_notes。

    LLM 只接收已定死的数字作为入参,只输出文本,不返回任何数字字段。
    """
    import json
    import os
    import logging

    _logger = logging.getLogger("wp-generate-execution-plan")

    factor_subset = {
        "current_price": factor_snapshot.get("current_price"),
        "volatility_annual": factor_snapshot.get("volatility_annual"),
        "price_percentile": factor_snapshot.get("price_percentile"),
        "drawdown_from_high": factor_snapshot.get("drawdown_from_high"),
        "trend_signal": factor_snapshot.get("trend_signal"),
        "rsi14": factor_snapshot.get("rsi14"),
        "ma_position": factor_snapshot.get("ma_position"),
    }
    plan_json = json.dumps(plan_summary, indent=2, ensure_ascii=False, default=str)
    factor_json = json.dumps(factor_subset, indent=2, ensure_ascii=False, default=str)
    constraint_info = f"纪律违规修正: {violations}" if violations else "无纪律违规"
    warning_info = f"警告: {warnings}" if warnings else "无警告"

    prompt = f"""你是 WealthPilot 执行计划解释器。你的任务是为一份已确定的分批执行计划写解释和风险提示。

## 严格禁止
- 绝不输出任何价格、数量、批次数字。所有数字已由规则引擎确定，你无权修改或新增。
- 绝不编造计划之外的任何数值。
- 如需引用波动率、回撤、分位等数据，必须取自下方的因子快照，不得自行计算或估算。

## 已确定的执行计划
```json
{plan_json}
```

## 因子快照
```json
{factor_json}
```

## 约束信息
{constraint_info}
{warning_info}

## 输出要求
请输出**且仅输出**以下 JSON,不要包含 markdown 代码块标记:
{{
  "rationale": "一段 100-200 字的中文解释,说明为什么这样分批(引用因子快照中的趋势/波动率/分位等,但不重复计划里的具体数字)",
  "risk_notes": "一段 50-100 字的风险提示(如波动较高需关注、接近低点但趋势偏弱等)"
}}
"""

    try:
        from openai import OpenAI
        import httpx

        client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            http_client=httpx.Client(trust_env=False, timeout=httpx.Timeout(30.0)),
        )
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        raw = resp.choices[0].message.content.strip()

        # 去 markdown 代码块
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        parsed = json.loads(raw)
        return parsed.get("rationale", ""), parsed.get("risk_notes", "")

    except Exception as e:
        _logger.warning("[orchestrator] LLM rationale 失败,降级为空: %s", e)
        return (
            f"执行计划已由规则引擎生成。标的 {symbol} 的 {side} 计划包含分批操作。",
            "LLM 解释生成失败,请人工审阅计划数字。",
        )


# ══════════════════════════════════════════════════════════════════
# Tool 注册表（统一入口）
# ══════════════════════════════════════════════════════════════════

TOOL_SCHEMAS = [
    FETCH_HOLDINGS_SCHEMA,
    CHECK_DISCIPLINE_SCHEMA,
    CALC_DEVIATION_SCHEMA,
    PROPOSE_INCREMENT_SCHEMA,
    QUERY_VIEWPOINT_SCHEMA,
    FETCH_REALTIME_SCHEMA,
    WEB_SEARCH_SCHEMA,
    GENERATE_SIGNALS_SCHEMA,
    LOAD_DECISION_CONTEXT_SCHEMA,
    FETCH_FUND_DETAIL_SCHEMA,
    FETCH_FUND_NAV_HISTORY_SCHEMA,
    FETCH_FUND_PERFORMANCE_SCHEMA,
    DIAGNOSE_FUND_SCHEMA,
    SEARCH_FINANCIAL_NEWS_SCHEMA,
    GET_LATEST_QUOTATIONS_SCHEMA,
    RETRIEVE_PRINCIPLES_SCHEMA,
]

TOOL_EXECUTORS = {
    "fetch_holdings":             execute_fetch_holdings,
    "check_discipline_rules":     execute_check_discipline,
    "calc_allocation_deviation":  execute_calc_deviation,
    "propose_increment_plan":     execute_propose_increment,
    "query_viewpoint_cards":      execute_query_viewpoint,
    "fetch_realtime_research":    execute_fetch_realtime,
    "web_search":                 execute_web_search,
    "generate_signals":           execute_generate_signals,
    "load_decision_context":      execute_load_decision_context,
    "fetch_fund_detail":          execute_fetch_fund_detail,
    "fetch_fund_nav_history":     execute_fetch_fund_nav_history,
    "fetch_fund_performance":     execute_fetch_fund_performance,
    "diagnose_fund":              execute_diagnose_fund,
    "search_financial_news":      execute_search_financial_news,
    "get_latest_quotations":      execute_get_latest_quotations,
    "retrieve_principles":        execute_retrieve_principles,
    "generate_execution_plan":    execute_generate_execution_plan,
}


def call_tool(tool_name: str, **kwargs) -> Any:
    """统一 Tool 调用入口。LLM Function Calling 的结果直接传入。"""
    executor = TOOL_EXECUTORS.get(tool_name)
    if not executor:
        raise ValueError(f"未知 Tool: {tool_name}。可用 Tool: {list(TOOL_EXECUTORS.keys())}")
    return executor(**kwargs)
