"""Bounded private process-local receipts/results for the original position analysis.

No queue, persistence, provider client, portfolio writes, or replay after expiry.
A boot-scoped request ID prevents a retry from becoming new paid work after restart.
"""
from dataclasses import dataclass
import hashlib
import json
import secrets
import threading
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict
from app.llm_observation import run_id
from pocs.chatgpt_skills.analysis import AnalysisRequest, AnalysisResult


class PositionAnalysisJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["wealthpilot.position-analysis-job.v1"] = "wealthpilot.position-analysis-job.v1"
    job_id: str | None = None
    analysis_run_id: str | None = None
    request_scope: str
    state: Literal["running", "completed", "unavailable"]
    business_status: Literal["ok", "partial", "unavailable", "UNKNOWN"] = "UNKNOWN"
    issue: Literal["REQUEST_SCOPE_UNAVAILABLE", "REQUEST_ID_CONFLICT", "ANALYSIS_BUSY",
        "JOB_CAPACITY_REACHED", "JOB_UNKNOWN_OR_PROCESS_RESTARTED", "RESULT_EXPIRED",
        "JOB_START_FAILED", "ANALYSIS_JOB_FAILED", "RESULT_STORAGE_UNAVAILABLE"] | None = None
    poll_after_seconds: int | None = None
    result: AnalysisResult | None = None


@dataclass
class _Receipt:
    fingerprint: str
    job_id: str
    analysis_run_id: str
    state: str = "running"
    issue: str | None = None
    result_json: str | None = None
    finished: float | None = None


class PositionJobs:
    """One paid analysis at a time, shared admission with the original sync reads.

    Receipts never evict within this process: capacity refusal is safer than replay.
    Full results expire after two hours; retrieval never extends their lifetime.
    """
    def __init__(self, execute, slot, *, max_receipts=64, max_results=8,
                 retention_seconds=7200, max_result_bytes=2 * 1024 * 1024):
        self.scope = "pa_" + secrets.token_hex(16)
        self.execute, self.slot = execute, slot
        self.max_receipts, self.max_results = max_receipts, max_results
        self.retention_seconds, self.max_result_bytes = retention_seconds, max_result_bytes
        self.lock = threading.Lock()
        self.requests, self.jobs = {}, {}

    def _expire(self):
        now = time.monotonic()
        for row in self.jobs.values():
            if row.result_json is not None and now - row.finished >= self.retention_seconds:
                row.result_json = None
                row.state, row.issue = "unavailable", "RESULT_EXPIRED"

    def _response(self, row=None, issue=None):
        result = AnalysisResult.model_validate_json(row.result_json) if row and row.result_json is not None else None
        return PositionAnalysisJob(request_scope=self.scope, job_id=row.job_id if row else None,
            analysis_run_id=row.analysis_run_id if row else None,
            state=row.state if row else "unavailable", issue=issue or (row.issue if row else None),
            business_status=result.status if result else "UNKNOWN",
            poll_after_seconds=15 if row and row.state == "running" else None, result=result)

    def start(self, request_id: str, request: AnalysisRequest):
        # This prefix is advertised by the tool, not supplied as business authority.
        prefix = self.scope + ":"
        if not request_id.startswith(prefix) or not 16 <= len(request_id[len(prefix):]) <= 64:
            return self._response(issue="REQUEST_SCOPE_UNAVAILABLE")
        payload = request.model_dump(mode="json")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        with self.lock:
            self._expire()
            previous = self.requests.get(request_id)
            if previous:
                if previous.fingerprint != fingerprint:
                    return self._response(issue="REQUEST_ID_CONFLICT")
                return self._response(previous)
            occupied = sum(row.state == "running" or row.result_json is not None for row in self.jobs.values())
            if len(self.requests) >= self.max_receipts or occupied >= self.max_results:
                return self._response(issue="JOB_CAPACITY_REACHED")
            if not self.slot.acquire(blocking=False):
                return self._response(issue="ANALYSIS_BUSY")
            row = _Receipt(fingerprint, secrets.token_urlsafe(32), run_id())
            self.requests[request_id] = self.jobs[row.job_id] = row
            try:
                # A process-owned thread, never a task in the HTTP request's cancel scope.
                thread = threading.Thread(target=self._run, args=(row, request.model_copy(deep=True)),
                    name="wp-position-analysis", daemon=True)
                thread.start()
            except Exception:
                row.state, row.issue, row.finished = "unavailable", "JOB_START_FAILED", time.monotonic()
                self.slot.release()
            return self._response(row)

    def _run(self, row, request):
        result_json, issue = None, None
        try:
            result = AnalysisResult.model_validate(self.execute(request, row.analysis_run_id))
            result_json = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)
            if len(result_json.encode()) > self.max_result_bytes:
                result_json, issue = None, "RESULT_STORAGE_UNAVAILABLE"
        except Exception:
            # No raw exception, prompt, assets, or model content in job metadata/logs.
            issue = "ANALYSIS_JOB_FAILED"
        finally:
            with self.lock:
                row.result_json, row.finished = result_json, time.monotonic()
                row.state, row.issue = ("completed", None) if result_json is not None else ("unavailable", issue)
            self.slot.release()

    def get(self, job_id: str):
        with self.lock:
            self._expire()
            row = self.jobs.get(job_id)
            return self._response(row, None if row else "JOB_UNKNOWN_OR_PROCESS_RESTARTED")
