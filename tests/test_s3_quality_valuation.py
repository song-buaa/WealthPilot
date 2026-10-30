from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

import pytest

from backend.services.market_data.quality_valuation import ResearchRequest, compare, scenario
from backend.services.quality_valuation_research import (
    digest, frozen_inputs, method_hashes, save_bundle, read_bundle, replay, difference, view_hashes, views,
)

AT = datetime(2026, 10, 30, tzinfo=timezone.utc)


def params(growth=".08", multiple="25"):
    return dict(basis="S3_RESEARCH_ASSUMPTION_NOT_POLICY", rationale="Explicit synthetic assumption, not a forecast or policy.", required_return=".1",
                **{name: dict(earnings_growth=growth, annual_share_change="0", terminal_pe=multiple,
                              dividend_payout=".1") for name in ("cautious", "base", "optimistic")})


def request():
    return dict(pool_id="synthetic", pool_version="v1", purpose="Offline contract test",
                decision_at=AT.isoformat(), source_manifest_sha256="a" * 64,
                companies={"AAA:US": params(), "BBB:US": params()})


def assessment(symbol="AAA:US", eps=2):
    # Explicit synthetic qualified-Owner output, never a real source fixture.
    rows = []
    for start, end in [("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31"),
                       ("2026-01-01", "2026-03-31"), ("2026-04-01", "2026-06-30")]:
        field = "diluted_eps_ordinary_quarter"
        rows.append(dict(kind="fundamental_field", symbol=symbol, field=field, period=end, fiscal_period_start=start,
                         scope="quarterly", quality="valid", is_seed=False, is_mock=False, currency="USD",
                         unit="currency_per_ordinary_share", issuer_type="domestic", ordinary_shares_per_ads=None,
                         accounting_basis="diluted_eps_ordinary", record_id=f"test-{symbol}-{end}",
                         published_at="2026-08-01T00:00:00+00:00", fetched_at="2026-10-01T00:00:00+00:00",
                         historical_only=end != "2026-06-30", values={field: eps}, publication_links=[dict(
                             source_document_sha256="a" * 64, source_url="https://www.sec.gov/example",
                             published_at="2026-08-01T00:00:00+00:00", fetched_at="2026-10-01T00:00:00+00:00")]))
    return dict(symbol=symbol, as_of=AT.isoformat(), verified_field_records=rows[-1:],
                historical_field_records=rows[:-1], conflicts=[], source_conflicts=[], records=[],
                technical_levels={"day": dict(record_id="test-price", currency="USD", end_at="2026-10-29",
                                               bars=[dict(close=100.)])})


def test_s3_real_computation_is_repeatable_and_does_not_mutate_owner_input():
    source = {s: assessment(s) for s in request()["companies"]}
    before = deepcopy(source)
    result = compare(request(), source)
    assert result == compare(request(), source) and source == before
    assert [c["rank"] for c in result["cards"]] == [1, 1]
    assert all(c["group"] != "优先考虑" for c in result["cards"])
    assert all(c["quality"]["capital_efficiency"]["roic"]["status"] == "UNKNOWN" for c in result["cards"])
    assert not result["execution_authorized"] and not result["order_created"]


@pytest.mark.parametrize("fault", ["currency", "unit", "period", "future", "source", "conflict", "identity", "basis"])
def test_reject_incompatible_facts_locally_without_losing_other_company(fault):
    source = {s: assessment(s) for s in request()["companies"]}
    bad = source["AAA:US"]
    row = bad["verified_field_records"][0]
    if fault == "currency": row["currency"] = "CNY"
    elif fault == "unit": row["unit"] = "million_USD"
    elif fault == "period": row["fiscal_period_start"] = "2026-07-01"
    elif fault == "future": row["published_at"] = "2027-01-01T00:00:00+00:00"
    elif fault == "source": row["publication_links"] = []
    elif fault == "conflict": bad["conflicts"] = ["FINANCIAL_SOURCE_CONFLICT"]
    elif fault == "identity": bad["symbol"] = "OTHER:US"
    else: row["accounting_basis"] = "made_up_eps"
    result = compare(request(), source)
    assert result["cards"][0].get("valuation_status") in {"UNKNOWN", "NOT_APPLICABLE_NONPOSITIVE_EPS"}
    assert result["cards"][1]["scenarios"]


@pytest.mark.parametrize("fault", ["missing", "negative", "missing_quarter", "wrong_span"])
def test_no_fake_multiples_when_method_not_applicable(fault):
    a = assessment()
    if fault == "missing": a["verified_field_records"] = []
    elif fault == "negative":
        for row in a["verified_field_records"] + a["historical_field_records"]:
            row["values"]["diluted_eps_ordinary_quarter"] = -1
    elif fault == "missing_quarter": a["historical_field_records"].pop()
    else: a["historical_field_records"][0]["fiscal_period_start"] = "2025-06-01"
    card = compare(request(), {"AAA:US": a})["cards"][0]
    assert not card["scenarios"] and card["group"] == "暂不优先"
    if fault != "negative":
        assert card["quality"]["floors"]["data_trust"]["status"] == "暂无法判断"


