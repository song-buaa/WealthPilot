"""E1-N1 frozen SEC discovery and read-only reminder projection."""
from datetime import datetime
import gzip
import json
from pathlib import Path

import pytest

from backend.services.investment_thesis.monitoring import ReviewMonitorStore
from backend.services.investment_thesis.monitoring_cli import main as monitoring_cli
from backend.services.investment_thesis.natural_scan import NaturalReviewScanner, natural_scan_enabled
from research_v2.sec_disclosures import verify_filing_envelope
from tests.test_e1_b32_monitoring import setup as b32_setup  # noqa: F401


ACCESSION = "0001737806-26-000001"
BODY = (b"<SEC-HEADER>\nCENTRAL INDEX KEY: 0001737806\nCONFORMED SUBMISSION TYPE: 20-F\n"
        b"ACCESSION NUMBER: 0001737806-26-000001\n<ACCEPTANCE-DATETIME>20260825072002\n"
        b"</SEC-HEADER>\n<DOCUMENT><TYPE>20-F\n<SEQUENCE>1\n<FILENAME>pdd-2026.htm\n</DOCUMENT>")
ROOT = Path(__file__).resolve().parents[1]


def directory():
    return {"0": {"cik_str": 1737806, "ticker": "PDD", "title": "PDD Holdings Inc."}}


def submissions(*, include_new):
    rows = [dict(accessionNumber="0001104659-25-050727", form="20-F",
                 filingDate="2025-04-25", reportDate="2024-12-31",
                 primaryDocument="pdd-20241231x20f.htm",
                 acceptanceDateTime="2025-04-25T20:00:00Z")]
    if include_new:
        rows.insert(0, dict(accessionNumber=ACCESSION, form="20-F", filingDate="2026-08-25",
                           reportDate="2025-12-31", primaryDocument="pdd-2026.htm",
                           acceptanceDateTime="2026-08-25T11:20:02Z"))
    recent = {key: [row[key] for row in rows] for key in rows[0]}
    return {"cik": 1737806, "name": "PDD Holdings Inc.", "tickers": ["PDD"],
            "filings": {"recent": recent}}


class FrozenSEC:
    def __init__(self):
        self.include_new = False
        self.calls = []
        self.fail_original = False
        # Reproduces the production ordering: the response completes after the
        # scan cutoff. fetched_at is evidence acquisition time, not publication.
        self.fetched_at = "2026-10-29T01:00:00.679722+00:00"

    def __call__(self, url):
        self.calls.append(url)
        fetched = self.fetched_at
        if url.endswith("company_tickers.json"):
            return json.dumps(directory()).encode(), fetched
        if url.endswith("CIK0001737806.json"):
            return json.dumps(submissions(include_new=self.include_new)).encode(), fetched
        if url.endswith(ACCESSION + ".txt") and not self.fail_original:
            return BODY, fetched
        return None


def test_first_watermark_new_event_dedup_restart_and_read_page(b32_setup):
    store, monitor, _, _, before = b32_setup
    source = FrozenSEC()
    baseline = NaturalReviewScanner(monitor, fetch=source).run(
        as_of=datetime.fromisoformat("2026-10-29T01:00:00+00:00"), run_id="baseline")
    assert baseline["status"] == "NO_NEW_EVENTS"
    assert baseline["monitor_receipts"][0]["status"] == "WATERMARK_INITIALIZED"
    assert baseline["review_scan"]["generated"] == []
    assert len(source.calls) == 2  # no original fetch and no historical replay

    source.include_new = True
    discovered = NaturalReviewScanner(ReviewMonitorStore(store), fetch=source).run(
        as_of=datetime.fromisoformat("2026-10-29T01:01:00+00:00"), run_id="new-filing")
    assert discovered["status"] == "OK"
    assert len(discovered["review_scan"]["generated"]) == 1
    task = discovered["review_scan"]["generated"][0]
    assert task["trigger"]["economic_event_id"] == "SEC:0001737806:" + ACCESSION
    assert task["notification_status"] == "NOT_WIRED"

    restarted = NaturalReviewScanner(ReviewMonitorStore(store), fetch=source).run(
        as_of=datetime.fromisoformat("2026-10-29T01:02:00+00:00"), run_id="restart")
    assert restarted["review_scan"]["generated"] == []
    assert restarted["monitor_receipts"][0]["status"] == "NO_NEW_EVENTS"
    page = ReviewMonitorStore(store).due_page(
        as_of=datetime.fromisoformat("2026-10-29T01:03:00+00:00"), limit=1)
    assert [row["event_key"] for row in page["items"]] == [task["event_key"]]
    hidden = ReviewMonitorStore(store).due_page(
        as_of=datetime.fromisoformat("2026-10-29T01:03:00+00:00"),
        known_event_keys=[task["event_key"]])
    assert hidden["items"] == [] and hidden["unchanged_event_keys"] == [task["event_key"]]
    assert store.history("e1-b32-test-pdd") == before["history"]
    assert store.reviews("e1-b32-test-pdd") == before["reviews"]


