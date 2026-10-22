"""Freshness projection for cached 4.2 Explain reports.

Only server-created metadata lives here. Reads recheck the same owner inputs;
no model, provider, asset lookup, broker or persistent mutation is performed.
"""
from copy import deepcopy
from datetime import datetime, timezone


_CONTEXTS = {}


def _now():
    return datetime.now(timezone.utc)


def remember(conversation_id, decision_id, *, portfolio_id, assessments, projected,
             asset_view=None, options=None, amount_scope=None):
    """Called only by the original final-response stores, never request parsing."""
    _CONTEXTS[(conversation_id, decision_id)] = deepcopy(dict(
        generated_at=_now().isoformat(), portfolio_id=portfolio_id,
        assessments=assessments, projected=projected, asset_view=asset_view,
        options=options or {}, amount_scope=amount_scope))


def clear(conversation_id):
    for key in [key for key in _CONTEXTS if key[0] == conversation_id]:
        _CONTEXTS.pop(key, None)


def _is_v42(value):
    if isinstance(value, dict):
        if any(key in value for key in ("analysisQualification", "professional_explanation", "exitPlan", "analysisSnapshot")):
            return True
        if value.get("mode") in {"restricted_portfolio_analysis", "multi_snapshot_analysis"}:
            return True
        return any(_is_v42(item) for item in value.values())
    return isinstance(value, list) and any(_is_v42(item) for item in value)


def _current(context, at):
    from backend.services.market_data.evidence import stamp
    from backend.services.snapshot_chat import _owner_evidence_current, _projection_current
    from backend.services.decision_service_v3 import _final_analysis_view
    if not isinstance(context, dict):
        return False
    generated = stamp(context.get("generated_at"))
    if generated is None or generated > at or not isinstance(context.get("assessments"), list):
        return False
    pid = context.get("portfolio_id")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        view = deepcopy(context["asset_view"])
        def requires_view(value):
            if isinstance(value, dict):
                levels = value.get("analysisQualification", {})
                if (any(levels.get(level, {}).get("allowed") for level in ("L2", "L3", "L4"))
                        or value.get("amount_qualified") or value.get("mode") == "qualified_static_scenario"):
                    return True
                return any(requires_view(item) for item in value.values())
            return isinstance(value, list) and any(requires_view(item) for item in value)
        if view is None and requires_view(context["projected"]):
            return False
        if view is not None:
            if not isinstance(view, dict) or not view.get("captured_at") or view.get("portfolio_id") != pid:
                return False
            initial_quality = view.get("data_quality", {})
            view = _final_analysis_view(view)
            current_quality = view.get("data_quality", {})
            if (not view.get("context_current")
                    or (initial_quality.get("can_propose_rebalance") and not current_quality.get("can_propose_rebalance"))
                    or (initial_quality.get("analysis_eligibility", {}).get("B", {}).get("allowed")
                        and not current_quality.get("analysis_eligibility", {}).get("B", {}).get("allowed"))):
                return False
        if not _owner_evidence_current(context["assessments"], pid, at=at):
            return False
        return _projection_current(context["projected"], view, context["options"], pid,
            amount_scope=context.get("amount_scope"), at=at)
    except (ValueError, TypeError, KeyError, OSError, AttributeError):
        return False


def _historical(value, context, at):
    """Do not leave expired prices, quantities or model paragraphs in old fields."""
    context = context or {}
    view = context.get("asset_view") or {}
    source_metadata = []
    for assessment in context.get("assessments") or []:
        for record in assessment.get("records", []):
            source_metadata.append({key:record.get(key) for key in
                ("kind", "symbol", "source", "source_url", "record_id", "published_at", "data_as_of", "fetched_at", "period")})
    snapshot_id = view.get("snapshot_id") or value.get("analysisSnapshot")
    message = "历史分析记录：原证据、目标或资产上下文已过期或无法重新核验；原推论与精确方案已停止展示。请重新分析后复核，本记录不具备当前调仓或交易资格。"
    blockers = ["EXPLAIN_EVIDENCE_EXPIRED_OR_UNVERIFIED"]
    qualification = {level:dict(allowed=False, blockers=blockers) for level in ("L1", "L2", "L3", "L4")}
    projected = dict(mode="historical_analysis", snapshot_id=snapshot_id,
        analysisQualification=qualification, can_propose_rebalance=False,
        execution_authorized=False, professional_explanation=[], blockers=blockers)
    return dict(decision_id=value.get("decision_id"), conversation_id=value.get("conversation_id"),
        stage="historical", was_aborted=False, aborted_reason=None,
        analysisSnapshot=snapshot_id, analysis_result=projected,
        data=dict(snapshot_id=snapshot_id, portfolio_id=context.get("portfolio_id"), source_metadata=source_metadata),
        llm=dict(chat_answer=message, reasoning=[], risk=[], strategy=[], raw_output="",
            original_decision=None, structured_result=projected),
        generic_llm=dict(chat_answer=message, is_fallback=False, error=None),
        actionable=False, actionable_hint=None,
        analysisFreshness=dict(status="historical", generated_at=context.get("generated_at"),
            checked_at=at.isoformat(), blockers=blockers))


def project(conversation_id, decision_id, value, *, at=None):
    """Legacy 4.1 Explain stays unchanged; 4.2 never assumes missing context current."""
    if not isinstance(value, dict):
        return value
    context = _CONTEXTS.get((conversation_id, decision_id))
    if context is None and not _is_v42(value):
        return value
    at = at or _now()
    if not _current(context, at):
        return _historical(value, context, at)
    result = deepcopy(value)
    # Explain's legacy diagnostic arrays are the original unfiltered Loader/RAG
    # objects. They are not current facts and must not bypass the final owner
    # projection, even while the cited professional report is still fresh.
    if isinstance(result.get("data"), dict):
        for name in ("research", "retrieved_research_views", "retrieved_principles"):
            if name in result["data"]:
                result["data"][name] = []
    result["analysisFreshness"] = dict(status="current", generated_at=context["generated_at"],
        checked_at=at.isoformat(), execution_authorized=False)
    return result
