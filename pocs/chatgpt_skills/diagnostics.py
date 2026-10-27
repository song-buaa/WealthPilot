"""Closed, content-free normal MCP diagnostics; no business or billing authority."""
from contextvars import ContextVar
import time

from app.llm_observation import run_id, safe_metadata, STAGES as PEER_STAGES, ERRORS as MODEL_ERRORS

ANALYSIS_OPERATIONS = {"analyze_position", "review_portfolio", "analyze_performance"}
STAGES = {"mcp_received", "worker_start", "request_send", "ipc_wait", "request_received",
          "analyze_peer", "result_serialization", "ipc_receive", "pydantic_validation", "mcp_return", "worker_cleanup"}
CATEGORIES = {"NONE", "UNKNOWN", "worker_start_failed", "worker_start_refused", "ipc_timeout", "worker_exited",
              "ipc_io", "invalid_json", "invalid_envelope", "run_id_mismatch", "business_error",
              "business_unavailable", "serialization_failed", "validation_failed", "unexpected_error", "cleanup_failed"}
request_run = ContextVar("mcp_analysis_request_run", default=None)


def diagnostic(identifier, stage, *, category="NONE", status="UNKNOWN", protocol=False,
               elapsed_ms=0, observation=None, exit_code=None):
    """Construct from closed fields only, including at every IPC/metadata edge."""
    meta = safe_metadata(observation)
    if meta and meta["analysis_run_id"] != identifier:
        meta = None
    result = dict(schema_version="wealthpilot.mcp-diagnostic.v1", analysis_run_id=run_id(identifier),
        stage=stage if stage in STAGES else "ipc_receive", protocol_is_error=bool(protocol),
        business_status=status if status in {"ok", "partial", "unavailable"} else "UNKNOWN",
        failure_stage=stage if category != "NONE" and stage in STAGES else None,
        safe_error_category=category if category in CATEGORIES else "UNKNOWN",
        elapsed_ms=max(0, int(elapsed_ms)), model_call_count="UNKNOWN", provider_request_count="UNKNOWN",
        model_count_scope="unified_generation_adapter_only", observed_peer_stages="UNKNOWN",
        model_error_categories="UNKNOWN")
    if meta and type(meta.get("call_count")) is int and meta.get("collection_status") != "UNAVAILABLE":
        result["model_call_count"] = meta["call_count"]
        result["observed_peer_stages"] = sorted({c["peer_stage"] for c in meta["calls"] if c["peer_stage"] in PEER_STAGES})
        result["model_error_categories"] = sorted({c["error_category"] for c in meta["calls"] if c["error_category"] in MODEL_ERRORS})
    if type(exit_code) is int:
        result["worker_exit_code"] = exit_code
    return result


def safe_diagnostic(value, identifier):
    if not isinstance(value, dict) or value.get("schema_version") != "wealthpilot.mcp-diagnostic.v1":
        return None
    if not isinstance(identifier, str) or value.get("analysis_run_id") != identifier:
        return None
    try:
        run_id(identifier)
    except ValueError:
        return None
    if not isinstance(value.get("stage"), str) or value["stage"] not in STAGES:
        return None
    if not isinstance(value.get("safe_error_category"), str) or value["safe_error_category"] not in CATEGORIES:
        return None
    if not isinstance(value.get("business_status"), str):
        return None
    result = diagnostic(identifier, value.get("stage"), category=value.get("safe_error_category"),
        status=value.get("business_status"), protocol=value.get("protocol_is_error") is True,
        elapsed_ms=value.get("elapsed_ms") if type(value.get("elapsed_ms")) is int else 0,
        exit_code=value.get("worker_exit_code"))
    count = value.get("model_call_count")
    if type(count) is int and 0 <= count <= 10**12:
        result["model_call_count"] = count
    for key, allowed in (("observed_peer_stages", PEER_STAGES), ("model_error_categories", MODEL_ERRORS)):
        values = value.get(key)
        if isinstance(values, list) and len(values) <= len(allowed) and all(isinstance(v, str) and v in allowed for v in values):
            result[key] = sorted(set(values))
    return result


def milliseconds(started):
    return int((time.monotonic() - started) * 1000)
