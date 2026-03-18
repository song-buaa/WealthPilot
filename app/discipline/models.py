"""
投资纪律执行引擎 — 数据结构定义

所有引擎模块通过这些标准结构传递数据，禁止在引擎内部定义私有数据类。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional


# ─────────────────────────────────────────────────────────
# 输入结构
# ─────────────────────────────────────────────────────────

@dataclass
class PositionState:
    """单一标的当前状态"""
    symbol: str                            # 股票代码或名称（唯一标识）
    name: str = ""                         # 显示名称
    weight: float = 0.0                    # 当前仓位占投资性资产比例（0~1）
    target_weight: float = 0.0            # 目标仓位占比（0=未设定，跳过偏离度检查）
    cost_basis: float = 0.0               # 成本价
    current_price: float = 0.0            # 当前价格
    drawdown_pct: float = 0.0             # 从成本计算的回撤（负数，如 -0.30 = 回撤 30%）
    asset_class: str = "equity"           # equity / fixed_income / cash / alternatives / leverage_etf
    is_core_holding: bool = False         # 是否为持有 1 年以上的核心仓位（规则9）
    last_add_date: Optional[date] = None  # 上次加仓日期（规则6 间隔检查）
    logic_intact: bool = True             # 长期逻辑是否完好（规则7/5）


@dataclass
class PortfolioState:
    """账户整体状态"""
    total_assets: float                   # 总投资性资产（人民币）
    cash_ratio: float                     # 流动性资金比例（货币+固收，0~1）
    drawdown_pct: float                   # 账户从历史高点的回撤（负数，如 -0.25 = 回撤 25%）
    positions: List[PositionState] = field(default_factory=list)


@dataclass
class MarketContext:
    """市场环境"""
    trend: str = "sideways"              # up / down / sideways
    volatility: str = "normal"           # low / normal / high
    recent_events: List[str] = field(default_factory=list)
    major_negative_event: bool = False   # 是否发生重大利空事件


@dataclass
class UserState:
    """用户行为与心理状态"""
    emotional_state: str = "normal"      # normal / regret / greed / panic / lucky
    cooldown_active: bool = False
    cooldown_until: Optional[datetime] = None
    daily_nav_drop_pct: float = 0.0      # 今日净值跌幅（负数，如 -0.05 = 跌 5%）


@dataclass
class TradeAction:
    """待评估的交易操作"""
    action_type: str                     # BUY / SELL / ADD / REDUCE
    symbol: str
    amount_pct: float                    # 本次操作占总投资性资产的比例（正数，如 0.10 = 10%）
    is_margin_trading: bool = False      # 是否涉及融资融券
    is_options: bool = False             # 是否涉及期权
    is_credit_loan: bool = False         # 是否使用借贷资金
    is_leverage_etf: bool = False        # 是否为杠杆 ETF


# ─────────────────────────────────────────────────────────
# 引擎输出结构
# ─────────────────────────────────────────────────────────

@dataclass
class RiskCheckResult:
    """Risk Engine 输出"""
    status: str                                    # ALLOW / BLOCK / WARNING
    violated_rules: List[str] = field(default_factory=list)   # 违规规则编号列表
    messages: List[str] = field(default_factory=list)         # BLOCK 级消息
    warnings: List[str] = field(default_factory=list)         # WARNING 级消息


@dataclass
class PsychologyCheckResult:
    """Psychology Engine 输出"""
    status: str                                    # NORMAL / COOLDOWN
    triggered_reasons: List[str] = field(default_factory=list)
    cooldown_until: Optional[datetime] = None


@dataclass
class DecisionResult:
    """Decision Engine 输出"""
    recommendation: str                            # BUY / SELL / HOLD / REDUCE / ADD
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class EvaluationResult:
    """engine_runner.evaluate_action() 最终输出"""
    allowed: bool
    final_verdict: str                             # BLOCKED / COOLDOWN / PROCEED
    risk: RiskCheckResult = field(
        default_factory=lambda: RiskCheckResult(status="ALLOW")
    )
    psychology: PsychologyCheckResult = field(
        default_factory=lambda: PsychologyCheckResult(status="NORMAL")
    )
    decision: DecisionResult = field(
        default_factory=lambda: DecisionResult(recommendation="HOLD")
    )
    block_reasons: List[str] = field(default_factory=list)
