"""Bounded, opt-in SEC discovery for E1 review tasks; no scheduler or analysis."""
from __future__ import annotations

from datetime import datetime
import hashlib
import os
import re

from backend.services.instruments.issuers import resolve_sec_issuer
from research_v2 import sec_disclosures as sec

from .monitoring import ReviewMonitorStore


FORM_EVENT = {
    "10-K": "OFFICIAL_FINANCIAL_FILING", "10-Q": "OFFICIAL_FINANCIAL_FILING",
    "20-F": "OFFICIAL_FINANCIAL_FILING", "8-K": "FORMAL_REGULATORY_EVENT",
    "6-K": "FORMAL_REGULATORY_EVENT",
}


def natural_scan_enabled():
    return os.getenv("WEALTHPILOT_E1_NATURAL_SCAN_ENABLED", "").lower() == "true"


class NaturalReviewScanner:
    """One manual low-frequency run. Production scheduling remains external/off."""

    def __init__(self, monitor: ReviewMonitorStore, *, fetch=None, max_monitors=10,
                 max_requests=31, max_new_events=3):
        self.monitor = monitor
        self.fetch = fetch or sec._fetch
        self.max_monitors = max_monitors
        self.max_requests = max_requests
        self.max_new_events = max_new_events
        self.requests_used = 0

    def _fetch(self, url):
        if self.requests_used >= self.max_requests:
            return None
        self.requests_used += 1
        try:
            return self.fetch(url)
        except (OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _json(source):
        return sec._json(source)

    @staticmethod
    def _proof(source):
        try:
            source_sha = hashlib.sha256(source[0]).hexdigest()
        except (IndexError, TypeError):
            return None, None
        try:
            fetched = datetime.fromisoformat(source[1].replace("Z", "+00:00"))
            fetched_at = source[1] if fetched.tzinfo is not None and fetched.utcoffset() is not None else None
            return source_sha, fetched_at
        except (AttributeError, IndexError, TypeError, ValueError):
            return source_sha, None

    @staticmethod
    def _source_receipt_valid(source):
        """Validate the observed fetch clock without treating it as a PIT cutoff.

        A network response necessarily completes after a scan begins.  Point-in-time
        eligibility belongs to each filing's SEC acceptance clock; fetched_at is the
        immutable acquisition clock and must remain truthful.
        """
        try:
            fetched = datetime.fromisoformat(source[1].replace("Z", "+00:00"))
            return fetched.tzinfo is not None and fetched.utcoffset() is not None
        except (AttributeError, IndexError, TypeError, ValueError):
            return False

    @staticmethod
    def _published_by(filing, as_of):
        try:
            published = datetime.fromisoformat(filing["published_at"].replace("Z", "+00:00"))
            return published <= as_of if published.tzinfo else published.date() <= as_of.date()
        except (AttributeError, KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def _candidate_cik(symbol, directory):
        ticker = symbol[:-3] if isinstance(symbol, str) and symbol.endswith(":US") else None
        matches = [row for row in directory.values() if isinstance(row, dict)
                   and row.get("ticker") == ticker] if isinstance(directory, dict) else []
        if len(matches) != 1:
            return None
        try:
            return str(int(matches[0]["cik_str"])).zfill(10)
        except (KeyError, TypeError, ValueError):
            return None

    def run(self, *, as_of: datetime, run_id: str):
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("E1_AS_OF_REQUIRED")
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
            raise ValueError("E1_NATURAL_SCAN_RUN_ID_INVALID")
        policies = [row for row in self.monitor.latest_monitors()
                    if row["state"] == "ACTIVE" and row["registration"]["event_types"]]
        policies = policies[:self.max_monitors]
        if not policies:
            due = self.monitor.scan(as_of=as_of)
            return self._result(as_of, run_id, "NO_OPTED_IN_MONITORS", due, [])
        directory_source = self._fetch(sec.DIRECTORY)
        directory = self._json(directory_source) if self._source_receipt_valid(directory_source) else None
        directory_sha, directory_fetched = self._proof(directory_source)
        if directory is None:
            receipts = [self._receipt(policy, as_of, "SOURCE_UNAVAILABLE",
                        issues=["SEC_DIRECTORY_UNAVAILABLE"], directory_sha=directory_sha,
                        directory_fetched=directory_fetched)
                        for policy in policies]
            self._save_receipts(receipts, run_id)
            due = self.monitor.scan(as_of=as_of)
            return self._result(as_of, run_id, "PARTIAL_SOURCE_FAILURE", due, receipts)

        receipts, events = [], []
        for policy in policies:
            registration = policy["registration"]
            symbol = policy["security"]["symbol"]
            cik = self._candidate_cik(symbol, directory)
            if cik is None:
                receipts.append(self._receipt(policy, as_of, "IDENTITY_UNRESOLVED",
                                              issues=["SEC_TICKER_CIK_UNRESOLVED"],
                                              directory_sha=directory_sha,
                                              directory_fetched=directory_fetched))
                continue
            submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            submissions_source = self._fetch(submissions_url)
            submissions = (self._json(submissions_source)
                           if self._source_receipt_valid(submissions_source) else None)
            submissions_sha, submissions_fetched = self._proof(submissions_source)
            source_proof = dict(directory_sha=directory_sha, submissions_sha=submissions_sha,
                                directory_fetched=directory_fetched,
                                submissions_fetched=submissions_fetched)
            if submissions is None:
                receipts.append(self._receipt(policy, as_of, "SOURCE_UNAVAILABLE", cik=cik,
                                              issues=["SEC_SUBMISSIONS_UNAVAILABLE"], **source_proof))
                continue
            issuer = resolve_sec_issuer(symbol, directory, submissions)
            if issuer is None or issuer["issuer_id"] != registration["issuer_id"]:
                receipts.append(self._receipt(policy, as_of, "IDENTITY_UNRESOLVED", cik=cik,
                                              issues=["SEC_ISSUER_IDENTITY_MISMATCH"], **source_proof))
                continue
            filings = [row for row in sec.discover_filings(issuer, submissions)[:200]
                       if self._published_by(row, as_of)]
            prior = policy.get("source_scan")
            if prior is None:
                known = [row["accession"] for row in filings[:200]]
                receipts.append(self._receipt(policy, as_of, "WATERMARK_INITIALIZED", cik=cik,
                                              known=known, **source_proof))
                continue
            known = set(prior.get("known_accessions", []))
            unseen = [row for row in filings if row["accession"] not in known]
            accepted, eligible_ids, issues = [], [], []
            for filing in unseen:
                event_type = FORM_EVENT.get(filing["form"])
                if event_type not in registration["event_types"]:
                    known.add(filing["accession"])
                    continue
                if "T" not in filing["published_at"]:
                    known.add(filing["accession"])
                    issues.append("ACCEPTANCE_CLOCK_UNAVAILABLE:" + filing["accession"])
                    continue
                if len(accepted) >= self.max_new_events:
                    break
                envelope = sec.verify_filing_envelope(self._fetch(filing["full_url"]), issuer, filing)
                if envelope is None:
                    issues.append("SEC_ORIGINAL_UNVERIFIED:" + filing["accession"])
                    continue
                published = datetime.fromisoformat(envelope["published_at"])
                fetched = datetime.fromisoformat(envelope["fetched_at"])
                if published > as_of:
                    issues.append("FUTURE_AT_SCAN_TIME:" + filing["accession"])
                    continue
                economic_id = f"SEC:{cik}:{filing['accession']}"
                event_id = economic_id + ":" + envelope["source_sha256"][:12]
                events.append(dict(event_id=event_id, economic_event_id=economic_id,
                    event_type=event_type, symbol=symbol, issuer_id=issuer["issuer_id"],
                    description=f"SEC {filing['form']} accepted for {symbol}; review required without directional inference.",
                    source_ref=envelope["source_ref"], source_sha256=envelope["source_sha256"],
                    published_at=published, known_at=published, fetched_at=fetched))
                accepted.append(filing["accession"]); eligible_ids.append(event_id)
                known.add(filing["accession"])
            status = "OK" if accepted else "PARTIAL_SOURCE_FAILURE" if issues else "NO_NEW_EVENTS"
            ordered_known = [row["accession"] for row in filings if row["accession"] in known]
            receipts.append(self._receipt(policy, as_of, status, cik=cik, known=ordered_known,
                                          new=accepted, eligible=eligible_ids, issues=issues, **source_proof))

        scan = self.monitor.scan(as_of=as_of, events=events)
        self._save_receipts(receipts, run_id)
        overall = ("OK" if events else "PARTIAL_SOURCE_FAILURE"
                   if any(row["status"] in {"SOURCE_UNAVAILABLE", "IDENTITY_UNRESOLVED",
                                             "PARTIAL_SOURCE_FAILURE"} for row in receipts)
                   else "NO_NEW_EVENTS")
        return self._result(as_of, run_id, overall, scan, receipts)

    def _receipt(self, policy, as_of, status, *, cik=None, known=None, new=None,
                 eligible=None, issues=None, directory_sha=None, submissions_sha=None,
                 directory_fetched=None, submissions_fetched=None):
        return dict(monitor_id=policy["registration"]["monitor_id"], source="SEC", status=status,
            as_of=as_of, cik=cik, directory_sha256=directory_sha,
            submissions_sha256=submissions_sha, directory_fetched_at=directory_fetched,
            submissions_fetched_at=submissions_fetched,
            known_accessions=known or [], new_accessions=new or [],
            eligible_event_ids=eligible or [], issues=issues or [], requests_used=self.requests_used)

    def _save_receipts(self, receipts, run_id):
        for row in receipts:
            monitor_id = row.pop("monitor_id")
            request_key = hashlib.sha256(f"{run_id}:{monitor_id}".encode()).hexdigest()
            self.monitor.record_source_scan(monitor_id, row, "e1-n1-" + request_key)
            row["monitor_id"] = monitor_id

    def _result(self, as_of, run_id, status, scan, receipts):
        return dict(schema_version="wealthpilot.4.3.e1.natural-scan.v1", run_id=run_id,
            status=status, as_of=as_of.isoformat(), requests_used=self.requests_used,
            monitor_receipts=receipts, review_scan=scan, auto_scan="OFF",
            notification_status="NOT_WIRED", scheduled_task_created=False,
            analysis_run=False, research_records_written=0, execution_authorized=False)
