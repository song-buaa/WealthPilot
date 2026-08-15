"""Narrow natural-language parser for v3.15 Phase 1 trade intent.

The parser receives only user conversation text. It has no portfolio, market,
broker, contract-resolution, quote, cash-authority, or execution dependency.
"""
from __future__ import annotations

import logging
import os
import re
from enum import Enum
from typing import Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    FieldProvenance,
    FieldResolutionStatus,
    IntentConfirmationStatus,
    IntentReadiness,
    StructuredTradeIntent,
    TradeIntentField,
    TradeIntentIssue,
    TradeIntentLeg,
    unresolved_field,
)

logger = logging.getLogger(__name__)


class _StrictExtractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractionProvenance(str, Enum):
    USER_EXPLICIT = "USER_EXPLICIT"
    AI_INFERRED = "AI_INFERRED"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"


T = TypeVar("T")


class ExtractedEnumField(_StrictExtractionModel, Generic[T]):
    value: T | None = None
    provenance: ExtractionProvenance
    source_text: str | None = None


class ExtractedTextField(_StrictExtractionModel):
    value: str | None = None
    provenance: ExtractionProvenance
    source_text: str | None = None


class ExtractedMoneyField(_StrictExtractionModel):
    amount: float | None = None
    currency: str | None = None
    provenance: ExtractionProvenance
    source_text: str | None = None


BrokerValue = Literal["IBKR", "TIGER", "FUTU", "OTHER"]
FundingSourceValue = Literal["CASH", "OTHER"]
CurrencyValue = Literal["USD", "HKD", "EUR", "GBP", "CNY", "OTHER"]
BudgetModeValue = Literal["ALL_AVAILABLE_CASH", "FIXED_TOTAL", "OTHER"]
VenueValue = Literal["LSE", "LSEETF", "NYSE", "NASDAQ", "OTHER"]
ShareClassValue = Literal["ACC", "DIST", "OTHER"]
SideValue = Literal["BUY", "SELL", "OTHER"]
OrderTypeValue = Literal["LIMIT", "MARKET", "OTHER"]
AllocationModeValue = Literal["APPROX_AMOUNT", "REMAINDER", "MISSING", "AMBIGUOUS"]


class ExtractedTradeIntentLeg(_StrictExtractionModel):
    alias: ExtractedTextField
    allocation_mode: ExtractedEnumField[AllocationModeValue]
    target_amount: ExtractedMoneyField
    venue_override: ExtractedEnumField[VenueValue]
    trading_currency_override: ExtractedEnumField[CurrencyValue]
    share_class_override: ExtractedEnumField[ShareClassValue]


class UnsupportedFeature(str, Enum):
    SELL = "SELL"
    MARKET_ORDER = "MARKET_ORDER"
    NON_USD_FUNDING = "NON_USD_FUNDING"
    FRACTIONAL_SHARES = "FRACTIONAL_SHARES"
    NON_LSE_VENUE = "NON_LSE_VENUE"
    NON_ACC_SHARE_CLASS = "NON_ACC_SHARE_CLASS"
    OTHER = "OTHER"


class TradeIntentExtraction(_StrictExtractionModel):
    is_trade_intent: bool
    broker: ExtractedEnumField[BrokerValue]
    account: ExtractedTextField
    funding_source: ExtractedEnumField[FundingSourceValue]
    funding_currency: ExtractedEnumField[CurrencyValue]
    budget_mode: ExtractedEnumField[BudgetModeValue]
    stated_cash: ExtractedMoneyField
    venue: ExtractedEnumField[VenueValue]
    trading_currency: ExtractedEnumField[CurrencyValue]
    share_class: ExtractedEnumField[ShareClassValue]
    side: ExtractedEnumField[SideValue]
    order_type: ExtractedEnumField[OrderTypeValue]
    legs: list[ExtractedTradeIntentLeg] = Field(default_factory=list)
    unsupported_features: list[UnsupportedFeature] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)


class StructuredTradeIntentProvider(Protocol):
    def extract(
        self,
        user_message: str,
        conversation_context: list[dict],
    ) -> TradeIntentExtraction: ...


