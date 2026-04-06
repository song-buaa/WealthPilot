"""
资产配置模块 — 数据类型定义

包含：枚举、Pydantic 模型、中英文映射
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ── 资产大类枚举 ──────────────────────────────────────────

class AllocAssetClass(str, enum.Enum):
    """资产配置模块使用的英文枚举，与 DB 中的中文枚举双向映射"""
    CASH = "cash"
    FIXED = "fixed"
    EQUITY = "equity"
    ALT = "alt"
    DERIV = "deriv"
    UNCLASSIFIED = "unclassified"


# 中文 → 英文
CN_TO_ALLOC: Dict[str, AllocAssetClass] = {
    "货币": AllocAssetClass.CASH,
    "固收": AllocAssetClass.FIXED,
    "权益": AllocAssetClass.EQUITY,
    "另类": AllocAssetClass.ALT,
    "衍生": AllocAssetClass.DERIV,
}

# 英文 → 中文
ALLOC_TO_CN: Dict[AllocAssetClass, str] = {v: k for k, v in CN_TO_ALLOC.items()}

# 英文 → 中文 label（用于 AI 输出）
ALLOC_LABEL: Dict[AllocAssetClass, str] = {
    AllocAssetClass.CASH: "货币",
    AllocAssetClass.FIXED: "固收",
    AllocAssetClass.EQUITY: "权益",
    AllocAssetClass.ALT: "另类",
    AllocAssetClass.DERIV: "衍生",
    AllocAssetClass.UNCLASSIFIED: "未分类",
}

# 五大类（不含 unclassified）
FIVE_CLASSES = [
    AllocAssetClass.CASH,
    AllocAssetClass.FIXED,
    AllocAssetClass.EQUITY,
    AllocAssetClass.ALT,
    AllocAssetClass.DERIV,
]

# 四类（不含货币和 unclassified，用于条形图）
FOUR_NON_CASH = [
    AllocAssetClass.FIXED,
    AllocAssetClass.EQUITY,
    AllocAssetClass.ALT,
    AllocAssetClass.DERIV,
]


# ── 偏离等级 & 状态枚举 ──────────────────────────────────

class DeviationLevel(str, enum.Enum):
    NORMAL = "normal"
    MILD = "mild"
    SIGNIFICANT = "significant"
    ALERT = "alert"


class CashStatus(str, enum.Enum):
    SUFFICIENT = "sufficient"
    LOW = "low"
    INSUFFICIENT = "insufficient"


class OverallStatus(str, enum.Enum):
    ON_TARGET = "on_target"
    MILD_DEVIATION = "mild_deviation"
    SIGNIFICANT_DEVIATION = "significant_deviation"
    ALERT = "alert"


class PriorityAction(str, enum.Enum):
    NO_ACTION = "no_action"
    CORRECT_WITH_INFLOW = "correct_with_inflow"
    URGENT_ATTENTION = "urgent_attention"


class ViolationSeverity(str, enum.Enum):
    WARNING = "warning"
    BLOCK = "block"


class AllocationIntentType(str, enum.Enum):
    INITIAL_ALLOCATION = "INITIAL_ALLOCATION"
    INCREMENT_ALLOCATION = "INCREMENT_ALLOCATION"
    DIAGNOSIS = "DIAGNOSIS"
    REBALANCE_DIRECTION = "REBALANCE_DIRECTION"  # P1
    EXPLAIN = "EXPLAIN"
    CONCEPT = "CONCEPT"


# ── Pydantic 模型 ────────────────────────────────────────

class AssetTarget(BaseModel):
    asset_class: AllocAssetClass
    # 货币类
    cash_min_amount: Optional[float] = None   # 绝对金额下限（元）
    cash_max_amount: Optional[float] = None   # 绝对金额上限（元），V1 仅展示
    # 非货币四类
    floor_ratio: Optional[float] = None       # 占比下限（0-1），衍生类此字段为 None
    ceiling_ratio: float                       # 占比上限（0-1），所有类均有
    mid_ratio: Optional[float] = None         # 目标中值 = (floor + ceiling) / 2


class ClassAllocation(BaseModel):
    amount: float = 0.0
    ratio: float = 0.0


class AllocationSnapshot(BaseModel):
    snapshot_at: datetime = Field(default_factory=datetime.now)
    total_investable_assets: float = 0.0
    by_class: Dict[str, ClassAllocation] = {}   # key = AllocAssetClass.value
    unclassified_amount: float = 0.0
    has_unclassified: bool = False


class ClassDeviation(BaseModel):
    current_ratio: float
    target_mid: float
    deviation: float         # current - target_mid
    is_above_floor: bool
    is_below_ceiling: bool
    is_in_range: bool
    deviation_level: DeviationLevel


class CashDeviation(BaseModel):
    current_amount: float
    min_amount: float
    max_amount: float
    status: CashStatus


class DeviationSnapshot(BaseModel):
    by_class: Dict[str, ClassDeviation] = {}   # key = fixed/equity/alt/deriv
    cash: CashDeviation
    overall_status: OverallStatus
    priority_action: PriorityAction


class DisciplineViolation(BaseModel):
    type: str
    message: str
    severity: ViolationSeverity


class DisciplineCheckResult(BaseModel):
    passed: bool
    violations: List[DisciplineViolation] = []


class AllocationPlanItem(BaseModel):
    asset_class: str
    label: str
    current_ratio: float = 0.0
    target_mid: float = 0.0
    deviation: float = 0.0
    suggested_amount: float = 0.0
    suggested_ratio: float = 0.0
    candidates: List[str] = []


class AllocationResult(BaseModel):
    """增量/初始分配结果"""
    total_amount: float
    allocations: Dict[str, float] = {}   # key = AllocAssetClass.value, value = 金额
    plan_items: List[AllocationPlanItem] = []
    discipline_check: Optional[DisciplineCheckResult] = None


# ── 会话上下文 ────────────────────────────────────────────

class SessionContext(BaseModel):
    confirmed_increment_amount: Optional[float] = None
    confirmed_replanning: Optional[bool] = None
    user_requested_deriv: Optional[bool] = None


class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class AllocationChatRequest(BaseModel):
    message: str
    conversation_history: List[ChatMessage] = []
    context: Optional[Dict] = None
    session_context: Optional[SessionContext] = None


class ExplainPanelData(BaseModel):
    tools_called: List[str] = []
    key_data: Dict[str, object] = {}
    reasoning: str = ""


class AllocationAIResponse(BaseModel):
    """AI 输出的配置方案 / 诊断结果"""
    # 四段式（方案生成类）
    diagnosis: Optional[str] = None
    logic: Optional[str] = None
    plan: Optional[Dict] = None
    risk_note: Optional[str] = None
    # 三段式（诊断类）
    status_conclusion: Optional[str] = None
    deviation_detail: Optional[str] = None
    action_direction: Optional[Dict] = None
    # Explain Panel
    explain_panel: Optional[ExplainPanelData] = None


class AllocationChatResponse(BaseModel):
    intent_type: str
    response: AllocationAIResponse
    updated_session_context: Optional[SessionContext] = None
