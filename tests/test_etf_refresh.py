from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from openpyxl import Workbook
import pytest

from backend.services.instruments import etf_refresh as refresh
from backend.services.investment_policy import concentration_adapter as adapter
from backend.services.investment_policy import etf_release as release
from backend.services.investment_policy.configuration import Listing
from backend.services.investment_policy.contracts import digest
from tests.test_c_concentration import ALPHABET, AT, active, frozen, holdings, position, project, view  # noqa: F401

AT_REFRESH = datetime(2026, 11, 26, 11, tzinfo=timezone.utc)
DAY = date(2026, 11, 25)


@pytest.fixture
def key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def channel(tmp_path):
    return tmp_path.resolve()


def record(symbol="SPY:US", value=".1", source="a", **kwargs):
    return holdings(symbol, {"GOOG:US": value}, source_sha256=source * 64, **kwargs)


def test_atomic_versions_idempotence_concurrency_and_expired_rollback(channel, key):
    pub = key.public_key().public_bytes_raw()
    records = [record(), record("QQQ:US", source="b")]
    def publish_one(item):
        return release.publish(channel, key, 7, {item.symbol: item}, AT)
    with ThreadPoolExecutor(2) as workers:
        assert sorted(r.sequence for r in workers.map(publish_one, records)) == [1, 2]
    old, data = release.read_release(channel, pub, 7, AT)
    assert {r.symbol for r in data.records} == {"SPY:US", "QQQ:US"}
    assert release.publish(channel, key, 7, {"SPY:US": record(value=".2")}, AT) == old
    rollback_name = f"receipt-{sha256((channel / 'current.json').read_bytes()).hexdigest()}.json"
    fresh = record(value=".2", source="c", valid_until=AT + timedelta(hours=4))
    assert publish_one(fresh).sequence == 3
    later = AT + timedelta(hours=2)
    rolled = release.publish(channel, key, 7, {}, later, rollback_receipt=rollback_name)
    assert rolled.sequence == 4
    assert release.read_release(channel, pub, 7, later)[1].records == ()
    assert len(list(channel.glob("receipt-*"))) == 4


def test_release_rejects_wrong_scope_key_payload_and_consumer_uid(channel, key, monkeypatch):
    receipt = release.publish(channel, key, 7, {"SPY:US": record()}, AT)
    pub = key.public_key().public_bytes_raw()
    with pytest.raises(ValueError, match="READ_ONLY"):
        release.consumer_directory(channel, os.getuid())
    with pytest.raises(ValueError, match="OWNER_UID"):
        release.consumer_directory(channel, os.getuid() + 1)
    with pytest.raises(ValueError, match="BINDING"):
        release.read_release(channel, pub, 1, AT)
    with pytest.raises(ValueError, match="SIGNATURE"):
        release.read_release(channel, Ed25519PrivateKey.generate().public_key().public_bytes_raw(), 7, AT)
    with pytest.raises(ValueError, match="BINDING"):
        release.read_release(channel, pub, 7, AT - timedelta(microseconds=1))
    manifest = channel / f"manifest-{receipt.manifest_sha256}.json"
    manifest.write_bytes(b"{}")
    with pytest.raises(ValueError, match="DIGEST"):
        release.read_release(channel, pub, 7, AT)
    monkeypatch.setenv("WEALTHPILOT_ETF_RELEASE_DIRECTORY", str(channel))
    monkeypatch.setenv("WEALTHPILOT_ETF_RELEASE_OWNER_UID", str(os.getuid()))
    monkeypatch.setenv("WEALTHPILOT_ETF_RELEASE_PUBLIC_KEY", pub.hex())
    assert adapter._read_etf_evidence(7, at=AT)[2] == "ETF_OWNER_EVIDENCE_UNAVAILABLE"


