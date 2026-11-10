"""Independent Fraction oracle and frozen, no-I/O C regression fixtures."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace

import pytest

from backend.services.investable_assets import project_investable_assets
from backend.services.instruments.etf_holdings import ETFHoldings, ETF_IDENTITIES
from backend.services.investment_policy.concentration import analyze_concentration
from backend.services.investment_policy.contracts import digest
from backend.services.investment_policy.parser import PolicyError
from tests.test_investable_assets import AT as BASE_AT, view  # noqa: F401
from tests.test_investment_policy import POLICY, snapshot

ALPHABET = "SEC_CIK_0001652044"
APPLE = "SEC_CIK_0000320193"
TESLA = "SEC_CIK_0001318605"
D = Decimal
AT = BASE_AT.replace(hour=22)
STAMP = AT.isoformat()


def project(view, *, at=AT):
    return project_investable_assets(view, portfolio_id=7, snapshot_id=view["snapshot_id"],
                                     snapshot_sha256=digest(view), at=at)


@pytest.fixture
def active():
    raw = POLICY.decode().replace("超过10%", "超过6%").replace("SYN1", "AAPL")
    raw = raw.replace("超过12%", "超过7%").replace("超过15%", "超过8%")
    raw = raw.replace("严格超过5% | WARNING", "严格超过3% | WARNING")
    raw = raw.replace("达到或超过6% | HUMAN_REVIEW", "达到或超过4% | HUMAN_REVIEW")
    policy = snapshot(raw.encode())
    ref = dict(bundle_id="synthetic", bundle_version=1, bundle_sha256="a"*64,
               structured_sha256=digest(policy), scope="portfolio:7")
    return SimpleNamespace(portfolio_id=7, context=dict(mode="POLICY_ACTIVE", status="ACTIVE",
        snapshot=policy.model_dump(mode="json"), policy_reference=ref, effective_at=STAMP))


@pytest.fixture
def frozen(view):
    q = view["data_quality"]
    view["captured_at"] = STAMP
    q["accounts"][0]["account_synced_at"] = STAMP
    q["investable_scope_evidence"]["as_of"] = STAMP
    for receipt in q["investable_scope_evidence"]["coverage"].values():
        receipt["as_of"] = STAMP
    q["security_inputs"][0].update(cash_as_of=STAMP, economic_asset_class="CASH", classification_source="fixture:cash",
        classification_verification_status="VERIFIED", classification_version="canonical-v1")
    q["investable_owner_evidence"] = dict(sha256="b"*64, confirmed_at=STAMP,
                                         valid_until=(AT+timedelta(days=1)).isoformat())
    return view


def position(view, symbol, amount, *, vehicle="COMMON_STOCK", currency="USD", other_account=False):
    q = view["data_quality"]
    items = q["security_inputs"]
    ident = len(items) + 1
    account = q["accounts"][0]
    if other_account:
        account = dict(account, account_ref="account_fixture_2")
        if account not in q["accounts"]:
            q["accounts"].append(account)
    row = dict(id=ident, symbol=symbol, vehicle_type=vehicle, currency=currency,
        economic_asset_class="EQUITY", classification_source="fixture:listing",
        classification_verification_status="VERIFIED", classification_version="canonical-v1",
        market_value_cny=str(amount), account_ref=account["account_ref"], positions_as_of=STAMP,
        price_as_of=AT.date().isoformat(), price_time_basis="close",
        fx_quality="current", fx_rate_to_cny="7", fx_rate_date=AT.date().isoformat())
    items.append(row)
    q["security_inputs"][0]["market_value_cny"] = str(D(q["security_inputs"][0]["market_value_cny"]) - D(amount))
    evidence = q["investable_scope_evidence"]
    for domain in ("accounts", "deduplication"):
        evidence["coverage"][domain]["asset_refs"].append(f"position:{ident}")
    evidence["dispositions"][f"position:{ident}"] = dict(state="INCLUDED", reason="FREE_INVESTMENT",
                                                         evidence_refs=["fixture:purpose"])
    return row


def btc(view, amount, *, supported=True):
    q = view["data_quality"]
    items = view["views"]["get_manual_wealth_items"]["items"]
    ident = len(items) + 1
    ref = f"wealth:{ident}"
    items.append(dict(id=ident, kind="asset", item_type="other_asset", currency="CNY",
                      value_as_of=AT.date().isoformat(), last_verified_at=STAMP, current_value=str(amount)))
    q["security_inputs"][0]["market_value_cny"] = str(D(q["security_inputs"][0]["market_value_cny"]) - D(amount))
    receipt = q["investable_scope_evidence"]
    for domain in ("manual_assets", "deduplication", "btc"):
        receipt["coverage"][domain]["status"] = "PRESENT"
        receipt["coverage"][domain]["asset_refs"].append(ref)
    receipt["coverage"]["btc"]["evidence_refs"].append(f"{ref}:BTC_SPOT_UNLEVERED")
    if supported:
        receipt["coverage"]["btc"]["evidence_refs"].append(f"{ref}:BTC_SPOT_UNLEVERED:source:fixture:spot")
    receipt["dispositions"][ref] = dict(state="INCLUDED", reason="FREE_INVESTMENT", evidence_refs=["fixture:purpose"])
    q["investable_owner_evidence"].setdefault("manual_classes", {})[ref] = {
        "economic_asset_class":"ALTERNATIVE", "evidence_refs":["fixture:class"]}


def holdings(symbol, weights, **changes):
    raw = dict(symbol=symbol, isin=ETF_IDENTITIES[symbol], holdings_as_of=AT.date(),
               known_at=AT, valid_until=AT+timedelta(hours=1), source="https://example.invalid/synthetic",
               source_sha256="c"*64, parser_version="synthetic-v1", weight_basis="NONNEGATIVE_EQUITY_NAV",
               rows=[dict(symbol=k, currency="USD", weight_lower_bound=v) for k,v in weights.items()])
    return ETFHoldings.model_validate({**raw, **changes})


def run(view, active, evidence=None, pins=None, *, at=AT, projection=None):
    evidence = evidence or {}
    if pins is None:
        pins = {key:digest(record) for key,record in evidence.items()}
    return analyze_concentration(view, projection=projection or project(view, at=at), session=active,
                                 etf_evidence=evidence, evidence_pins=pins, at=at)


def test_cross_account_issuer_and_etf_math_against_fraction_oracle(frozen, active):
    for sym, amt, kwargs in [
        ("GOOG:US",20,{}), ("GOOGL:US",15,{}), ("GOOG:US",5,{"other_account":True}),
        ("LI:US",15,{"vehicle":"ADR"}), ("2015:HK",20,{"currency":"HKD"}),
        ("AAPL:US",50,{}),
    ]:
        position(frozen, sym, amt, **kwargs)
    etfs = {
        "SPY:US": (200, {"GOOG:US":".02","GOOGL:US":".03","AAPL:US":".05"}),
        "QQQ:US": (200, {"GOOG:US":".04","GOOGL:US":".06","AAPL:US":".06"}),
        "SPMO:US": (100, {"GOOGL:US":".10","AAPL:US":".04"}),
    }
    evidence = {}
    for sym,(amount,weights) in etfs.items():
        position(frozen, sym, amount, vehicle="ETF")
        evidence[sym] = holdings(sym, weights)
    before = deepcopy(frozen)
    value = run(frozen, active, evidence)
    assert frozen == before and value["denominator_cny"] == 1000 and not value["execution_authorized"]
    alpha = value["issuers"][ALPHABET]
    apple = value["issuers"][APPLE]
    li = value["issuers"]["LI_AUTO"]
    assert len(alpha["direct_members"]) == 3
    assert len({m["account_ref"] for m in alpha["direct_members"]}) == 2
    assert {m["currency"] for m in li["direct_members"]} == {"USD","HKD"}
    assert li["direct_cny"] == 35
    for row, direct, tickers in [(alpha,40,("GOOG:US","GOOGL:US")), (apple,50,("AAPL:US",))]:
        expected = sum(Fraction(amount) * Fraction(weights.get(k,"0"))
                       for amount,weights in etfs.values() for k in tickers)
        assert Fraction(row["indirect_known_cny"]) == expected
        assert sum(Fraction(member["known_cny"]) for member in row["indirect_members"]) == expected
        assert Fraction(row["known_lower_bound_pct"]) == (direct + expected) / 10
        assert row["proven_upper_bound_pct"] is None and row["unknown_coverage"]
        critical = Fraction(direct + expected) / Fraction(8,100)
        assert Fraction(row["repayment_threshold"]["i_critical_cny"]) == critical
        assert Fraction(row["repayment_threshold"]["r_trigger_cny"]) == max(0, 1000-critical)
    assert alpha["risk_status"] == "HUMAN_REVIEW" and alpha["repayment_threshold"]["already_triggered"]
    assert apple["risk_status"] == "WARNING"
    assert li["risk_status"] == "UNKNOWN"
    assert value["unresolved_principal_cny"] == 434


@pytest.mark.parametrize("symbol,key,amount,risk", [
    ("TSLA:US",TESLA,"60","SAFE_BOUNDED"), ("TSLA:US",TESLA,"60.00001","WARNING"),
    ("TSLA:US",TESLA,"79.99999","WARNING"), ("TSLA:US",TESLA,"80","HUMAN_REVIEW"),
    ("AAPL:US",APPLE,"65","SAFE_BOUNDED"), ("AAPL:US",APPLE,"70","SAFE_BOUNDED"),
    ("AAPL:US",APPLE,"70.00001","WARNING"), ("AAPL:US",APPLE,"80","HUMAN_REVIEW"),
])
def test_exact_issuer_boundaries(frozen, active, symbol, key, amount, risk):
    position(frozen, symbol, amount)
    row = run(frozen, active)["issuers"][key]
    assert row["risk_status"] == risk
    assert row["known_lower_bound_pct"] == row["proven_upper_bound_pct"] == D(amount)/10


@pytest.mark.parametrize("amount,risk", [("30","SAFE_BOUNDED"),("30.00001","WARNING"),
                                        ("39.99999","WARNING"),("40","HUMAN_REVIEW")])
def test_btc_spot_policy_thresholds(frozen, active, amount, risk):
    btc(frozen, "10")
    btc(frozen, D(amount)-10)
    result = run(frozen, active)["btc"]
    assert result["known_spot_cny"] == D(amount) and result["risk_status"] == risk


def test_btc_marker_and_wrapper_cannot_prove_spot(frozen, active):
    btc(frozen, "39", supported=False)
    result = run(frozen, active)["btc"]
    assert result["known_spot_cny"] == 0 and result["risk_status"] == "UNKNOWN"


def test_unknown_denominator_retains_only_conditional_risk(frozen, active):
    position(frozen, "TSLA:US", "80")
    frozen["data_quality"]["investable_scope_evidence"]["coverage"]["btc"]["status"] = "UNKNOWN"
    value = run(frozen, active)
    assert value["status"] == "CONDITIONAL_RISK"
    assert value["denominator_cny"] is None and "DENOMINATOR_UNVERIFIED" in value["issues"]
    row = value["issuers"][TESLA]
    assert row["direct_cny"] == 80 and row["risk_status"] == "CONDITIONAL_RISK"
    assert row["known_lower_bound_pct"] is None and row["proven_upper_bound_pct"] is None
    assert not row["policy_hits"] and row["repayment_threshold"] is None


@pytest.mark.parametrize("reason", ["STALE","VERSION_UNVERIFIED","NOT_YET_KNOWN","VALUATION_DATE_MISMATCH"])
def test_etf_date_version_or_expiry_cannot_supply_formal_indirect(frozen, active, reason):
    position(frozen, "TSLA:US", "80")
    position(frozen, "QQQ:US", "300", vehicle="ETF")
    kwargs = {}
    at = AT+timedelta(seconds=1)
    if reason == "STALE":
        kwargs["valid_until"] = AT
    if reason == "NOT_YET_KNOWN":
        kwargs["known_at"] = AT+timedelta(seconds=5)
    if reason == "VALUATION_DATE_MISMATCH":
        kwargs["holdings_as_of"] = (AT-timedelta(days=1)).date()
    rec = holdings("QQQ:US", {"TSLA:US":".1"}, **kwargs)
    pins = {"QQQ:US":"d"*64} if reason == "VERSION_UNVERIFIED" else None
    value = run(frozen, active, {"QQQ:US":rec}, pins, at=at)
    assert value["etfs"]["position:3"]["freshness_status"] == reason
    row = value["issuers"][TESLA]
    assert row["risk_status"] == "HUMAN_REVIEW" and row["indirect_known_cny"] == 0
    assert row["proven_upper_bound_pct"] is None


def test_exact_coverage_is_an_upper_bound_only_when_resolved(frozen, active):
    position(frozen, "SPY:US", "50", vehicle="ETF")
    for symbol, upper in [("MSFT:US",D(5)), ("UNMAPPED:US",None)]:
        rec = holdings("SPY:US", {"AAPL:US":".9",symbol:".1"}, scope="COMPLETE_UNLEVERED")
        result = run(frozen, active, {"SPY:US":rec})["issuers"][APPLE]
        assert result["proven_upper_bound_pct"] == (D("4.5") if upper else None)
        assert result["risk_status"] == ("SAFE_BOUNDED" if upper else "UNKNOWN")


def test_pins_for_snapshot_and_active_bundle_fail_closed(frozen, active):
    position(frozen, "TSLA:US", "80")
    receipt = project(frozen)
    altered = deepcopy(frozen)
    altered["data_quality"]["security_inputs"][1]["symbol"] = "AAPL:US"
    with pytest.raises(ValueError, match="SNAPSHOT_BINDING_MISMATCH"):
        run(altered, active, projection=receipt)
    active.context["policy_reference"]["structured_sha256"] = "c"*64
    with pytest.raises(PolicyError, match="POLICY_BINDING_MISMATCH"):
        run(frozen, active)


def test_duplicate_security_alias_or_total_weight_over_nav_refused():
    for weights in ({"BRK.B:US":".1","BRK-B:US":".1"},{"2015:HK":".1","02015:HK":".1"},
                    {"TSLA:US":".6","AAPL:US":".6"}):
        with pytest.raises(ValueError, match="ETF_DUPLICATE_OR_EXCESS_WEIGHT"):
            holdings("SPY:US", weights)


def test_partial_evidence_cannot_prove_upper_bound_even_if_reported_weights_close(frozen, active):
    position(frozen, "SPY:US", "50", vehicle="ETF")
    rec = holdings("SPY:US", {"AAPL:US":".9", "MSFT:US":".1"})
    row = run(frozen, active, {"SPY:US":rec})["issuers"][APPLE]
    assert row["proven_upper_bound_pct"] is None and row["risk_status"] == "UNKNOWN"


def test_official_excerpt_provenance_and_coverage():
    import json
    from pathlib import Path

    path = Path(__file__).with_name("fixtures") / "c_official_etf_core_2026-11-06.json"
    records = [ETFHoldings.model_validate(value) for value in json.loads(path.read_bytes())]
    expected = {"SPY:US": (7, D(".24157423")), "QQQ:US": (6, D(".27110535")),
                "SPMO:US": (3, D(".20021618"))}
    assert set(expected) == {r.symbol for r in records}
    for rec in records:
        count, coverage = expected[rec.symbol]
        assert rec.scope == "PARTIAL" and rec.valid_until == rec.known_at
        assert len(rec.rows) == count and rec.coverage == coverage


def test_diagnostic_does_not_reach_broker_orders_or_asset_preparation(frozen, active, monkeypatch):
    from backend.services.action.order_manager import OrderManager
    from backend.services.asset_analysis import AssetAnalysisPreparation
    from backend.services.restricted_sync import SyncExecutor

    def forbidden(*args, **kwargs):
        raise AssertionError("risk must not use a write or synchronization entry")

    monkeypatch.setattr(OrderManager, "place_order", forbidden)
    monkeypatch.setattr(AssetAnalysisPreparation, "run", forbidden)
    monkeypatch.setattr(SyncExecutor, "execute", forbidden)
    position(frozen, "TSLA:US", 80)
    assert run(frozen, active)["issuers"][TESLA]["risk_status"] == "HUMAN_REVIEW"


def test_older_projection_cannot_carry_expired_position_qualification(frozen, active):
    row = position(frozen, "TSLA:US", 80)
    row["positions_as_of"] = (AT-timedelta(seconds=299)).isoformat()
    receipt = project(frozen)
    assert receipt.status == "QUALIFIED"
    assert run(frozen, active, projection=receipt)["expires_at"] == AT+timedelta(seconds=1)
    later = AT+timedelta(seconds=2)
    assert project(frozen, at=later).status == "UNKNOWN"
    value = run(frozen, active, at=later, projection=receipt)
    assert value["issuers"][TESLA]["risk_status"] == "CONDITIONAL_RISK"
    assert value["denominator_cny"] is None


@pytest.mark.parametrize("symbol,currency,expected", [
    ("PDD:US","USD","PDD_HOLDINGS"), ("3690:HK","HKD","MEITUAN"), ("XPEV:US","USD","XPENG"),
    ("MSFT:US","USD","SEC_CIK_0000789019"), ("META:US","USD","SEC_CIK_0001326801"),
    ("BRK.B:US","USD","SEC_CIK_0001067983"), ("BRK-B:US","USD","SEC_CIK_0001067983"),
])
def test_issuer_owner_additions_and_original_china_map(symbol, currency, expected):
    from backend.services.instruments.issuers import direct_issuer
    row = dict(symbol=symbol, currency=currency, vehicle_type="COMMON_STOCK", economic_asset_class="EQUITY")
    assert direct_issuer(row) == expected
    assert direct_issuer(dict(row, symbol="UNKNOWN:US", name=expected)) is None


def test_near_close_intraday_and_conflicting_etf_symbol_are_not_nav_evidence(frozen, active):
    row = position(frozen, "QQQ:US", 300, vehicle="ETF")
    evidence = {"QQQ:US": holdings("QQQ:US", {"TSLA:US":".1"})}
    row.update(price_as_of=AT.replace(hour=20,minute=56).isoformat(), price_time_basis="intraday")
    result = run(frozen, active, evidence)
    assert result["denominator_cny"] == 1000
    assert result["etfs"]["position:2"]["freshness_status"] == "VALUATION_DATE_UNVERIFIED"
    assert not result["issuers"]
    row.update(price_as_of=AT.date().isoformat(), price_time_basis="close", security_symbol="SPY:US")
    result = run(frozen, active, evidence)
    assert result["etfs"]["position:2"]["freshness_status"] == "UNAVAILABLE"
    assert not result["issuers"]


def test_original_currency_still_identifies_translated_legacy_row():
    from backend.services.instruments.issuers import direct_issuer

    row = dict(symbol="LI:US", security_symbol="LI:US", currency="CNY", original_currency="USD",
               security_currency="CNY", vehicle_type="ADR", economic_asset_class="EQUITY")
    assert direct_issuer(row) == "LI_AUTO"
