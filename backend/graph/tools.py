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
]

TOOL_EXECUTORS = {
    "fetch_holdings":             execute_fetch_holdings,
    "check_discipline_rules":     execute_check_discipline,
    "calc_allocation_deviation":  execute_calc_deviation,
    "propose_increment_plan":     execute_propose_increment,
    "query_viewpoint_cards":      execute_query_viewpoint,
    "fetch_realtime_research":    execute_fetch_realtime,
    "web_search":                 execute_web_search,
}


def call_tool(tool_name: str, **kwargs) -> Any:
    """统一 Tool 调用入口。LLM Function Calling 的结果直接传入。"""
    executor = TOOL_EXECUTORS.get(tool_name)
    if not executor:
        raise ValueError(f"未知 Tool: {tool_name}。可用 Tool: {list(TOOL_EXECUTORS.keys())}")
    return executor(**kwargs)