def test_signed_update_recalculates_c_without_restarting_and_binds_close(channel, key, monkeypatch, frozen, active):
    monkeypatch.setenv("WEALTHPILOT_ETF_RELEASE_DIRECTORY", str(channel))
    monkeypatch.setenv("WEALTHPILOT_ETF_RELEASE_OWNER_UID", str(os.getuid()))
    monkeypatch.setenv("WEALTHPILOT_ETF_RELEASE_PUBLIC_KEY", key.public_key().public_bytes_raw().hex())
    # Simulate the distinct deployment UID. All signature/scope/digest checks still run.
    monkeypatch.setattr(adapter, "consumer_directory", lambda path, owner_uid: release.release_root(path))
    position(frozen, "GOOG:US", 45)
    position(frozen, "GOOGL:US", 25)
    fund = position(frozen, "SPY:US", 200, vehicle="ETF")
    fund.update(quantity="2", fx_rate_to_cny="1")
    position(frozen, "QQQ:US", 25, vehicle="ETF")
    position(frozen, "SPMO:US", 25, vehicle="ETF")

    def calc():
        return adapter.allocation_concentration(frozen, projection=project(frozen), session=active,
                                                at=AT, classified_assets=None)
    def change(weight, source):
        item = record(value=weight, source=source, parser_version="test" + release.AUTOMATED_SPY_PROOF)
        release.publish(channel, key, 7, {"SPY:US": item}, AT, close_caps={"SPY:US": Decimal(100)})
    change(".04", "a")
    assert calc()["issuers"][ALPHABET]["risk_status"] != "HUMAN_REVIEW"
    change(".06", "b")
    result = calc()
    assert result["issuers"][ALPHABET]["risk_status"] == "HUMAN_REVIEW"
    assert result["issuers"][ALPHABET]["proven_upper_bound_pct"] is None
    assert {v["symbol"]: v["freshness_status"] for v in result["etfs"].values()} == {
        "SPY:US": "FRESH", "QQQ:US": "UNAVAILABLE", "SPMO:US": "UNAVAILABLE",
    }
    fund["quantity"] = "1.99"  # Still same date, but USD value per share exceeds cap.
    assert calc()["issuers"][ALPHABET]["risk_status"] != "HUMAN_REVIEW"
    frozen["data_quality"].pop("investable_owner_evidence")
    assert calc()["status"] == "CONDITIONAL_RISK"


def workbook(sheet, rows):
    book = Workbook()
    book.active.title = sheet
    for row in rows:
        book.active.append(row)
    raw = BytesIO()
    book.save(raw)
    book.close()
    return raw.getvalue()


def synthetic_sources():
    rows = [("Fund Name:", "SPDR S&P 500® ETF Trust"), ("Ticker Symbol:", "SPY"),
            ("Holdings:", "As of 25-Nov-2026"), (None,) * 8,
            ("Name", "Ticker", "Identifier", "SEDOL", "Weight", "Sector", "Shares Held", "Local Currency")]
    rows.extend((ticker, ticker, cusip, "-", 1, "-", 100, "USD") for ticker, cusip in refresh.FORMAL_CUSIPS.items())
    fixtures = {refresh.SOURCES["SPY"]: workbook("holdings", rows),
                refresh.SOURCES["QQQ"]: b'{"effectiveDate":"2026-11-25"}',
                refresh.SOURCES["SPMO"]: json.dumps(dict(cusip="46138E339", effectiveDate=DAY.isoformat(),
                    effectiveBusinessDate=DAY.isoformat(), totalNumberOfHoldings=1,
                    holdings=[dict(ticker="NVDA", cusip="67066G104", currency="USD", securityTypeCode="COM",
                                   issuerName="NVIDIA", percentageOfTotalNetAssets=8, units=1000)])).encode(),
                refresh.NAV_SOURCE: workbook("navhist", [("Fund Name:", "SPY"), ("Ticker Symbol:", "SPY"),
                    (None,) * 4, ("Date", "NAV", "Shares Outstanding", "Total Net Assets"),
                    ("25-Nov-2026", 100, 10000, 1000000)])}
    for ticker in ("SPY", *refresh.FORMAL_CUSIPS):
        fixtures[refresh.price_url(ticker, DAY)] = json.dumps({
            "status": {"rCode": 200}, "data": {"symbol": ticker, "tradesTable": {"rows": [
                {"date": "11/25/2026", "close": "$102.00" if ticker == "SPY" else "$100.00"}]}}}).encode()
    return fixtures


