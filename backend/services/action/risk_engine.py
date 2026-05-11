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

    if portfolio_total_value <= 0:
        logger.warning("[RiskEngine] portfolio_total_value <= 0, 跳过风控")
        return result

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
