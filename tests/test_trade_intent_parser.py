"""Deterministic Phase 1 Typed Trade Intent contract and parser tests."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.services.trade_intent.models import (
    FieldProvenance,
    FieldResolutionStatus,
    IntentConfirmationStatus,
    IntentReadiness,
)
from backend.services.trade_intent.parser import (
    ExtractedEnumField,
    ExtractedMoneyField,
    ExtractedTextField,
    ExtractedTradeIntentLeg,
    ExtractionProvenance,
    TradeIntentExtraction,
    UnsupportedFeature,
    is_actionable_trade_candidate,
    parse_trade_intent,
)


CANONICAL_MESSAGE = """IBKR 现在还有 $16,632 现金，全部用于补充固收，按以下结构执行：
1. IBTA：买入约 $11,350。
2. VDCA：买入约 $2,850。
3. CBU0：买入约 $1,400。
4. IB01：剩余资金全部买入。
全部选择 LSE 美元交易线、Acc 累积型，使用限价单买入。"""


def enum_field(value, provenance="USER_EXPLICIT", source=None):
    return ExtractedEnumField(
        value=value,
        provenance=ExtractionProvenance(provenance),
        source_text=source,
    )


def text_field(value, provenance="USER_EXPLICIT", source=None):
    return ExtractedTextField(
        value=value,
        provenance=ExtractionProvenance(provenance),
        source_text=source,
    )


def money_field(amount, currency="USD", provenance="USER_EXPLICIT", source=None):
    return ExtractedMoneyField(
        amount=amount,
        currency=currency,
        provenance=ExtractionProvenance(provenance),
        source_text=source,
    )


def missing_enum():
    return enum_field(None, "MISSING")


def missing_money():
    return money_field(None, None, "MISSING")


def leg(alias, mode, amount=None, *, amount_currency="USD"):
    return ExtractedTradeIntentLeg(
        alias=text_field(alias, source=alias),
        allocation_mode=enum_field(
            mode,
            "AI_INFERRED" if mode in {"APPROX_AMOUNT", "REMAINDER"} else mode,
            source="约" if mode == "APPROX_AMOUNT" else "剩余资金",
        ),
        target_amount=(
            money_field(amount, amount_currency, source=str(amount))
            if amount is not None else missing_money()
        ),
        venue_override=missing_enum(),
        trading_currency_override=missing_enum(),
        share_class_override=missing_enum(),
    )


def canonical_extraction() -> TradeIntentExtraction:
    return TradeIntentExtraction(
        is_trade_intent=True,
        broker=enum_field("IBKR", source="IBKR"),
        account=text_field(None, "MISSING"),
        # Mirrors the misclassification observed during the first human check.
        # Parser normalization must correct facts provable from user text.
        funding_source=enum_field("CASH", "AI_INFERRED", "现金"),
        funding_currency=enum_field("USD", "AI_INFERRED", "$16,632"),
        budget_mode=enum_field("FIXED_TOTAL", "AI_INFERRED", "全部用于"),
        stated_cash=money_field(16632, "USD", source="$16,632"),
        venue=enum_field("LSE", source="LSE"),
        trading_currency=enum_field("USD", source="美元交易线"),
        share_class=enum_field("ACC", source="Acc 累积型"),
        side=enum_field("BUY", source="买入"),
        order_type=enum_field("LIMIT", source="限价单"),
        legs=[
            leg("IBTA", "APPROX_AMOUNT", 11350),
            leg("VDCA", "APPROX_AMOUNT", 2850),
            leg("CBU0", "APPROX_AMOUNT", 1400),
            leg("IB01", "REMAINDER"),
        ],
    )


class FakeProvider:
    def __init__(self, extraction=None, error=None):
        self.extraction = extraction
        self.error = error
        self.calls = 0

    def extract(self, user_message, conversation_context):
        self.calls += 1
        if self.error:
            raise self.error
        return self.extraction


def test_canonical_case_builds_ready_typed_intent_without_execution_facts(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT", "must-not-be-read")
    intent = parse_trade_intent(CANONICAL_MESSAGE, provider=FakeProvider(canonical_extraction()))

    assert intent is not None
    assert intent.readiness == IntentReadiness.READY_FOR_CONFIRMATION
    assert intent.confirmation_status == IntentConfirmationStatus.PENDING
    assert intent.broker.value == "IBKR"
    assert intent.stated_cash.value == {"amount": 16632.0, "currency": "USD"}
    assert intent.stated_cash.provenance == FieldProvenance.USER_EXPLICIT
    assert intent.budget_mode.value == "ALL_AVAILABLE_CASH"
    assert intent.funding_source.provenance == FieldProvenance.USER_EXPLICIT
    assert intent.funding_currency.provenance == FieldProvenance.USER_EXPLICIT
    assert intent.account.value is None
    assert intent.account.status == FieldResolutionStatus.MISSING
    assert [item.alias.value for item in intent.legs] == ["IBTA", "VDCA", "CBU0", "IB01"]
    assert [item.allocation_mode.value for item in intent.legs] == [
        "APPROX_AMOUNT", "APPROX_AMOUNT", "APPROX_AMOUNT", "REMAINDER",
    ]
    assert intent.legs[0].target_amount.value["amount"] == 11350
    assert intent.legs[3].target_amount.value is None
    assert intent.legs[0].allocation_mode.provenance == FieldProvenance.AI_INFERRED

    payload = intent.model_dump(mode="json")
    encoded = json.dumps(payload)
    for forbidden in (
        "conId", "con_id", "localSymbol", "local_symbol", "marketRule",
        "market_rule", "bid", "ask", "limit_price", "quantity", "commission",
        "usable_cash", "remainder_amount", "execution_batch", "order_record",
    ):
        assert forbidden not in encoded


def test_ai_inferred_account_is_discarded_and_deferred_to_phase2():
    extraction = canonical_extraction()
    extraction.account = text_field("inferred-live-account", "AI_INFERRED")

    intent = parse_trade_intent(CANONICAL_MESSAGE, provider=FakeProvider(extraction))

    assert intent.account.value is None
    assert intent.account.provenance == FieldProvenance.NOT_PROVIDED
    assert intent.account.status == FieldResolutionStatus.MISSING
    assert intent.readiness == IntentReadiness.READY_FOR_CONFIRMATION


def test_analysis_only_does_not_call_provider_or_create_preview():
    message = "比较一下 IBTA、VDCA、CBU0 和 IB01，哪个更适合配置固收？"
    provider = FakeProvider(error=AssertionError("provider must not be called"))

    assert is_actionable_trade_candidate(message) is False
    assert parse_trade_intent(message, provider=provider) is None
    assert provider.calls == 0


def test_missing_multi_leg_allocation_is_blocked_without_fifty_fifty_inference():
    extraction = canonical_extraction()
    extraction.budget_mode = enum_field("FIXED_TOTAL", "AI_INFERRED", "1 万美元")
    extraction.stated_cash = money_field(10000, "USD", source="1 万美元")
    extraction.legs = [
        leg("IBTA", "AMBIGUOUS"),
        leg("VDCA", "AMBIGUOUS"),
    ]

    intent = parse_trade_intent(
        "把 1 万美元买 IBTA 和 VDCA。",
        provider=FakeProvider(extraction),
    )

    assert intent.readiness == IntentReadiness.NEEDS_REVIEW
    assert intent.confirmation_status == IntentConfirmationStatus.BLOCKED
    assert all(item.target_amount.value is None for item in intent.legs)
    assert all(item.allocation_mode.status == FieldResolutionStatus.AMBIGUOUS for item in intent.legs)


def test_true_fixed_total_is_not_rewritten_as_all_available_cash():
    extraction = canonical_extraction()
    extraction.budget_mode = enum_field("FIXED_TOTAL", "AI_INFERRED", "拿 1 万美元")
    extraction.stated_cash = money_field(10000, "USD", source="1 万美元")
    extraction.legs = [
        leg("IBTA", "APPROX_AMOUNT", 7000),
        leg("VDCA", "APPROX_AMOUNT", 3000),
    ]

    intent = parse_trade_intent(
        "我准备拿 1 万美元买 IBTA 7000、VDCA 3000。全部走 LSE 美元交易线、Acc，使用限价单买入。",
        provider=FakeProvider(extraction),
    )

    assert intent.budget_mode.value == "FIXED_TOTAL"
    assert intent.funding_currency.provenance == FieldProvenance.USER_EXPLICIT


def test_remainder_keeps_target_amount_null():
    extraction = canonical_extraction()
    extraction.legs = [
        leg("IBTA", "APPROX_AMOUNT", 8000),
        leg("IB01", "REMAINDER"),
    ]
    intent = parse_trade_intent(
        "IBTA 买 8000 美元，剩下的钱买 IB01。",
        provider=FakeProvider(extraction),
    )

    assert intent.legs[1].allocation_mode.value == "REMAINDER"
    assert intent.legs[1].target_amount.value is None


@pytest.mark.parametrize(
    ("message", "field", "value", "feature"),
    [
        ("卖出 IBTA 5000 美元。", "side", "SELL", UnsupportedFeature.SELL),
        ("IBTA 全部用市价单买。", "order_type", "MARKET", UnsupportedFeature.MARKET_ORDER),
    ],
)
def test_unsupported_side_and_order_type_are_preserved_and_blocked(
    message, field, value, feature,
):
    extraction = canonical_extraction()
    setattr(extraction, field, enum_field(value, source=message))
    extraction.unsupported_features = [feature]

    intent = parse_trade_intent(message, provider=FakeProvider(extraction))

    assert getattr(intent, field).value == value
    assert intent.readiness == IntentReadiness.UNSUPPORTED_FOR_V3_15_V1
    assert intent.confirmation_status == IntentConfirmationStatus.BLOCKED


def test_conflicting_leg_currency_is_explicit_and_blocking():
    extraction = canonical_extraction()
    extraction.legs[1].trading_currency_override = enum_field("EUR", source="VDCA 用欧元线")
    extraction.ambiguities = ["全局 USD 与 VDCA EUR 约束冲突"]

    intent = parse_trade_intent(
        "全部走 LSE 美元线，其中 VDCA 用欧元线买入约 2850 美元。",
        provider=FakeProvider(extraction),
    )

    assert intent.legs[1].trading_currency_override.value == "EUR"
    assert intent.legs[1].trading_currency_override.status == FieldResolutionStatus.CONFLICTING
    assert intent.confirmation_status == IntentConfirmationStatus.BLOCKED
    assert any(issue.code == "conflicting_constraint" for issue in intent.issues)


def test_parser_failure_fails_closed():
    intent = parse_trade_intent(
        "IBTA 买入 1000 美元，使用限价单。",
        provider=FakeProvider(error=TimeoutError("offline timeout")),
    )

    assert intent is not None
    assert intent.readiness == IntentReadiness.PARSE_FAILED
    assert intent.confirmation_status == IntentConfirmationStatus.BLOCKED
    assert intent.legs == []


def test_structured_provider_schema_rejects_execution_only_fields():
    payload = canonical_extraction().model_dump(mode="json")
    payload["conId"] = 272686955
    with pytest.raises(ValidationError):
        TradeIntentExtraction.model_validate(payload)