def test_earnings_share_dividend_identity_and_discounting():
    case = dict(earnings_growth="0", annual_share_change="0", terminal_pe="10", dividend_payout=".5")
    r = scenario(10, 100, case, ".1")
    assert Decimal(r["terminal_price"]) == 100
    assert Decimal(r["dividends_cash"]) == 25
    assert Decimal(r["terminal_wealth"]) == 125
    assert abs(Decimal(r["conditional_value"]) - sum(Decimal(5) / Decimal('1.1') ** t for t in range(1, 6)) - Decimal(100) / Decimal('1.1') ** 5) < Decimal('.00000001')
    diluted = scenario(10, 100, dict(case, annual_share_change=".1"), ".1")
    assert Decimal(diluted["terminal_price"]) < 100
    assert "buyback_return" not in diluted


def test_assumption_changes_recompute_rank_and_sensitive_ties_are_exposed():
    a = {s: assessment(s) for s in request()["companies"]}
    one = compare(request(), a)
    altered = request()
    altered["companies"]["BBB:US"] = params(".3", "40")
    two = compare(altered, a)
    assert one["cards"][0]["rank"] == one["cards"][1]["rank"]
    assert two["cards"][1]["rank"] == 1 and two["cards"][0]["rank"] == 2
    assert one["ranking"]["changes"] and not one["ranking"]["stable"]
    assert all(c["group"] != "优先考虑" for c in two["cards"])


def test_all_failed_companies_remain_visible_and_unranked():
    result = compare(request(), {})
    assert len(result["cards"]) == 2
    assert all(c["group"] == "暂不优先" and c["rank"] is None for c in result["cards"])


@pytest.mark.parametrize("fault", ["too_many", "nan", "bad_method", "naive_time", "fake_policy"])
def test_bounded_explicit_research_contract(fault):
    r = request()
    if fault == "too_many": r["companies"] = {f"S{i}:US": params() for i in range(21)}
    elif fault == "nan": r["companies"]["AAA:US"]["required_return"] = "NaN"
    elif fault == "bad_method": r["companies"]["AAA:US"]["method"] = "DCF_FROM_MISSING_CASH"
    elif fault == "naive_time": r["decision_at"] = "2026-10-30"
    else: r["companies"]["AAA:US"]["basis"] = "APPROVED_POLICY"
    with pytest.raises(ValueError): ResearchRequest.model_validate(r)


def bundle():
    r = request()
    a = {s: assessment(s) for s in r["companies"]}
    p = dict(request=ResearchRequest.model_validate(r).model_dump(mode="json"), assessments=a,
             result=compare(r, a), method_files=method_hashes(), source_files={})
    p["view_hashes"] = view_hashes(p["result"])
    return dict(payload=p, artifact_id=digest(p))


def test_immutable_save_reread_replay_diff_and_separate_independent_verifier(tmp_path):
    b = bundle()
    path = save_bundle(tmp_path, b, {})
    assert save_bundle(tmp_path, b, {}) == path
    assert read_bundle(path / "bundle.json") == b
    assert replay(path / "bundle.json")["verified"]
    from scripts.s3_independent_recompute import verify
    assert verify(path / "bundle.json")["calculations_and_base_ranks"] == "PASS"
    changed = deepcopy(b)
    p = changed["payload"]
    p["request"]["companies"]["BBB:US"] = params(".3")
    p["result"] = compare(p["request"], p["assessments"])
    p["view_hashes"] = view_hashes(p["result"])
    changed["artifact_id"] = digest(p)
    new = save_bundle(tmp_path, changed, {})
    d = difference(path / "bundle.json", new / "bundle.json")
    assert d["assumptions_changed"] and not d["data_changed"]
    assert not d["method_changed"] and read_bundle(path / "bundle.json") == b


def test_tampered_result_raw_source_and_method_drift_are_rejected(tmp_path):
    b = bundle()
    source = tmp_path / "original.txt"
    source.write_text("original public evidence")
    b["payload"]["source_files"] = {"original.txt": hashlib.sha256(source.read_bytes()).hexdigest()}
    b["artifact_id"] = digest(b["payload"])
    p = save_bundle(tmp_path / "archive", b, {"original.txt": source})
    (p / "sources" / "original.txt").write_text("changed")
    with pytest.raises(ValueError, match="ARCHIVED_SOURCE_HASH"): read_bundle(p / "bundle.json")
    (p / "sources" / "original.txt").write_bytes(source.read_bytes())
    tampered = deepcopy(b)
    tampered["payload"]["result"]["cards"][0]["rank"] = 99
    (p / "bundle.json").write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match="ARTIFACT_HASH"): read_bundle(p / "bundle.json")
    tampered["artifact_id"] = digest(tampered["payload"])
    for name, body in views(tampered["payload"]["result"]).items():
        (p / name).write_text(body)
    tampered["payload"]["view_hashes"] = view_hashes(tampered["payload"]["result"])
    tampered["artifact_id"] = digest(tampered["payload"])
    (p / "bundle.json").write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match="RECOMPUTATION"): replay(p / "bundle.json")
    tampered["payload"]["method_files"]["old"] = "a" * 64
    tampered["artifact_id"] = digest(tampered["payload"])
    (p / "bundle.json").write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match="METHOD_DRIFT"): replay(p / "bundle.json")


def test_pinned_source_manifest_and_shared_cutoff_required(tmp_path):
    (tmp_path / "P1_ACCEPTANCE_MANIFEST.json").write_text('{}')
    with pytest.raises(ValueError, match="SOURCE_HASH"): frozen_inputs(tmp_path, request())
    r = request()
    r["source_manifest_sha256"] = hashlib.sha256(b'{}').hexdigest()
    with pytest.raises(KeyError): frozen_inputs(tmp_path, r)