_SYSTEM_PROMPT = """\
You extract a typed trade intent for WealthPilot v3.15 Phase 1.

Return only the schema requested by the structured-output API. Extract what the
user said; never resolve securities or calculate execution values.

Allowed normalized enum-like values:
- broker: IBKR, TIGER, FUTU, OTHER
- funding_source: CASH, OTHER
- currency: USD, HKD, EUR, GBP, CNY, OTHER
- budget_mode: ALL_AVAILABLE_CASH, FIXED_TOTAL, OTHER
- venue: LSE, LSEETF, NYSE, NASDAQ, OTHER
- share_class: ACC, DIST, OTHER
- side: BUY, SELL, OTHER
- order_type: LIMIT, MARKET, OTHER
- allocation_mode: APPROX_AMOUNT, REMAINDER, MISSING, AMBIGUOUS

Rules:
1. Analysis/comparison questions are not trade intents.
2. Do not invent allocation. If one total amount covers multiple legs without a
   per-leg split, mark each unresolved allocation MISSING or AMBIGUOUS.
3. A phrase such as "about $11,350" has an explicit target amount and an
   AI_INFERRED allocation mode APPROX_AMOUNT.
4. A remainder leg has target_amount.amount=null. Never calculate remainder.
5. Account stays missing unless the user explicitly names it in text. Never use
   environment, portfolio, or broker facts.
6. Preserve each supporting source excerpt. Do not output conId, IBKR symbol,
   localSymbol, resolved exchange, marketRule, bid, ask, limit price, quantity,
   commission, usable cash, remainder amount, batch, leg execution, or order data.
7. Detect SELL, MARKET, non-USD funding, non-LSE venue, non-Acc share class,
   and fractional-share requests in unsupported_features without rewriting them.
8. Leg-specific constraints must be placed in the override fields. Conflicting
   global and leg-specific constraints must also be described in ambiguities.
9. Provenance describes where the underlying fact came from, not whether an
   enum was normalized. If the user wrote "现金", CASH is USER_EXPLICIT. If the
   user wrote "$", "美元", "USD", or "美金", USD is USER_EXPLICIT.
10. ALL_AVAILABLE_CASH means the user describes an available cash balance and
    directs that all of it be used (including a remainder leg). FIXED_TOTAL means
    the user deliberately sets aside a fixed total such as "拿 1 万美元买...".
"""


