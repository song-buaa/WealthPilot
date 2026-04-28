"""
ViewpointCard v2 Pydantic schemas — 三层分离结构。

事实层(FactsLayer): 机器填充，用户只读
叙事层(NarrativeLayer): LLM 生成，用户可追加 annotation
判断层(JudgmentLayer): 人类填写（LLM 可预填），决策引擎消费核心
"""

import enum
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ── 枚举定义 ──────────────────────────────────────────────────────


class SourceType(str, enum.Enum):
    USER_UPLOAD = "user_upload"
    ALPHA_VANTAGE_NEWS = "alpha_vantage_news"
    ALPHA_VANTAGE_FUNDAMENTAL = "alpha_vantage_fundamental"
    ALPHA_VANTAGE_EARNINGS = "alpha_vantage_earnings"
    PERPLEXITY_SEARCH = "perplexity_search"
    AKSHARE_NEWS = "akshare_news"
    AKSHARE_FUNDAMENTAL = "akshare_fundamental"
    AKSHARE_HIST = "akshare_hist"
    HYBRID = "hybrid"


class EventType(str, enum.Enum):
    EARNINGS = "earnings"
    GUIDANCE_UPDATE = "guidance_update"
    ANALYST_RATING = "analyst_rating"
    PRODUCT_LAUNCH = "product_launch"
    PRODUCT_REVIEW = "product_review"
    DELIVERY_OR_SALES_DATA = "delivery_or_sales_data"
    INDUSTRY_COMPETITION = "industry_competition"
    MACRO_POLICY = "macro_policy"
    REGULATORY = "regulatory"
    MANAGEMENT_CHANGE = "management_change"
    EXECUTIVE_INTERVIEW = "executive_interview"
    CORPORATE_ACTION = "corporate_action"
    FUNDAMENTAL_SNAPSHOT = "fundamental_snapshot"
    MARKET_MOVEMENT = "market_movement"
    OTHER = "other"


class UserEndorsement(str, enum.Enum):
    ENDORSE = "endorse"
    REFERENCE_ONLY = "reference_only"
    DISAGREE = "disagree"


