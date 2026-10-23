"""Bounded, content-free analysis metadata. No I/O, SDK or business authority.

Only server-created UUIDs cross internal worker envelopes. Calls contain a
closed vocabulary and numeric counters, never prompts, outputs or exceptions.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import inspect
import re
import threading
import time
import uuid

UNKNOWN = "UNKNOWN"
PRICE_VERSION = "openai-standard-text-reference-2026-10-23-v1"
PRICE_SOURCE = "https://developers.openai.com/api/docs/pricing"
# USD per million tokens, standard short-context uncached text reference.
# Reasoning is already part of output_tokens; never charge it twice.
RATES = {"gpt-6.1-sol": (2.0, 10.0), "gpt-6-luna": (0.10, 0.50)}
STAGES = {"planning", "executing", "expressing", "reviewing"}
ERRORS = {"authentication", "model_access", "quota", "rate_limit", "network", "timeout",
          "invalid_request", "provider", "provider_error", "truncated", "empty_output",
          "refusal", "invalid_output", "model_mismatch", "legacy_profile_required",
          "reasoning_effort_unsupported", "tools_require_responses", "observation_unavailable"}
EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}
_run = ContextVar("wealthpilot_analysis_observation", default=None)
_stage = ContextVar("wealthpilot_peer_stage", default=UNKNOWN)


def run_id(value=None):
    if value is None:
        return uuid.uuid4().hex
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("ANALYSIS_RUN_ID_INVALID")
    return value


def model_name(value):
    if isinstance(value, str) and re.fullmatch(
            r"gpt-(?:6\.1-sol|6-luna|4\.1(?:-mini)?)(?:-\d{4}-\d{2}-\d{2})?", value):
        return value
    return UNKNOWN


def counter(value):
    return value if type(value) is int and 0 <= value <= 10**12 else None


def field(value, key):
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def usage_metadata(usage, endpoint):
    inp = counter(field(usage, "prompt_tokens" if endpoint == "chat" else "input_tokens"))
    out = counter(field(usage, "completion_tokens" if endpoint == "chat" else "output_tokens"))
    details = field(usage, "completion_tokens_details" if endpoint == "chat" else "output_tokens_details")
    reasoning = counter(field(details, "reasoning_tokens"))
    if reasoning is not None and (out is None or reasoning > out):
        reasoning = None
    return dict(usage_status="KNOWN" if inp is not None and out is not None else UNKNOWN,
                input_tokens=inp, output_tokens=out, reasoning_tokens=reasoning)


def estimate(model, usage, *, standard_text):
    known = usage["usage_status"] == "KNOWN" and model in RATES and standard_text
    known = known and usage["input_tokens"] <= 272000
    amount = None
    if known:
        a, b = RATES[model]
        amount = round((usage["input_tokens"] * a + usage["output_tokens"] * b) / 1_000_000, 9)
    return dict(status="ESTIMATE" if known else UNKNOWN, estimated_cost_usd=amount,
                price_version=PRICE_VERSION, price_source=PRICE_SOURCE,
                basis="standard_uncached_text_reference_excludes_cache_adjustments_tools_and_regional_uplift",
                actual_bill=UNKNOWN)


class AnalysisObservation:
    def __init__(self, identifier=None):
        self.analysis_run_id = run_id(identifier)
        self.started = time.monotonic()
        self.lock = threading.RLock()
        self.calls = []
        self.count = 0
        self.closed = False
        self.status = "running"
        self.elapsed_ms = None

    def begin(self, options, endpoint):
        with self.lock:
            if self.closed:
                return None
            self.count += 1
            if len(self.calls) >= 128:
                return None
            model = model_name(options.get("model"))
            effort = options.get("reasoning_effort", field(options.get("reasoning"), "effort"))
            call = dict(call_id=uuid.uuid4().hex, peer_stage=_stage.get(),
                role="main" if model.startswith("gpt-6.1-sol") or re.fullmatch(r"gpt-4[.]1", model) else
                     "light" if model.startswith("gpt-6-luna") or re.fullmatch(r"gpt-4[.]1-mini", model) else UNKNOWN,
                endpoint=endpoint, requested_model=model, returned_model=UNKNOWN,
                reasoning_effort=effort if isinstance(effort, str) and effort in EFFORTS else UNKNOWN,
                returned_reasoning_effort=UNKNOWN, status="in_flight", error_category=None,
                elapsed_ms=None, sdk_retry_count=None, transport_attempt_count=None,
                **usage_metadata(None, endpoint), cost=estimate(model, usage_metadata(None, endpoint), standard_text=False))
            self.calls.append(call)
            return call

    def complete(self, call, *, result=None, error=None, kind=None, raw=None, options=None,
                 elapsed_ms=0, standard_text=False):
        if call is None:
            return
        with self.lock:
            if self.closed:
                return  # Late thread completion cannot alter the returned timeout record.
            retries = counter(getattr(raw, "retries_taken", None))
            if retries is None and error is not None:
                request = getattr(error, "request", None)
                if request is None:
                    request = getattr(getattr(error, "response", None), "request", None)
                value = getattr(request, "headers", {}).get("x-stainless-retry-count")
                retries = int(value) if isinstance(value, str) and re.fullmatch(r"[0-9]{1,3}", value) else None
            # Parse failures may still include billable usage, including truncation.
            source = result or getattr(error, "completion", None)
            if source is None and raw is not None:
                try:
                    body = raw.http_response.json()
                    source = {k:body.get(k) for k in ("model", "usage", "service_tier", "reasoning")}
                except (ValueError, AttributeError, TypeError):
                    source = None
            usage = usage_metadata(field(source, "usage"), call["endpoint"])
            returned = model_name(field(source, "model"))
            effort = field(field(source, "reasoning"), "effort")
            cost = estimate(call["requested_model"], usage,
                standard_text=standard_text and field(source, "service_tier") == "default"
                and returned.startswith(call["requested_model"]) and kind != "model_mismatch")
            call.update(usage, returned_model=returned,
                returned_reasoning_effort=effort if isinstance(effort, str) and effort in EFFORTS else UNKNOWN,
                elapsed_ms=max(0, int(elapsed_ms)), status="completed" if kind is None else "failed",
                error_category=kind if kind in ERRORS else "provider_error" if kind else None,
                sdk_retry_count=retries, transport_attempt_count=retries + 1 if retries is not None else None,
                cost=cost)

    def snapshot(self, status=None):
        with self.lock:
            if status and not self.closed:
                self.closed = True
                self.status = status if status in {"completed", "failed", "timeout", "cancelled"} else "failed"
                self.elapsed_ms = max(0, int((time.monotonic() - self.started) * 1000))
            calls = deepcopy(self.calls)
            complete = len(calls) == self.count and all(c["status"] != "in_flight" for c in calls)
            measured = bool(calls) and complete
            def total(key):
                return sum(c[key] for c in calls) if measured and all(c[key] is not None for c in calls) else None
            cost_known = measured and all(c["cost"]["estimated_cost_usd"] is not None
                                          and c["sdk_retry_count"] == 0 for c in calls)
            return dict(schema_version="wealthpilot.llm-observation.v1", analysis_run_id=self.analysis_run_id,
                scope="unified_generation_adapter_only",
                status=self.status, collection_status="COMPLETE" if complete else "PARTIAL",
                elapsed_ms=self.elapsed_ms if self.closed else int((time.monotonic()-self.started)*1000),
                call_count=self.count, records_truncated=len(calls) != self.count, calls=calls,
                totals=dict(input_tokens=total("input_tokens"), output_tokens=total("output_tokens"),
                    reasoning_tokens=total("reasoning_tokens"), transport_attempt_count=total("transport_attempt_count"),
                    sdk_retry_count=total("sdk_retry_count"),
                    cost_status="ESTIMATE" if cost_known else UNKNOWN,
                    estimated_cost_usd=round(sum(c["cost"]["estimated_cost_usd"] for c in calls), 9) if cost_known else None,
                    actual_bill=UNKNOWN, price_version=PRICE_VERSION,
                    retry_usage_coverage="final_attempt_only"))

    def invalidate(self, call):
        with self.lock:
            if not self.closed and call is not None:
                call.update(status="failed", error_category="observation_unavailable",
                    sdk_retry_count=None, transport_attempt_count=None, **usage_metadata(None, call["endpoint"]))
                call["cost"] = estimate(UNKNOWN, usage_metadata(None, "chat"), standard_text=False)


@contextmanager
def analysis_run(identifier=None):
    existing = _run.get()
    if existing is not None:
        yield existing, False
        return
    trace = AnalysisObservation(identifier)
    token = _run.set(trace)
    try:
        yield trace, True
    finally:
        _run.reset(token)


def current():
    return _run.get()


@contextmanager
def bind_run(trace):
    """Bind only while advancing a stream, never across a yield to its caller."""
    token = _run.set(trace)
    try:
        yield
    finally:
        _run.reset(token)


def unavailable(identifier, *, status="failed", elapsed_ms=0):
    value = AnalysisObservation(identifier).snapshot(status)
    value.update(collection_status="UNAVAILABLE", call_count=None, elapsed_ms=max(0, int(elapsed_ms)))
    for key in ("input_tokens", "output_tokens", "reasoning_tokens", "transport_attempt_count", "sdk_retry_count", "estimated_cost_usd"):
        value["totals"][key] = None
    value["totals"]["cost_status"] = UNKNOWN
    return value


def safe_metadata(value):
    """Project the closed metadata contract at IPC/Explain edges, never echo extras."""
    if not isinstance(value, dict) or value.get("schema_version") != "wealthpilot.llm-observation.v1":
        return None
    try:
        identifier = run_id(value["analysis_run_id"])
        calls = value["calls"]
        if not isinstance(calls, list) or len(calls) > 128:
            return None
        clean = []
        for item in calls:
            if not isinstance(item, dict):
                return None
            row = {"call_id":run_id(item["call_id"])}
            for key, allowed in (("peer_stage", STAGES), ("role", {"main", "light"}),
                    ("endpoint", {"chat", "responses"}), ("reasoning_effort", EFFORTS),
                    ("returned_reasoning_effort", EFFORTS), ("status", {"completed", "failed", "in_flight", "rejected"})):
                val = item.get(key)
                row[key] = val if isinstance(val, str) and val in allowed else UNKNOWN
            for key in ("requested_model", "returned_model"):
                row[key] = model_name(item.get(key))
            kind = item.get("error_category")
            row["error_category"] = kind if isinstance(kind, str) and kind in ERRORS else None
            for key in ("elapsed_ms", "sdk_retry_count", "transport_attempt_count", "input_tokens", "output_tokens", "reasoning_tokens"):
                row[key] = counter(item.get(key))
            row["usage_status"] = "KNOWN" if row["input_tokens"] is not None and row["output_tokens"] is not None else UNKNOWN
            # Cost records are local reference estimates, never arbitrary URLs/text.
            raw_cost = item.get("cost") or {}
            eligible = (isinstance(raw_cost, dict) and raw_cost.get("price_version") == PRICE_VERSION
                        and raw_cost.get("status") == "ESTIMATE"
                        and row["returned_model"].startswith(row["requested_model"])
                        and row["error_category"] != "model_mismatch")
            row["cost"] = estimate(row["requested_model"], row, standard_text=eligible)
            clean.append(row)
        trace = AnalysisObservation(identifier)
        trace.calls, trace.count = clean, counter(value.get("call_count"))
        trace.count = trace.count if trace.count is not None else len(clean)
        trace.closed = True
        trace.status = value.get("status") if value.get("status") in ("completed", "failed", "timeout", "cancelled", "running") else "failed"
        trace.elapsed_ms = counter(value.get("elapsed_ms"))
        result = trace.snapshot()
        if value.get("collection_status") == "UNAVAILABLE":
            return unavailable(identifier, status=trace.status, elapsed_ms=trace.elapsed_ms or 0)
        return result
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def peer_stage(name):
    """Instrument original agents without changing their return contracts."""
    if name not in STAGES:
        raise ValueError("PEER_STAGE_INVALID")
    def decorate(fn):
        if inspect.isasyncgenfunction(fn):
            @wraps(fn)
            async def streaming(*args, **kwargs):
                iterator = fn(*args, **kwargs)
                try:
                    while True:
                        token = _stage.set(name)
                        try:
                            chunk = await anext(iterator)
                        except StopAsyncIteration:
                            break
                        finally:
                            _stage.reset(token)
                        yield chunk
                finally:
                    await iterator.aclose()
            return streaming
        @wraps(fn)
        def synchronous(*args, **kwargs):
            token = _stage.set(name)
            try:
                return fn(*args, **kwargs)
            finally:
                _stage.reset(token)
        return synchronous
    return decorate


def observed_result(fn):
    """MCP/frozen-Web result metadata survives private stderr suppression."""
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        identifier = kwargs.pop("_analysis_run_id", None)
        if identifier is None and args and isinstance(args[0], dict):
            identifier = args[0].get("analysis_run_id")
        with analysis_run(identifier) as (trace, owner):
            try:
                result = await fn(*args, **kwargs)
            except BaseException as error:
                status = "timeout" if isinstance(error, TimeoutError) else "cancelled" if not isinstance(error, Exception) else "failed"
                if owner:
                    error.llm_observation = trace.snapshot(status)
                raise
            status = "timeout" if "ANALYSIS_TIMEOUT" in result.get("issues", []) else "failed" if result.get("status") == "unavailable" else "completed"
            result["llm_observation"] = trace.snapshot(status if owner else None)
            return result
    return wrapped
