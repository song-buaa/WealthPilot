"""
意图体系数据结构定义

对应工程PRD §2（数据结构定义）。
将 TypeScript 接口翻译为 Python dataclass，所有字段语义与 PRD 保持一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── PRD §2.1 枚举定义 ─────────────────────────────────────────────────────────

VALID_INTENTS: set = {
    "PortfolioReview",
    "AssetAllocation",
    "PositionDecision",
    "PerformanceAnalysis",
    "Education",
}

TRADE_ACTIONS: set = {"BUY", "SELL", "ADD", "REDUCE", "REBALANCE", "TAKE_PROFIT", "STOP_LOSS"}
INFO_ACTIONS: set = {"ANALYZE", "VIEW_PERFORMANCE", "GET_REPORT", "SET_ALERT"}
VALID_ACTIONS: set = TRADE_ACTIONS | INFO_ACTIONS

# 各 Intent 对应的合法 Subtask（PRD §2.1 SubtaskType）
INTENT_SUBTASK_MAP: Dict[str, set] = {
    "PortfolioReview":     {"review", "risk_check", "concentration_check", "rebalance_check"},
    "AssetAllocation":     {"new_cash_allocation", "rebalance_allocation", "goal_based_allocation"},
    "PositionDecision":    {"thesis_review", "position_fit_check", "action_evaluation"},
    "PerformanceAnalysis": {"pnl_breakdown", "loss_reason", "attribution"},
    "Education":           {"concept_explain", "rule_explain"},
}


# ── PRD §2.1 IntentPayload ────────────────────────────────────────────────────

@dataclass
class IntentEntities:
    """实体字段（PRD §2.1 entities 结构）"""
    asset: Optional[str] = None               # 标的名称（原始文本，如"理想汽车"）
    asset_normalized: Optional[str] = None   # 标准化后的股票代码（TODO Phase 3: SymbolSearchAPI）
    capital: Optional[str] = None            # 资金规模原始文本（如"20万"）
    capital_amount: Optional[float] = None   # 标准化数值（单位：元，本地解析）
    portfolio_id: Optional[str] = None       # 组合ID（已登录用户）
    time_horizon: Optional[str] = None       # 投资期限
    multi_assets: List[str] = field(default_factory=list)  # 多标的同操作时填写（单标时为空）


@dataclass
class IntentPayload:
    """
    意图识别输出结构（PRD §2.1）

    由 IntentRecognizer 生成，校验通过后传入 ContextManager。
    """
    primary_intent: str                       # 主意图（唯一）
    secondary_intents: List[str]              # 次意图（最多2个，Phase 1 暂不执行）
    subtasks: List[str]                       # 激活的子任务列表
    actions: List[str]                        # 行为信号（可多个）
    entities: IntentEntities
    confidence: float                         # 0~1，识别置信度


# ── PRD §2.2 ExecutionContext ─────────────────────────────────────────────────

@dataclass
class Turn:
    """单轮对话摘要（PRD §2.2 Turn）"""
    turn_index: int
    intent: str
    entities_snapshot: Dict[str, str]
    summary: str                              # 本轮对话摘要，注入后续 prompt


@dataclass
class InheritedFields:
    """从历史上下文继承的字段（PRD §2.2 inherited_fields）"""
    asset: Optional[str] = None
    asset_normalized: Optional[str] = None
    capital: Optional[float] = None
    portfolio_id: Optional[str] = None
    risk_level: Optional[str] = None          # "低" | "中" | "高"
    goal: Optional[str] = None
    time_horizon: Optional[str] = None


@dataclass
class UserProfile:
    """长期用户画像（PRD §2.2 user_profile）"""
    risk_level: str = "中"                    # "低" | "中" | "高"
    goal: str = "长期增值"
    verified: bool = True                     # KYC 是否通过（Phase 1 默认通过）


@dataclass
class ExecutionContext:
    """执行上下文（PRD §2.2），由 ContextManager 生成，注入 Orchestrator"""
    session_id: str
    turn_index: int
    intent_payload: IntentPayload
    inherited_fields: InheritedFields
    user_profile: UserProfile
    conversation_history: List[Turn] = field(default_factory=list)


# ── PRD §2.3 ExecutionPlan ────────────────────────────────────────────────────

@dataclass
class DataRequirement:
    """单个 Subtask 需要拉取的数据规格（PRD §2.3）"""
    type: str                                 # "market_data" | "portfolio_data" | "user_profile" | "news"
    params: Dict[str, str] = field(default_factory=dict)


@dataclass
class SubtaskExecution:
    """单个 Subtask 的执行描述（PRD §2.3）"""
    subtask: str
    intent_source: str                        # "primary" | "secondary"
    execution_depth: str                      # "full" | "summary"
    depends_on: List[str] = field(default_factory=list)
    data_requirements: List[DataRequirement] = field(default_factory=list)


@dataclass
class ExecutionPlan:
    """Orchestrator 生成的执行计划（PRD §2.3）"""
    primary_flow: List[SubtaskExecution]
    secondary_flow: List[SubtaskExecution]    # Phase 1: 始终为空
    execution_mode: str                       # "sequential" | "parallel"


# ── PRD §2.4 SubtaskResult ────────────────────────────────────────────────────

@dataclass
class SubtaskResult:
    """单个 Subtask 执行结果（PRD §2.4）"""
    subtask: str
    status: str                               # "success" | "failed" | "skipped"
    content: str                              # LLM 输出的分析文本（或跳过/失败说明）
    structured_data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