class Stance(str, enum.Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    WATCH = "watch"


class Horizon(str, enum.Enum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class Confidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ActionType(str, enum.Enum):
    CONSIDER_ADD = "consider_add"
    CONSIDER_REDUCE = "consider_reduce"
    CONSIDER_EXIT = "consider_exit"
    HOLD_OBSERVE = "hold_observe"
    NO_ACTION = "no_action"


class ValidityStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


class RelationType(str, enum.Enum):
    REINFORCES = "reinforces"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    FOLLOWS_UP = "follows_up"


# ── 子结构 ────────────────────────────────────────────────────────


class SourceRef(BaseModel):
    """来源引用"""
    ref_type: str  # "url" / "document_id" / "api_call_id"
    ref_value: str
    title: Optional[str] = None


class Annotation(BaseModel):
    """用户追加备注"""
    author: str  # "user" / "system"
    created_at: datetime = Field(default_factory=datetime.now)
    content: str
    attached_to: Optional[str] = None  # "thesis" / "bull_case" / "bear_case" / "summary"


class GuidanceItem(BaseModel):
    """guidance 子项"""
    value: Optional[str] = None
    period: Optional[str] = None
    vs_consensus: Optional[str] = None


class Guidance(BaseModel):
    """guidance 子结构"""
    revenue: Optional[GuidanceItem] = None
    delivery: Optional[GuidanceItem] = None
    margin: Optional[GuidanceItem] = None


class ExtractedKPI(BaseModel):
    """LLM 从事实层抽取的关键指标（决策输入）"""

    # 第一优先（必须抽取）
    current_price: Optional[float] = None
    target_price: Optional[float] = None
    price_change_pct: Optional[float] = None
    revenue_yoy: Optional[float] = None
    earnings_yoy: Optional[float] = None
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None
    free_cash_flow: Optional[float] = None
    deliveries_latest: Optional[float] = None
    deliveries_yoy: Optional[float] = None
    analyst_target_upside: Optional[float] = None

    # 第二优先（扩展用，允许 null）
    market_cap: Optional[float] = None
    pe_ttm: Optional[float] = None
    forward_pe: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    eps_surprise_pct: Optional[float] = None
    subsidy_or_marketing_spend: Optional[float] = None

    # guidance 子结构
    guidance: Optional[Guidance] = None

    # 元数据
    notes: Optional[str] = None
    extra: Optional[dict] = None


class DecisionSignal(BaseModel):
    """结构化决策信号"""
    direction: int = Field(default=0, ge=-1, le=1)  # -1 / 0 / +1
    strength: float = Field(ge=0.0, le=1.0, default=0.5)
    confidence_score: float = Field(ge=0.0, le=1.0, default=0.3)


class Relation(BaseModel):
    """观点间关系"""
    related_card_id: str
    relation_type: RelationType
    note: Optional[str] = None
    created_by: str = "user"  # "user" / "llm_auto"
    created_at: datetime = Field(default_factory=datetime.now)


# ── 三层 Schema ───────────────────────────────────────────────────


class FactsLayer(BaseModel):
    """事实层 — 机器填充，用户只读"""
    affected_symbols: list[str] = Field(default_factory=list)  # 标准 symbol 字符串
    primary_symbol: Optional[str] = None
    primary_entity_id: Optional[str] = None

    @field_validator("affected_symbols")
    @classmethod
    def _validate_affected_symbols(cls, v: list[str]) -> list[str]:
        """校验每个 symbol 都能解析为合法 Symbol"""
        from research_v2.symbol import Symbol
        for s in v:
            Symbol.parse(s)
        return v

    @field_validator("primary_symbol")
    @classmethod
    def _validate_primary_symbol(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        from research_v2.symbol import Symbol
        Symbol.parse(v)
        return v
    source_type: SourceType
    source_refs: list[SourceRef] = Field(default_factory=list)
    as_of: datetime
    ingested_at: datetime = Field(default_factory=datetime.now)
    raw_facts: dict = Field(default_factory=dict)
    sentiment_raw: Optional[dict] = None


class NarrativeLayer(BaseModel):
    """叙事层 — LLM 生成，用户不能改原文，可追加 annotation"""
    thesis: Optional[str] = None
    bull_case: Optional[str] = None
    bear_case: Optional[str] = None
    narrative_summary: Optional[str] = None
    event_type: EventType  # 必填，Processor 必须显式给值
    topics: list[str] = Field(default_factory=list)
    extracted_kpi: Optional[ExtractedKPI] = None
    user_annotations: list[Annotation] = Field(default_factory=list)


class JudgmentLayer(BaseModel):
    """判断层 — 人类填写（LLM 可预填），决策引擎消费核心。

    is_ai_prefilled 是"用户是否确认"的唯一信号来源：
    - True：LLM 预填状态，用户未确认，其他判断层字段的值只是 LLM 草稿。
      决策引擎默认只消费 is_ai_prefilled=False 的卡。
    - False：用户点击 Confirm 按钮后设为 False，表示值可信。
    """
    is_ai_prefilled: bool = True
    user_endorsement: UserEndorsement = UserEndorsement.REFERENCE_ONLY
    stance: Stance = Stance.NEUTRAL
    horizon: Horizon = Horizon.MEDIUM
    confidence: Confidence = Confidence.LOW
    decision_signal: DecisionSignal = Field(default_factory=DecisionSignal)
    action_type: ActionType = ActionType.HOLD_OBSERVE
    trigger_conditions: Optional[str] = None
    invalidation_conditions: Optional[str] = None
    key_metrics_to_watch: list[str] = Field(default_factory=list)
    validity_status: ValidityStatus = ValidityStatus.ACTIVE
    expires_at: Optional[datetime] = None


# ── ViewpointCard 完整结构 ────────────────────────────────────────


class ViewpointCard(BaseModel):
    """三层分离的观点卡完整结构"""
    card_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    facts: FactsLayer
    narrative: NarrativeLayer = Field(default_factory=NarrativeLayer)
    judgment: JudgmentLayer = Field(default_factory=JudgmentLayer)
    relations: list[Relation] = Field(default_factory=list)
    status: str = "pending_review"  # pending_review / active / discarded
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
