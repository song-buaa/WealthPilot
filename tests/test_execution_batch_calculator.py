from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.services.execution_batch.calculator import (
    CashLedger,
    ExecutionSafetyError,
    calculate_fixed_quantity,
    normalize_buy_limit,
    quote_guard,
    select_authoritative_cash,
)
from backend.services.execution_batch.trusted_instruments import (
    EXPECTED_MARKET_RULE_IDS,
    TRUSTED_INSTRUMENTS,
    verify_resolved_instrument,
)


def test_trusted_case1_mapping_is_exact_and_acc_verified():
    assert set(TRUSTED_INSTRUMENTS) == {"IBTA", "VDCA", "CBU0", "IB01"}
    assert {item.con_id for item in TRUSTED_INSTRUMENTS.values()} == {
        272686955, 354532794, 79000139, 354802220,
    }
    assert TRUSTED_INSTRUMENTS["CBU0"].symbol == "CSBGU0"
    assert TRUSTED_INSTRUMENTS["CBU0"].local_symbol == "CBU0"
    assert all(item.share_class == "ACC" for item in TRUSTED_INSTRUMENTS.values())
    assert all(item.verification_status == "VERIFIED" for item in TRUSTED_INSTRUMENTS.values())
    assert EXPECTED_MARKET_RULE_IDS == {
        "IBTA": 1874, "VDCA": 98, "CBU0": 983, "IB01": 1874,
    }


def test_trusted_mapping_rejects_any_conid_mismatch():
    trusted = TRUSTED_INSTRUMENTS["IBTA"].to_dict()
    trusted["con_id"] += 1
    with pytest.raises(ValueError, match="trusted identity mismatch"):
        verify_resolved_instrument("IBTA", trusted)


def test_market_rule_rounds_buy_limit_up_at_correct_tier():
    tiers = [
        {"low_edge": 0, "increment": 0.0001},
        {"low_edge": 1, "increment": 0.0002},
        {"low_edge": 2, "increment": 0.0005},
        {"low_edge": 5, "increment": 0.001},
    ]
    assert normalize_buy_limit(Decimal("5.0001"), tiers) == Decimal("5.001")


def test_fixed_amount_never_exceeds_user_target():
    quantity, notional = calculate_fixed_quantity(
        Decimal("11350"), Decimal("5.123"),
    )
    assert quantity == 2215
    assert notional <= Decimal("11350")


def test_money_accepts_phase1_amount_currency_value_object():
    from backend.services.execution_batch.calculator import money
    assert money({"amount": 16632, "currency": "USD"}) == Decimal("16632")


def test_quantity_zero_is_not_forced_to_one():
    with pytest.raises(ExecutionSafetyError, match="quantity=0"):
        calculate_fixed_quantity(Decimal("1"), Decimal("10"))


def test_cash_authority_is_most_conservative_true_cash_not_buying_power():
    value, source = select_authoritative_cash({
        "CashBalance": 16632,
        "SettledCash": 16620,
        "TotalCashValue": 16640,
        "AvailableFunds": 30000,
        "BuyingPower": 90000,
    })
    assert value == Decimal("16620")
    assert source == "SettledCash"


def test_cash_ledger_does_not_double_subtract_broker_cash():
    ledger = CashLedger(
        initial_cash=Decimal("16632"),
        filled_cost=Decimal("1000"),
        active_reservations=Decimal("500"),
        fee_reserve=Decimal("7"),
        safety_cushion=Decimal("25"),
    )
    assert ledger.remaining == Decimal("15100")
    ledger.consistency_guard(Decimal("15100"))
    with pytest.raises(ExecutionSafetyError, match="低于本地安全账本"):
        ledger.consistency_guard(Decimal("15099"))


def test_quote_guard_rejects_stale_and_abnormal_spread(monkeypatch):
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    monkeypatch.setenv("BATCH_QUOTE_MAX_AGE_SECONDS", "30")
    monkeypatch.setenv("BATCH_MAX_SPREAD_PCT", "0.01")
    with pytest.raises(ExecutionSafetyError, match="stale"):
        quote_guard({
            "quote_quality": "LIVE", "bid": 10, "ask": 10.01,
            "quote_timestamp": (now - timedelta(seconds=31)).isoformat(),
        }, now=now)
    with pytest.raises(ExecutionSafetyError, match="spread"):
        quote_guard({
            "quote_quality": "LIVE", "bid": 9, "ask": 10,
            "quote_timestamp": now.isoformat(),
        }, now=now)
