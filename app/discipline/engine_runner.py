"""
Engine Runner — 统一执行入口

执行流程（严格按序，禁止跳过）：
    Step 1  Risk Engine    — BLOCK → 直接拒绝，不进入后续步骤
    Step 2  Psychology Engine — COOLDOWN → 禁止操作，不进入后续步骤
    Step 3  Decision Engine   — 输出操作建议

对外暴露：evaluate_action()
"""

from .models import (
    PortfolioState, PositionState, MarketContext,
    UserState, TradeAction, EvaluationResult,
    RiskCheckResult, PsychologyCheckResult, DecisionResult,
)
from . import risk_engine, psychology_engine, decision_engine


def evaluate_action(
    portfolio: PortfolioState,
    position: PositionState,
    market: MarketContext,
    user_state: UserState,
    action: TradeAction,
    t_strategy_drawdown: float = 0.0,
) -> EvaluationResult:
    """
    投资纪律执行引擎主入口。

    Args:
        portfolio           账户整体状态（现金比例、总资产、回撤、持仓列表）
        position            目标标的当前状态（仓位、成本、逻辑完好性等）
        market              市场环境（趋势、波动率、重大事件）
        user_state          用户行为状态（情绪、冷却期、今日净值跌幅）
        action              待评估的操作（类型、标的、金额比例、工具类型）
        t_strategy_drawdown 做T参考回调幅度（可选，负数，如 -0.12 = 卖出后已回调 12%）

    Returns:
        EvaluationResult
            allowed        = True  → 可操作，参考 decision.recommendation
            allowed        = False → 被拦截，block_reasons 包含原因
            final_verdict  = BLOCKED / COOLDOWN / PROCEED
    """

    # ── Step 1: Risk Engine ──────────────────────────────
    risk_result = risk_engine.run(portfolio, action)

    if risk_result.status == "BLOCK":
        return EvaluationResult(
            allowed=False,
            final_verdict="BLOCKED",
            risk=risk_result,
            psychology=PsychologyCheckResult(status="NORMAL"),
            decision=DecisionResult(recommendation="HOLD"),
            block_reasons=risk_result.messages,
        )

    # ── Step 2: Psychology Engine ────────────────────────
    psych_result = psychology_engine.run(user_state, portfolio)

    if psych_result.status == "COOLDOWN":
        return EvaluationResult(
            allowed=False,
            final_verdict="COOLDOWN",
            risk=risk_result,
            psychology=psych_result,
            decision=DecisionResult(recommendation="HOLD"),
            block_reasons=psych_result.triggered_reasons,
        )

    # ── Step 3: Decision Engine ──────────────────────────
    decision_result = decision_engine.run(
        portfolio=portfolio,
        position=position,
        market=market,
        action=action,
        risk_result=risk_result,
        t_strategy_drawdown=t_strategy_drawdown,
    )

    return EvaluationResult(
        allowed=True,
        final_verdict="PROCEED",
        risk=risk_result,
        psychology=psych_result,
        decision=decision_result,
        block_reasons=[],
    )


# ─────────────────────────────────────────────────────────
# 示例调用
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    from datetime import date
    from .models import PositionState, PortfolioState, MarketContext, UserState, TradeAction

    # 构造示例：模拟加仓理想汽车，仓位已在警戒区
    portfolio = PortfolioState(
        total_assets=1_000_000,
        cash_ratio=0.18,        # 低于 20% → 流动性不足
        drawdown_pct=-0.10,     # 账户回撤 10%，未触发熔断
        positions=[
            PositionState(
                symbol="LI",
                name="理想汽车",
                weight=0.32,            # 已进入警戒区（>30%）
                target_weight=0.25,
                drawdown_pct=-0.15,
                asset_class="equity",
                is_core_holding=True,
                last_add_date=date.today(),  # 今天已加过仓
                logic_intact=True,
            )
        ],
    )

    action = TradeAction(
        action_type="ADD",
        symbol="LI",
        amount_pct=0.08,   # 加仓 8%
    )

    market = MarketContext(
        trend="down",
        volatility="high",
        major_negative_event=True,
    )

    user_state = UserState(
        emotional_state="regret",   # 不甘心 → 触发冷却
        daily_nav_drop_pct=-0.03,
    )

    result = evaluate_action(portfolio, portfolio.positions[0], market, user_state, action)

    print(f"\n{'='*60}")
    print(f"  最终裁定：{result.final_verdict}  |  allowed={result.allowed}")
    print(f"{'='*60}")

    print(f"\n[Risk Engine]  status={result.risk.status}")
    for msg in result.risk.messages:
        print(f"  BLOCK  {msg}")
    for w in result.risk.warnings:
        print(f"  WARN   {w}")

    print(f"\n[Psychology Engine]  status={result.psychology.status}")
    for r in result.psychology.triggered_reasons:
        print(f"  {r}")

    if result.allowed:
        print(f"\n[Decision Engine]  recommendation={result.decision.recommendation}")
        for r in result.decision.reasons:
            print(f"  {r}")
        for w in result.decision.warnings:
            print(f"  WARN  {w}")

    if result.block_reasons:
        print(f"\n[拦截原因]")
        for r in result.block_reasons:
            print(f"  >> {r}")