def test_historical_cutoff_uses_acceptance_not_later_fetch_clock(b32_setup):
    store, monitor, _, _, before = b32_setup
    source = FrozenSEC()
    source.include_new = True
    source.fetched_at = "2026-10-29T09:28:24.073396+00:00"

    # The current Submissions payload already contains the August filing, but at
    # this historical cutoff its SEC acceptance is still in the future.  The
    # later, truthful fetch clock must not make the source unavailable or leak it
    # into the initial watermark.
    before_acceptance = NaturalReviewScanner(monitor, fetch=source).run(
        as_of=datetime.fromisoformat("2026-08-25T10:00:00+00:00"),
        run_id="historical-before-acceptance")
    receipt = before_acceptance["monitor_receipts"][0]
    assert receipt["status"] == "WATERMARK_INITIALIZED"
    assert receipt["known_accessions"] == ["0001104659-25-050727"]
    assert receipt["directory_fetched_at"] == source.fetched_at
    assert before_acceptance["review_scan"]["generated"] == []
    assert source.calls == [
        "https://www.sec.gov/files/company_tickers.json",
        "https://data.sec.gov/submissions/CIK0001737806.json",
    ]

    # Once the acceptance clock is inside the cutoff, the same source and the
    # full submission are eligible even though both were fetched later.
    after_acceptance = NaturalReviewScanner(ReviewMonitorStore(store), fetch=source).run(
        as_of=datetime.fromisoformat("2026-08-25T12:00:00+00:00"),
        run_id="historical-after-acceptance")
    assert after_acceptance["status"] == "OK"
    assert after_acceptance["monitor_receipts"][0]["new_accessions"] == [ACCESSION]
    assert len(after_acceptance["review_scan"]["generated"]) == 1
    assert source.calls[-1] == (
        "https://www.sec.gov/Archives/edgar/data/1737806/"
        "000173780626000001/0001737806-26-000001.txt"
    )
    assert store.history("e1-b32-test-pdd") == before["history"]
    assert store.reviews("e1-b32-test-pdd") == before["reviews"]


def test_source_receipt_requires_parseable_aware_fetch_clock(b32_setup):
    _, monitor, _, _, _ = b32_setup
    payload = json.dumps(directory()).encode()
    for value in ("not-a-clock", "2026-10-29T01:00:00"):
        result = NaturalReviewScanner(
            monitor, fetch=lambda _url, timestamp=value: (payload, timestamp)).run(
                as_of=datetime.fromisoformat("2026-10-29T01:00:00+00:00"),
                run_id="bad-fetch-clock-" + ("text" if value == "not-a-clock" else "naive"))
        assert result["status"] == "PARTIAL_SOURCE_FAILURE"
        assert result["monitor_receipts"][0]["status"] == "SOURCE_UNAVAILABLE"
        assert result["monitor_receipts"][0]["issues"] == ["SEC_DIRECTORY_UNAVAILABLE"]


