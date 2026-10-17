"""
WealthPilot v3.2 M6 风控引擎。

v3.2 实现 3 条规则：
1. 单笔金额占比 > 5% → warning
2. 集中度 > 40%（卖出跳过）→ warning
3. 纪律违反（复用 check_discipline_rules 简化版）→ warning

设计原则：
- 纯函数，不访问 DB（调用方传入所需数据）
- 返回结构化结果，前端展示 + 后端二次校验共用
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)

CONFIRMATION_TEXT = "我已知晓风险并坚持下单"

# v3.2 风控阈值
SINGLE_ORDER_PCT_THRESHOLD = Decimal("0.05")   # 5%
CONCENTRATION_PCT_THRESHOLD = Decimal("0.40")   # 40%


@dataclass
class RiskWarning:
    rule: str          # "single_order_pct" / "concentration" / "discipline"
    severity: str      # "warning"
    message: str       # 用户可读文案
    detail: dict       # 具体数据


@dataclass
class RiskCheckResult:
    passed: bool = True                           # 全部通过 = True
    warnings: list[RiskWarning] = field(default_factory=list)

    @property
    def requires_confirmation(self) -> bool:
        return len(self.warnings) > 0


def check_order_risk(
    symbol: str,
    side: str,
    quantity: int,
    limit_price: Decimal,
    portfolio_total_value: Decimal,
    current_position_value: Optional[Decimal] = None,
    discipline_result: Optional[dict] = None,
    fx_rate_to_cny: Decimal = Decimal("1"),
) -> RiskCheckResult:
    """
    对一笔待提交订单执行风控检查。

    Args:
        symbol: 标的代码
        side: BUY / SELL
        quantity: 下单数量
        limit_price: 限价（交易币种）
        portfolio_total_value: 组合总资产（CNY）
        current_position_value: 该标的当前持仓市值（CNY），用于集中度计算
        discipline_result: check_discipline_rules 的返回结果 dict（可选）
        fx_rate_to_cny: 交易币种对 CNY 汇率（如 USD→CNY = 6.9）
    """
    result = RiskCheckResult()

    quantified = portfolio_total_value > 0 and fx_rate_to_cny is not None and fx_rate_to_cny > 0
    if not quantified:
        result.warnings.append(RiskWarning("data_quality", "warning",
            "组合估值或可信汇率缺失，无法量化金额及集中度风险", {}))

    if quantified:
        # 将订单金额转为 CNY，与 portfolio_total_value 同口径比较
        order_value = Decimal(str(quantity)) * limit_price * fx_rate_to_cny

        # 规则 1: 单笔金额占比
        order_pct = order_value / portfolio_total_value
        if order_pct > SINGLE_ORDER_PCT_THRESHOLD:
            pct_display = f"{float(order_pct) * 100:.1f}"
            result.warnings.append(RiskWarning(
                rule="single_order_pct",
                severity="warning",
                message=f"单笔金额占总资产 {pct_display}%，超过 5% 警戒线",
                detail={
                    "current_pct": float(order_pct),
                    "threshold": float(SINGLE_ORDER_PCT_THRESHOLD),
                    "order_value": float(order_value),
                    "total_value": float(portfolio_total_value),
                },
            ))

        # 规则 2: 集中度（卖出降低集中度，不触发警告）
        if side.upper() == "SELL":
            pass  # 卖出降低集中度，跳过此检查
        elif current_position_value is not None:
            post_position_value = current_position_value + order_value
            concentration = post_position_value / portfolio_total_value
            if concentration > CONCENTRATION_PCT_THRESHOLD:
                pct_display = f"{float(concentration) * 100:.1f}"
                result.warnings.append(RiskWarning(
                    rule="concentration",
                    severity="warning",
                    message=f"操作后单标的持仓占总资产 {pct_display}%，超过 40% 上限",
                    detail={
                        "post_pct": float(concentration),
                        "threshold": float(CONCENTRATION_PCT_THRESHOLD),
                        "current_value": float(current_position_value),
                        "order_value": float(order_value),
                    },
                ))

    # 规则 3: 纪律违反（复用 check_discipline_rules）
    if discipline_result is not None:
        violation = discipline_result.get("violation", False)
        if violation:
            warning_msg = discipline_result.get("warning", "")
            rule_details = discipline_result.get("rule_details", [])
            result.warnings.append(RiskWarning(
                rule="discipline",
                severity="warning",
                message=f"纪律违反：{warning_msg}" if warning_msg else "纪律检查发现违反项",
                detail={
                    "violation": True,
                    "warning": warning_msg,
                    "rule_details": rule_details,
                    "current_weight": discipline_result.get("current_weight", 0),
                    "max_position": discipline_result.get("max_position", 0),
                },
            ))

    result.passed = len(result.warnings) == 0
    return result


def validate_confirmation(risk_result: RiskCheckResult, confirmation_text: str) -> bool:
    """验证用户是否正确输入了风险确认文字。"""
    if not risk_result.requires_confirmation:
        return True  # 无风控警告，无需确认
    return confirmation_text == CONFIRMATION_TEXT


def portfolio_order_risk(session, *, portfolio_id, symbol, side, quantity, limit_price, currency=None):
    """Shared ordinary/Batch risk inputs, scoped to the same Portfolio.

    Missing/ambiguous FX, valuation or discipline is an explicit warning. This
    function does not weaken hard quote/contract/cash/WhatIf execution gates.
    """
    from app.models import Position
    from app.fx_service import fx_service
    rows = session.query(Position).filter_by(portfolio_id=portfolio_id, segment="投资").all()
    positive = [p for p in rows if p.market_value_cny is not None and p.market_value_cny > 0]
    matching = [p for p in rows if p.symbol == symbol or (":" not in symbol and p.ticker == symbol)]
    currencies = {str(p.currency).upper() for p in matching if p.currency}
    currency = currency or (next(iter(currencies)) if len(currencies) == 1 else None)
    if not currency and ":" in symbol:
        currency = {"US": "USD", "HK": "HKD", "SH": "CNY", "SZ": "CNY", "LSE": "USD"}.get(symbol.rsplit(":", 1)[1])
    rate = None
    if currency == "CNY":
        rate = Decimal("1")
    elif currency:
        # Only an already available FX observation counts. Do not call providers
        # or package a hardcoded fallback as a quantified PASS.
        cached = getattr(fx_service, "_cache", {}).get((currency, "CNY", "latest"))
        if cached and cached[1] not in {"fallback", "latest", None}:
            try:
                rate = Decimal(str(cached[0]))
            except (TypeError, ValueError):
                pass
    total = sum((Decimal(str(p.market_value_cny)) for p in positive), Decimal("0"))
    position = sum((Decimal(str(p.market_value_cny or 0)) for p in matching), Decimal("0"))
    discipline = None
    try:
        # Reuse the original rule engine with already loaded Portfolio facts.
        # data_loader.load would also fetch research/market/LLM context and is
        # deliberately not part of an explicit order risk review.
        from dataclasses import asdict
        from decision_engine.data_loader import LoadedData, InvestmentRules, PositionInfo
        from decision_engine.rule_engine import check
        from decision_engine.types import IntentResult
        from app.discipline.config import get_rules
        from app.utils.position_aggregator import aggregate
        raw = [{"name": p.name, "ticker": p.ticker or "", "symbol": p.symbol or "",
                "currency": p.currency, "platform": p.platform or "", "asset_class": p.asset_class,
                "market_value_cny": p.market_value_cny or 0, "profit_loss_rate": p.profit_loss_rate or 0,
                "profit_loss_value": p.profit_loss_value or 0, "cost_price": p.cost_price or 0,
                "current_price": p.current_price or 0, "quantity": p.quantity or 0, "is_leverage_etf": False}
               for p in rows]
        positions, total_value = aggregate(raw)
        target = next((p for p in positions if p.symbol == symbol), None)
        config = get_rules()
        rules = InvestmentRules(config["single_asset_limits"]["max_position_pct"],
            config["asset_allocation_ranges"]["equity_max"], config["liquidity_limits"]["min_cash_pct"],
            config["leverage_limits"]["leverage_ratio_warning_max"])
        data = LoadedData(profile=None, positions=[PositionInfo.from_aggregated(p) for p in positions],
            target_position=PositionInfo.from_aggregated(target) if target else None, rules=rules, total_assets=total_value)
        intent = IntentResult(symbol, "买入判断" if side.upper() == "BUY" else "卖出判断", "未知", None, 1.0)
        discipline = asdict(check(data, intent))
    except Exception:
        pass
    result = check_order_risk(symbol, side, quantity, limit_price, total, position,
                              discipline, rate)
    if discipline is None:
        result.warnings.append(RiskWarning("data_quality", "warning", "纪律数据无法核实", {}))
    result.passed = not result.warnings
    return {"passed": result.passed, "requires_confirmation": result.requires_confirmation,
            "warnings": [vars(w) for w in result.warnings], "portfolio_total_value": float(total),
            "confirmation_text_required": CONFIRMATION_TEXT if result.requires_confirmation else None}