def test_refresh_positive_repeated_bytes_and_single_fund_error(channel, key, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            return AT_REFRESH.astimezone(tz)

    monkeypatch.setattr(refresh, "datetime", Clock)
    fixtures = synthetic_sources()
    one = refresh.refresh(channel, key, 7, fetch=fixtures.__getitem__)
    assert one["published"] == 1
    assert one["sources"]["QQQ"]["failure"]["stage"] == "observation"
    assert one["sources"]["SPMO"]["issuer_observed_weight_pct"] == {"SEC_CIK_0001045810": "8"}
    signed, manifest = release.read_release(channel, key.public_key().public_bytes_raw(), 7, AT_REFRESH)
    assert signed.close_caps_usd == {"SPY:US": Decimal("102.005")}
    assert len(manifest.records[0].rows) == 4 and manifest.records[0].valid_until.date() == date(2026, 11, 27)
    assert manifest.records[0].coverage < Decimal(".04")  # ETF premium handled conservatively.
    second = refresh.refresh(channel, key, 7, fetch=fixtures.__getitem__)
    assert second["published"] is None and second["current_release_sequence"] == 1
    assert not second["published_event"] and second["sources"]["SPY"]["formal"] == "UNCHANGED"
    assert second["sources"]["SPY"]["parsed"] is False
    assert len(list(channel.glob("receipt-*"))) == 1


def test_extended_listing_identity_and_cusip():
    listing = Listing(symbol="NEWX:US", issuer_id="SEC_CIK_0001000000", issuer_name="TEST issuer",
                      listing_market="NASDAQ", cusip="123456789", share_class="Class A",
                      evidence_url="https://www.sec.gov/Archives/edgar/data/1000000/000100000026000001/form.htm",
                      evidence_sha256="0" * 64, valid_from=DAY, valid_until=DAY)
    row = dict(ticker="NEWX", cusip=listing.cusip, currency="USD", type="COM")
    listings = {listing.symbol: listing}
    assert refresh.observed_issuer(row, DAY, listings) == listing.issuer_id
    assert refresh.observed_issuer(row | {"cusip": "111111111"}, DAY, listings) is None
    assert refresh.observed_issuer(row, DAY - timedelta(days=1), listings) is None


def test_reader_sees_only_current_or_complete_new_version(channel, key, monkeypatch):
    from threading import Event

    pub = key.public_key().public_bytes_raw()
    release.publish(channel, key, 7, {"SPY:US": record()}, AT)
    before_activation, resume = Event(), Event()
    original = release.write_version

    def pause(root, name, data, **options):
        if name == "current.json":
            before_activation.set()
            assert resume.wait(5)
        return original(root, name, data, **options)

    monkeypatch.setattr(release, "write_version", pause)
    with ThreadPoolExecutor(1) as worker:
        candidate = worker.submit(release.publish, channel, key, 7, {"SPY:US": record(source="b")}, AT)
        try:
            assert before_activation.wait(5)
            assert release.read_release(channel, pub, 7, AT)[1].records[0].source_sha256 == "a" * 64
        finally:
            resume.set()
        assert candidate.result(timeout=5).sequence == 2
    assert release.read_release(channel, pub, 7, AT)[1].records[0].source_sha256 == "b" * 64


def test_rejects_automated_basis_without_cap_and_wrong_archived_receipt_digest(channel, key):
    auto = record(parser_version="test" + release.AUTOMATED_SPY_PROOF)
    with pytest.raises(ValueError, match="UPDATE_INVALID"):
        release.publish(channel, key, 7, {"SPY:US": auto}, AT)
    release.publish(channel, key, 7, {"SPY:US": record()}, AT)
    fake_name = f"receipt-{'0' * 64}.json"
    release.write_version(channel, fake_name, (channel / "current.json").read_bytes())
    with pytest.raises(ValueError, match="RECEIPT_DIGEST"):
        release.publish(channel, key, 7, {}, AT, rollback_receipt=fake_name)


@pytest.mark.parametrize(("now", "fires", "utc_hour"), [
    (datetime(2026, 11, 26, 11, tzinfo=timezone.utc), True, 11),  # after the Wednesday session
    (datetime(2026, 11, 27, 11, tzinfo=timezone.utc), False, 11),  # after Thanksgiving
    (datetime(2026, 11, 28, 11, tzinfo=timezone.utc), True, 11),  # after Friday's half day
    (datetime(2026, 7, 18, 10, tzinfo=timezone.utc), True, 10),  # summer DST
])
def test_owner_schedule_only_after_completed_session(now, fires, utc_hour, monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler

    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            return now.astimezone(tz)

    monkeypatch.setattr(refresh, "datetime", Clock)
    observed = []
    scheduler = BackgroundScheduler()
    refresh.register_refresh(scheduler, lambda: observed.append(True))
    job = scheduler.get_job("etf_evidence_refresh")
    assert job.trigger.get_next_fire_time(None, now).astimezone(timezone.utc).hour == utc_hour
    job.func()
    assert bool(observed) is fires


def test_observation_only_release_legacy_read_and_observation_tamper_isolated(channel, key, monkeypatch):
    from backend.services.investment_policy.etf_observation import ETFObservation

    public = key.public_key().public_bytes_raw()
    legacy = release.publish(channel, key, 7, {"SPY:US": record()}, AT)
    assert "observation_sha256" not in json.loads((channel / "current.json").read_bytes())
    assert release.read_release(channel, public, 7, AT)[0] == legacy
    observation = ETFObservation(symbol="QQQ:US", holdings_as_of=DAY, known_at=AT_REFRESH,
                                 source=refresh.SOURCES["QQQ"], source_sha256="9" * 64,
                                 parser_version="fixture-v1", issuer_version="fixture-v1", listing_sha256="8"*64,
                                 issuer_weights_pct={ALPHABET: ".5"}, unmapped_equity_weight_pct="80",
                                 disclosure_rows=100, disclosed_weight_pct="100")
    new, changed = release.publish(channel, key, 7, {}, AT_REFRESH, observations={"QQQ:US": observation},
                                   with_event=True)
    assert changed and new.sequence == 2 and new.manifest_sha256 == legacy.manifest_sha256
    assert release.read_observations(channel, new, public)["QQQ:US"] == observation
    assert set(json.loads((channel / "current.json").read_bytes())) == set(legacy.model_dump())
    next(channel.glob("observation-manifest-*.json")).write_bytes(b"{}")
    monkeypatch.setenv("WEALTHPILOT_ETF_RELEASE_DIRECTORY", str(channel))
    monkeypatch.setenv("WEALTHPILOT_ETF_RELEASE_OWNER_UID", str(os.getuid()))
    monkeypatch.setenv("WEALTHPILOT_ETF_RELEASE_PUBLIC_KEY", public.hex())
    monkeypatch.setattr(adapter, "consumer_directory", lambda path, uid: release.release_root(path))
    records, _, issue, _, observations = adapter._read_etf_evidence(7, at=AT_REFRESH)
    assert "SPY:US" in records and issue == "ETF_OBSERVATION_UNAVAILABLE" and not observations


def test_observations_aggregate_all_three_and_leave_formal_rules_unchanged(channel, key, monkeypatch, frozen, active):
    from backend.services.investment_policy.etf_observation import ETFObservation

    monkeypatch.setenv("WEALTHPILOT_ETF_RELEASE_DIRECTORY", str(channel))
    monkeypatch.setenv("WEALTHPILOT_ETF_RELEASE_OWNER_UID", str(os.getuid()))
    monkeypatch.setenv("WEALTHPILOT_ETF_RELEASE_PUBLIC_KEY", key.public_key().public_bytes_raw().hex())
    monkeypatch.setattr(adapter, "consumer_directory", lambda path, uid: release.release_root(path))
    position(frozen, "GOOG:US", 35)
    position(frozen, "GOOGL:US", 35)
    for ticker in refresh.SOURCES:
        position(frozen, f"{ticker}:US", 100, vehicle="ETF")
    release.publish(channel, key, 7, {"SPY:US": record(value=".01")}, AT)

    def calculate():
        return adapter.allocation_concentration(frozen, projection=project(frozen), session=active, at=AT,
                                                classified_assets=None)

    def observed(ticker, weight, raw):
        return ETFObservation(
            symbol=f"{ticker}:US", holdings_as_of=refresh.latest_completed_session(AT), known_at=AT,
            source=refresh.SOURCES[ticker], source_sha256=raw*64, parser_version="fixture-v1",
            issuer_version="fixture-v1", listing_sha256="8"*64, issuer_weights_pct={ALPHABET: weight},
            unmapped_equity_weight_pct="60", disclosure_rows=100, disclosed_weight_pct=100,
        )

    original = calculate()
    all_records = {f"{ticker}:US": observed(ticker, str(i+10), "a") for i, ticker in enumerate(refresh.SOURCES)}
    receipt, event = release.publish(channel, key, 7, {}, AT, observations=all_records, with_event=True)
    assert receipt.sequence == 2 and event
    result = calculate()
    obs = result.pop("etf_observation")
    assert result == original and result["issuers"][ALPHABET]["risk_status"] != "HUMAN_REVIEW"
    row = obs["issuers"][ALPHABET]
    assert row["direct_cny"] == "70"
    assert list(map(Decimal, row["indirect_observed_cny"].values())) == [10, 11, 12]
    assert Decimal(row["available_observed_cny"]) == 103 and row["coverage"] == "PARTIAL"
    next_receipt, changed = release.publish(channel, key, 7, {}, AT, observations={"QQQ:US": observed("QQQ","15","b")},
                                            with_event=True)
    assert next_receipt.sequence == 3 and changed
    assert Decimal(calculate()["etf_observation"]["issuers"][ALPHABET]["available_observed_cny"]) == 107
    frozen["data_quality"].pop("investable_owner_evidence")
    conditional = calculate()
    assert conditional["status"] == "CONDITIONAL_RISK" and conditional["etf_observation"]["status"] == "CONDITIONAL"
    assert conditional["issuers"][ALPHABET]["known_lower_bound_pct"] is None
    assert conditional["issuers"][ALPHABET]["policy_hits"] == []


def test_five_completed_sessions_boundary_and_rollback(channel, key):
    from exchange_calendars import get_calendar
    from backend.services.investment_policy.etf_observation import ETFObservation, freshness

    calendar = get_calendar("XNYS")
    value = ETFObservation(symbol="SPMO:US", holdings_as_of=DAY, known_at=AT_REFRESH,
                           source=refresh.SOURCES["SPMO"], source_sha256="9"*64,
                           parser_version="fixture-v1", issuer_version="fixture-v1", listing_sha256="8"*64,
                           issuer_weights_pct={ALPHABET: "10"}, unmapped_equity_weight_pct="60",
                           disclosure_rows=100, disclosed_weight_pct="100")
    first, activated = release.publish(channel, key, 7, {}, AT_REFRESH,
                                       observations={value.symbol: value}, with_event=True)
    assert first.sequence == 1 and activated
    rollback = f"receipt-{sha256((channel / 'current.json').read_bytes()).hexdigest()}.json"
    fifth = calendar.session_close(calendar.session_offset(DAY, 5)).to_pydatetime()
    sixth = calendar.session_close(calendar.session_offset(DAY, 6)).to_pydatetime()
    assert freshness(value, fifth, refresh.latest_completed_session(fifth)) == ("OBSERVED", 5, sixth)
    assert freshness(value, sixth, refresh.latest_completed_session(sixth)) == ("STALE", 6, sixth)
    rolled = release.publish(channel, key, 7, {}, sixth, rollback_receipt=rollback)
    assert rolled.sequence == 2 and not release.read_observations(channel, rolled, key.public_key().public_bytes_raw())


def test_disclosed_null_weight_does_not_make_a_known_zero():
    row = {"ticker": "GOOG", "cusip": "02079K107", "type": "COM", "currency": "USD"}
    assert refresh.map_weights([row | {"observed_weight_pct": None}], DAY, {}) == ({}, 0)
    assert refresh.map_weights([row | {"observed_weight_pct": "0"}], DAY, {}) == ({ALPHABET: 0}, 0)