def test_source_failure_is_not_no_event_and_retries_original(b32_setup):
    store, monitor, _, _, _ = b32_setup
    source = FrozenSEC()
    NaturalReviewScanner(monitor, fetch=source).run(
        as_of=datetime.fromisoformat("2026-10-29T01:00:00+00:00"), run_id="baseline-failure")
    source.include_new = True; source.fail_original = True
    failed = NaturalReviewScanner(monitor, fetch=source).run(
        as_of=datetime.fromisoformat("2026-10-29T01:01:00+00:00"), run_id="failed-original")
    assert failed["status"] == "PARTIAL_SOURCE_FAILURE"
    assert failed["monitor_receipts"][0]["issues"] == ["SEC_ORIGINAL_UNVERIFIED:" + ACCESSION]
    assert failed["review_scan"]["generated"] == []
    source.fail_original = False
    retried = NaturalReviewScanner(ReviewMonitorStore(store), fetch=source).run(
        as_of=datetime.fromisoformat("2026-10-29T01:02:00+00:00"), run_id="retry-original")
    assert len(retried["review_scan"]["generated"]) == 1


def test_identity_mismatch_future_and_cancel_fail_closed(b32_setup):
    store, monitor, _, _, _ = b32_setup
    source = FrozenSEC()
    bad = directory(); bad["0"]["ticker"] = "OTHER"
    source.__call__ = pytest.fail  # instance special methods are resolved on type; guard below uses lambda
    scanner = NaturalReviewScanner(monitor, fetch=lambda url: (
        (json.dumps(bad).encode(), "2026-10-29T01:00:00+00:00")
        if url.endswith("company_tickers.json") else None))
    result = scanner.run(as_of=datetime.fromisoformat("2026-10-29T01:00:00+00:00"), run_id="bad-id")
    assert result["status"] == "PARTIAL_SOURCE_FAILURE"
    assert result["monitor_receipts"][0]["status"] == "IDENTITY_UNRESOLVED"
    monitor.cancel("b32-pdd-monitor", changed_at=datetime.fromisoformat("2026-10-29T01:01:00+00:00"),
                   reason="explicit opt-out", request_id="n1-cancel")
    calls = []
    stopped = NaturalReviewScanner(ReviewMonitorStore(store), fetch=lambda url: calls.append(url)).run(
        as_of=datetime.fromisoformat("2026-10-29T01:02:00+00:00"), run_id="cancelled")
    assert stopped["status"] == "NO_OPTED_IN_MONITORS" and calls == []


def test_due_page_rejects_bad_cursor_and_keys(b32_setup):
    _, monitor, _, _, _ = b32_setup
    now = datetime.fromisoformat("2026-11-02T00:00:00+00:00")
    monitor.scan(as_of=now)
    with pytest.raises(ValueError, match="CURSOR_INVALID"):
        monitor.due_page(as_of=now, after_event_key="bad")
    with pytest.raises(ValueError, match="KNOWN_KEYS_INVALID"):
        monitor.due_page(as_of=now, known_event_keys=["bad"])


def test_real_frozen_sec_original_envelope_and_default_off(b32_setup, monkeypatch, capsys):
    store, _, _, _, _ = b32_setup
    accession = "0001104659-26-100534"
    filing = dict(accession=accession, form="6-K", filed="2026-08-25",
        published_at="2026-08-25T07:20:02-04:00",
        primary_url="https://www.sec.gov/Archives/edgar/data/1737806/000110465926100534/tm2623874d1_6k.htm",
        full_url="https://www.sec.gov/Archives/edgar/data/1737806/000110465926100534/" + accession + ".txt")
    body = gzip.decompress((ROOT / "tests/fixtures/pdd_sec_q2_2026/0001104659-26-100534.txt.gz").read_bytes())
    verified = verify_filing_envelope((body, "2026-10-29T01:00:00+00:00"),
        {"cik": "0001737806"}, filing)
    assert verified and verified["source_sha256"] == "162f5c2578869f4870fe24cc1ecb1518273447f3e21e0ba4ace1dfbd8cdc8b42"
    monkeypatch.delenv("WEALTHPILOT_E1_NATURAL_SCAN_ENABLED", raising=False)
    assert natural_scan_enabled() is False
    code = monitoring_cli(["--store", str(store.path), "--portfolio-id", "7", "natural-scan",
                           "--as-of", "2026-10-29T01:00:00+00:00", "--run-id", "disabled"])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "E1_NATURAL_SCAN_DISABLED"
