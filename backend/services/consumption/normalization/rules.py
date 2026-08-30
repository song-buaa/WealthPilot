"""High-confidence source-explicit evidence rules, deliberately narrow."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.services.consumption.economic_events import EventType, RuleSource


@dataclass(frozen=True)
class Evidence:
    event_type: EventType
    rule_source: RuleSource
    reason: str | None = None
    confidence: str = "HIGH"


_MARKERS = {
    "CONSUMPTION": EventType.CONSUMPTION,
    "REFUND": EventType.REFUND,
    "CC_REPAYMENT": EventType.CREDIT_CARD_REPAYMENT,
    "INSTALLMENT_PAYMENT": EventType.CREDIT_CARD_REPAYMENT,
    "LIQUIDITY_SWEEP": EventType.LIQUIDITY_SWEEP,
    "INVESTMENT": EventType.INVESTMENT_TRANSFER,
    "INCOME": EventType.INCOME,
    "LOAN_DISBURSEMENT": EventType.LOAN_DISBURSEMENT,
    "DEBT_REPAYMENT": EventType.DEBT_REPAYMENT,
    "FEE_INTEREST": EventType.FEE_INTEREST,
    "REBATE": EventType.REBATE,
}


def _marker(description: str) -> str | None:
    return description[1:description.index("]")] if description.startswith("[") and "]" in description else None


def classify_source(
    *, raw_description: str, account_type: str, source_amount: Decimal | None = None,
    source_section: str | None = None,
) -> Evidence:
    """Return only classifications supported by explicit source wording.

    Debit-card personal transfers deliberately fall through to ``OTHER``. A
    credit-card row can be a purchase only after all explicit exclusion rules
    are exhausted; this reflects the verified statement type, not merchant
    category inference.
    """
    if source_section == "REFUND":
        return Evidence(EventType.REFUND, RuleSource.DESCRIPTION_RULE, "SOURCE_STATEMENT_REFUND_SECTION")
    if source_section == "CREDIT_CARD_REPAYMENT":
        return Evidence(EventType.CREDIT_CARD_REPAYMENT, RuleSource.DESCRIPTION_RULE, "SOURCE_STATEMENT_REPAYMENT_SECTION")

    marker = _marker(raw_description)
    if marker in _MARKERS:
        return Evidence(_MARKERS[marker], RuleSource.DESCRIPTION_RULE)
    if marker == "INSTALLMENT_PRINCIPAL":
        return Evidence(EventType.OTHER, RuleSource.DESCRIPTION_RULE, "INSTALLMENT_ORIGINAL_PURCHASE_UNAVAILABLE")
    if marker == "UNKNOWN_INCOMING":
        return Evidence(EventType.OTHER, RuleSource.DESCRIPTION_RULE, "INCOMING_SOURCE_UNPROVEN")

    text = "".join(raw_description.casefold().split())
    exact_rules = (
        (("贷款利息", "贷款手续费", "分期手续费", "分期利息"), EventType.FEE_INTEREST),
        ((
            "信用卡自动还款", "信用卡还款", "招行信用卡还款", "自动还款", "按卡转账还款",
            "分期还款", "分期还款本金", "分期本金偿还", "分期本金", "分期偿还",
        ), EventType.CREDIT_CARD_REPAYMENT),
        (("银联入账", "还款入账"), EventType.CREDIT_CARD_REPAYMENT),
        (("朝朝宝转入", "朝朝宝转出"), EventType.LIQUIDITY_SWEEP),
        (("银证转账", "基金申购", "基金赎回", "基金快速赎回", "理财申购", "理财赎回"), EventType.INVESTMENT_TRANSFER),
        (("代发工资", "住房公积金管理中心代发"), EventType.INCOME),
        (("个贷放款",), EventType.LOAN_DISBURSEMENT),
        (("贷款本金偿还", "个贷本金偿还"), EventType.DEBT_REPAYMENT),
        (("活动现金红包", "信用卡返现"), EventType.REBATE),
        (("退款",), EventType.REFUND),
    )
    for phrases, event_type in exact_rules:
        if any(phrase in text for phrase in phrases):
            return Evidence(event_type, RuleSource.DESCRIPTION_RULE)
    # Both supported credit-card statements express merchant refunds as a
    # negative amount.  This remains below explicit description exclusions so
    # a named repayment is never misrepresented as a refund.
    if account_type == "CREDIT_CARD" and source_amount is not None and source_amount < 0:
        return Evidence(EventType.REFUND, RuleSource.DESCRIPTION_RULE, "CREDIT_CARD_NEGATIVE_MERCHANT_ENTRY")
    if account_type == "CREDIT_CARD":
        return Evidence(EventType.CONSUMPTION, RuleSource.DESCRIPTION_RULE)
    return Evidence(EventType.OTHER, RuleSource.DESCRIPTION_RULE, "SOURCE_SEMANTICS_UNPROVEN")


def has_explicit_internal_transfer_marker(raw_description: str) -> bool:
    marker = _marker(raw_description)
    text = "".join(raw_description.casefold().split())
    return marker == "INTERNAL" or "本人账户转账" in text


def refund_reference(raw_description: str) -> str | None:
    marker = "REF:"
    return raw_description.split(marker, 1)[1].split()[0] if marker in raw_description else None