class OpenAIStructuredTradeIntentProvider:
    """Strict OpenAI structured-output provider, created lazily."""

    def __init__(self, client=None, model: str = "gpt-4.1-mini"):
        self._client = client
        self._model = model

    def _client_or_default(self):
        if self._client is not None:
            return self._client
        import openai

        api_key = os.environ.get("WEALTHPILOT_OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("WEALTHPILOT_OPENAI_API_KEY 未配置")
        self._client = openai.OpenAI(api_key=api_key)
        return self._client

    def extract(
        self,
        user_message: str,
        conversation_context: list[dict],
    ) -> TradeIntentExtraction:
        context_lines: list[str] = []
        for turn in conversation_context[-6:]:
            role = str(turn.get("role", ""))
            content = str(turn.get("content", ""))
            if role in {"user", "assistant"} and content:
                context_lines.append(f"{role}: {content[:1200]}")

        prompt = (
            "Recent conversation context (may be empty):\n"
            + "\n".join(context_lines)
            + "\n\nCurrent user message (authoritative for this turn):\n"
            + user_message
        )
        response = self._client_or_default().responses.parse(
            model=self._model,
            input=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            text_format=TradeIntentExtraction,
            timeout=20.0,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise ValueError("structured output did not contain parsed data")
        return parsed


_ACTION_PATTERN = re.compile(
    r"(?:买入|买|卖出|卖|减仓|下单|执行|\bbuy\b|\bsell\b|\bpurchase\b)",
    re.IGNORECASE,
)
_EXECUTION_DETAIL_PATTERN = re.compile(
    r"(?:\$\s*[\d,.]+|[\d,.]+\s*万?\s*(?:美元|股)|限价|市价|现金|剩下|剩余|全部|按以下|按这|执行)",
    re.IGNORECASE,
)
_ANALYSIS_ONLY_PATTERN = re.compile(
    r"(?:比较|哪个更适合|该不该|是否|值得|怎么看|分析一下|适合.*(?:？|\?))",
    re.IGNORECASE,
)


def is_actionable_trade_candidate(user_message: str) -> bool:
    """Conservative deterministic gate that prevents analysis-only cards."""
    text = user_message.strip()
    if not text or not _ACTION_PATTERN.search(text):
        return False
    if not _EXECUTION_DETAIL_PATTERN.search(text):
        return False
    if _ANALYSIS_ONLY_PATTERN.search(text) and "执行" not in text:
        return False
    return True


_EXPLICIT_CASH_PATTERN = re.compile(r"(?:现金|cash)", re.IGNORECASE)
_EXPLICIT_USD_PATTERN = re.compile(r"(?:\$|美元|美金|\bUSD\b)", re.IGNORECASE)
_AVAILABLE_BALANCE_PATTERN = re.compile(
    r"(?:(?:现在|目前|账户|券商|IBKR).{0,16})?"
    r"(?:还有|可用|余额).{0,24}(?:现金|资金)|(?:全部可用现金|全部可用资金)",
    re.IGNORECASE,
)
_USE_ALL_PATTERN = re.compile(
    r"(?:全部用于|全部用来|全部拿来|(?:现金|资金)全部|剩余(?:现金|资金)全部)",
    re.IGNORECASE,
)


def _normalize_explicit_user_semantics(
    extraction: TradeIntentExtraction,
    user_message: str,
) -> TradeIntentExtraction:
    """Correct narrow, text-provable facts before public-contract validation.

    This normalization never consults environment, portfolio, broker, market,
    or execution data. It only upgrades facts directly evidenced by the current
    user message and distinguishes available-balance instructions from a fixed
    amount deliberately set aside for a trade.
    """
    normalized = extraction.model_copy(deep=True)

    if (
        normalized.funding_source.value == "CASH"
        and _EXPLICIT_CASH_PATTERN.search(user_message)
    ):
        normalized.funding_source.provenance = ExtractionProvenance.USER_EXPLICIT

    if (
        normalized.funding_currency.value == "USD"
        and _EXPLICIT_USD_PATTERN.search(user_message)
    ):
        normalized.funding_currency.provenance = ExtractionProvenance.USER_EXPLICIT

    if (
        _AVAILABLE_BALANCE_PATTERN.search(user_message)
        and _USE_ALL_PATTERN.search(user_message)
    ):
        normalized.budget_mode.value = "ALL_AVAILABLE_CASH"
        normalized.budget_mode.provenance = ExtractionProvenance.AI_INFERRED
        normalized.budget_mode.source_text = normalized.budget_mode.source_text or "全部可用现金"

    return normalized


def _provenance(raw: ExtractionProvenance) -> FieldProvenance:
    if raw == ExtractionProvenance.USER_EXPLICIT:
        return FieldProvenance.USER_EXPLICIT
    if raw == ExtractionProvenance.AI_INFERRED:
        return FieldProvenance.AI_INFERRED
    return FieldProvenance.NOT_PROVIDED


def _resolution(raw: ExtractionProvenance) -> FieldResolutionStatus:
    if raw == ExtractionProvenance.MISSING:
        return FieldResolutionStatus.MISSING
    if raw == ExtractionProvenance.AMBIGUOUS:
        return FieldResolutionStatus.AMBIGUOUS
    return FieldResolutionStatus.RESOLVED


def _enum_field(raw: ExtractedEnumField, *, upper: bool = True) -> TradeIntentField:
    value = raw.value
    if isinstance(value, str):
        value = value.strip()
        if upper:
            value = value.upper()
    return TradeIntentField(
        value=value,
        provenance=_provenance(raw.provenance),
        source_text=raw.source_text,
        status=_resolution(raw.provenance),
    )


def _text_field(raw: ExtractedTextField) -> TradeIntentField:
    value = raw.value.strip() if isinstance(raw.value, str) else raw.value
    return TradeIntentField(
        value=value,
        provenance=_provenance(raw.provenance),
        source_text=raw.source_text,
        status=_resolution(raw.provenance),
    )


def _money_field(raw: ExtractedMoneyField) -> TradeIntentField:
    value = None
    if raw.amount is not None:
        value = {
            "amount": raw.amount,
            "currency": raw.currency.upper() if raw.currency else None,
        }
    return TradeIntentField(
        value=value,
        provenance=_provenance(raw.provenance),
        source_text=raw.source_text,
        status=_resolution(raw.provenance),
    )


def _add_issue(
    intent: StructuredTradeIntent,
    *,
    code: str,
    path: str,
    status: FieldResolutionStatus,
    message: str,
    blocking: bool = True,
) -> None:
    intent.issues.append(TradeIntentIssue(
        code=code,
        field_path=path,
        status=status,
        message=message,
        blocking=blocking,
    ))


def _mark_required_field(
    intent: StructuredTradeIntent,
    field_name: str,
    allowed: set[str],
) -> None:
    field = getattr(intent, field_name)
    if field.value is None or field.status in {
        FieldResolutionStatus.MISSING,
        FieldResolutionStatus.AMBIGUOUS,
    }:
        _add_issue(
            intent,
            code=f"{field_name}_unresolved",
            path=field_name,
            status=field.status,
            message=f"{field_name} 缺失或存在多种解释",
        )
        return
    if str(field.value).upper() not in allowed:
        field.status = FieldResolutionStatus.UNSUPPORTED_FOR_V3_15_V1
        _add_issue(
            intent,
            code=f"{field_name}_unsupported",
            path=field_name,
            status=field.status,
            message=f"{field_name}={field.value} 不在 v3.15 v1 支持范围",
        )


def build_and_validate_intent(extraction: TradeIntentExtraction) -> StructuredTradeIntent:
    """Convert strict provider output into the public contract and validate it."""
    intent = StructuredTradeIntent(
        broker=_enum_field(extraction.broker),
        account=_text_field(extraction.account),
        funding_source=_enum_field(extraction.funding_source),
        funding_currency=_enum_field(extraction.funding_currency),
        budget_mode=_enum_field(extraction.budget_mode),
        stated_cash=_money_field(extraction.stated_cash),
        venue=_enum_field(extraction.venue),
        trading_currency=_enum_field(extraction.trading_currency),
        share_class=_enum_field(extraction.share_class),
        side=_enum_field(extraction.side),
        order_type=_enum_field(extraction.order_type),
    )

    for index, raw_leg in enumerate(extraction.legs, start=1):
        intent.legs.append(TradeIntentLeg(
            sequence=index,
            alias=_text_field(raw_leg.alias),
            allocation_mode=_enum_field(raw_leg.allocation_mode),
            target_amount=_money_field(raw_leg.target_amount),
            venue_override=_enum_field(raw_leg.venue_override),
            trading_currency_override=_enum_field(raw_leg.trading_currency_override),
            share_class_override=_enum_field(raw_leg.share_class_override),
        ))

    for field_name, allowed in {
        "broker": {"IBKR"},
        "funding_source": {"CASH"},
        "funding_currency": {"USD"},
        "budget_mode": {"ALL_AVAILABLE_CASH", "FIXED_TOTAL"},
        "venue": {"LSE", "LSEETF"},
        "trading_currency": {"USD"},
        "share_class": {"ACC"},
        "side": {"BUY"},
        "order_type": {"LIMIT"},
    }.items():
        _mark_required_field(intent, field_name, allowed)

    # Account identity is never inferred. Phase 1 may preserve only an account
    # identifier the user actually typed; config/broker selection belongs to
    # Phase 2 and is deliberately unavailable to this parser.
    if intent.account.provenance != FieldProvenance.USER_EXPLICIT:
        intent.account = unresolved_field()

    if intent.account.value is None:
        _add_issue(
            intent,
            code="account_deferred_to_phase2",
            path="account",
            status=FieldResolutionStatus.MISSING,
            message="用户未指定账户；账户选择与验证属于 Phase 2",
            blocking=False,
        )

    if not intent.legs:
        _add_issue(
            intent,
            code="legs_missing",
            path="legs",
            status=FieldResolutionStatus.MISSING,
            message="交易意图中没有可识别的标的",
        )

    seen_aliases: set[str] = set()
    remainder_count = 0
    for index, leg in enumerate(intent.legs):
        prefix = f"legs[{index}]"
        alias = str(leg.alias.value or "").upper()
        if not alias:
            _add_issue(
                intent,
                code="leg_alias_missing",
                path=f"{prefix}.alias",
                status=FieldResolutionStatus.MISSING,
                message="标的 alias 缺失",
            )
        elif alias in seen_aliases:
            leg.alias.status = FieldResolutionStatus.CONFLICTING
            _add_issue(
                intent,
                code="duplicate_leg_alias",
                path=f"{prefix}.alias",
                status=FieldResolutionStatus.CONFLICTING,
                message=f"标的 {alias} 重复且可能存在矛盾约束",
            )
        else:
            seen_aliases.add(alias)

        mode = str(leg.allocation_mode.value or "").upper()
        if mode == "REMAINDER":
            remainder_count += 1
            if leg.target_amount.value is not None:
                leg.target_amount.status = FieldResolutionStatus.CONFLICTING
                _add_issue(
                    intent,
                    code="remainder_has_target_amount",
                    path=f"{prefix}.target_amount",
                    status=FieldResolutionStatus.CONFLICTING,
                    message="REMAINDER 不得带有预先计算的 target_amount",
                )
        elif mode == "APPROX_AMOUNT":
            money = leg.target_amount.value
            amount = money.get("amount") if isinstance(money, dict) else None
            currency = money.get("currency") if isinstance(money, dict) else None
            if not isinstance(amount, (int, float)) or amount <= 0:
                leg.target_amount.status = FieldResolutionStatus.MISSING
                _add_issue(
                    intent,
                    code="target_amount_missing",
                    path=f"{prefix}.target_amount",
                    status=FieldResolutionStatus.MISSING,
                    message=f"{alias or '该标的'} 缺少可确认的目标金额",
                )
            elif str(currency).upper() != "USD":
                leg.target_amount.status = FieldResolutionStatus.UNSUPPORTED_FOR_V3_15_V1
                _add_issue(
                    intent,
                    code="target_currency_unsupported",
                    path=f"{prefix}.target_amount",
                    status=FieldResolutionStatus.UNSUPPORTED_FOR_V3_15_V1,
                    message=f"{alias or '该标的'} 目标金额不是 USD",
                )
        else:
            if leg.allocation_mode.status == FieldResolutionStatus.RESOLVED:
                leg.allocation_mode.status = FieldResolutionStatus.AMBIGUOUS
            _add_issue(
                intent,
                code="allocation_unresolved",
                path=f"{prefix}.allocation_mode",
                status=leg.allocation_mode.status,
                message=f"{alias or '该标的'} 的资金分配缺失或不明确",
            )

        for override_name, global_name in (
            ("venue_override", "venue"),
            ("trading_currency_override", "trading_currency"),
            ("share_class_override", "share_class"),
        ):
            override = getattr(leg, override_name)
            global_field = getattr(intent, global_name)
            if (
                override.value is not None
                and global_field.value is not None
                and str(override.value).upper() != str(global_field.value).upper()
            ):
                override.status = FieldResolutionStatus.CONFLICTING
                _add_issue(
                    intent,
                    code="conflicting_constraint",
                    path=f"{prefix}.{override_name}",
                    status=FieldResolutionStatus.CONFLICTING,
                    message=(
                        f"{alias or '该标的'} 的 {override_name}={override.value} "
                        f"与全局 {global_name}={global_field.value} 冲突"
                    ),
                )

    if remainder_count > 1:
        _add_issue(
            intent,
            code="multiple_remainder_legs",
            path="legs",
            status=FieldResolutionStatus.CONFLICTING,
            message="同一交易意图不能包含多个 REMAINDER leg",
        )

    for ambiguity in extraction.ambiguities:
        _add_issue(
            intent,
            code="provider_ambiguity",
            path="intent",
            status=FieldResolutionStatus.AMBIGUOUS,
            message=ambiguity,
        )

    unsupported_messages = {
        UnsupportedFeature.SELL: "v3.15 v1 不支持 SELL / REDUCE",
        UnsupportedFeature.MARKET_ORDER: "v3.15 v1 不支持市价单",
        UnsupportedFeature.NON_USD_FUNDING: "v3.15 v1 不支持非 USD 资金",
        UnsupportedFeature.FRACTIONAL_SHARES: "v3.15 v1 不支持碎股",
        UnsupportedFeature.NON_LSE_VENUE: "v3.15 v1 仅支持 LSE 交易线意图",
        UnsupportedFeature.NON_ACC_SHARE_CLASS: "v3.15 v1 仅支持 Acc 份额类型意图",
        UnsupportedFeature.OTHER: "请求包含 v3.15 v1 不支持的执行特性",
    }
    for feature in extraction.unsupported_features:
        _add_issue(
            intent,
            code=f"unsupported_{feature.value.lower()}",
            path="intent",
            status=FieldResolutionStatus.UNSUPPORTED_FOR_V3_15_V1,
            message=unsupported_messages[feature],
        )

    if any(
        issue.status == FieldResolutionStatus.UNSUPPORTED_FOR_V3_15_V1
        for issue in intent.issues
    ):
        intent.readiness = IntentReadiness.UNSUPPORTED_FOR_V3_15_V1
        intent.confirmation_status = IntentConfirmationStatus.BLOCKED
    elif any(issue.blocking for issue in intent.issues):
        intent.readiness = IntentReadiness.NEEDS_REVIEW
        intent.confirmation_status = IntentConfirmationStatus.BLOCKED
    else:
        intent.readiness = IntentReadiness.READY_FOR_CONFIRMATION
        intent.confirmation_status = IntentConfirmationStatus.PENDING

    return intent


def _failed_intent(message: str) -> StructuredTradeIntent:
    field = unresolved_field()
    intent = StructuredTradeIntent(
        broker=field.model_copy(deep=True),
        account=field.model_copy(deep=True),
        funding_source=field.model_copy(deep=True),
        funding_currency=field.model_copy(deep=True),
        budget_mode=field.model_copy(deep=True),
        stated_cash=field.model_copy(deep=True),
        venue=field.model_copy(deep=True),
        trading_currency=field.model_copy(deep=True),
        share_class=field.model_copy(deep=True),
        side=field.model_copy(deep=True),
        order_type=field.model_copy(deep=True),
        readiness=IntentReadiness.PARSE_FAILED,
        confirmation_status=IntentConfirmationStatus.BLOCKED,
    )
    _add_issue(
        intent,
        code="parser_failed",
        path="intent",
        status=FieldResolutionStatus.AMBIGUOUS,
        message=message,
    )
    return intent


def parse_trade_intent(
    user_message: str,
    conversation_context: list[dict] | None = None,
    provider: StructuredTradeIntentProvider | None = None,
) -> StructuredTradeIntent | None:
    """Parse only explicit/actionable candidates; failures remain blocked."""
    if not is_actionable_trade_candidate(user_message):
        return None

    try:
        extraction = (provider or OpenAIStructuredTradeIntentProvider()).extract(
            user_message,
            conversation_context or [],
        )
        if not extraction.is_trade_intent:
            return None
        normalized = _normalize_explicit_user_semantics(extraction, user_message)
        return build_and_validate_intent(normalized)
    except Exception as exc:
        logger.warning("[TradeIntentParser] fail-closed: %s", exc)
        return _failed_intent("交易意图解析失败，未生成任何可执行数据")
