"""Output enforcement shared by original Expressing and MCP final recheck."""
import json
import math


def prompt_context(assessment):
    records = [{k:v for k,v in r.items() if k != "values"} for r in assessment["records"]]
    return "\n确定性证据门槛（优先于旧实时行情模板；未知不得补全）："+json.dumps(
        dict(records=records,qualification=assessment["qualification"],issues=assessment["issues"],
             planner=assessment["planner"],confidence_ceiling=assessment["confidence_ceiling"],
             allocation_context=assessment.get("allocation_context"),scenarios=assessment.get("scenarios"),
             authority="Use frozen cross-account issuer exposure for company concentration. Request target versions are unverified proposals, not confirmed user targets. Formal discipline and defaults are separate; a legacy single-security rule pass does not establish that company targets are met. Static scenarios are assumptions, never orders."),
        ensure_ascii=False,default=str)


def enforce(output, assessment):
    """Model structure/confidence cannot grant qualification to itself."""
    payload = dict(output.structured_payload or {})
    prior = payload.get("confidence")
    try:
        prior = min(max(float(prior),0),1)
        prior = prior if math.isfinite(prior) else 0
    except (ValueError,TypeError):
        prior = 0
    # Numerical recommendations always come from the deterministic plan, even L3.
    decision = payload.get("decisionType", "hold")
    if not isinstance(decision,str) or decision not in {"hold","trim","exit","buy_init","buy_more","watch"}:
        decision = "watch"
    payload = dict(decisionType=decision,recommendedAction="人工复核下述结构化分析计划",
                   is_new_entry=payload.get("is_new_entry") is True)
    from backend.services.position_analysis import exit_plan, report
    plan=exit_plan(assessment)
    output.chat_answer=report(assessment,plan,decision)
    output.raw_text=""
    payload["exitPlan"]=plan
    payload.update(confidence=min(prior,assessment["confidence_ceiling"]),
        infoNeeded=assessment["infoNeeded"],blockers=assessment["infoNeeded"],
        analysisQualification=assessment["qualification"],
        evidenceQuality=dict(issues=assessment["issues"],snapshot_id=assessment["snapshot_id"],
                             confidence_ceiling=assessment["confidence_ceiling"]),
        planner=assessment["planner"])
    output.structured_payload = payload
    output.actionable = False
    output.actionable_hint = None
    if output.llm_result is not None:
        output.llm_result.chat_answer = output.chat_answer
        output.llm_result.structured_result = payload
        output.llm_result.raw_output = ""
        # Explain/store adapters expose these independently from chat_answer.
        # A filtered chat must not leave the original numeric strategy behind.
        for name in ("reasoning", "risk", "strategy"):
            if hasattr(output.llm_result, name):
                setattr(output.llm_result, name, [])
        if hasattr(output.llm_result, "raw_payload"):
            output.llm_result.raw_payload = payload
        if hasattr(output.llm_result, "original_decision"):
            output.llm_result.original_decision = None
        if hasattr(output.llm_result, "decision"):
            output.llm_result.decision = {"trim":"REDUCE","exit":"SELL","buy_init":"BUY","buy_more":"BUY"}.get(decision,"HOLD")
    return output


def enforce_portfolio(output, *, asset_view=None, options=None, loaded=None):
    """Portfolio routing cannot grant single-position L3 authority to model text.

    Keep owner-calculated context and L2 scenarios. There is no portfolio-wide
    research/quote/target-confirmation qualification for precise orders here.
    """
    from backend.services.allocation_scenarios import qualification_view, target_context
    from backend.services.allocation_service import get_targets, simulate_exit_scenario
    from backend.services.market_data.evidence import mapping
    options = options or {}
    view = qualification_view(asset_view, options.get("scenario_legs", [])) if asset_view else None
    rules = mapping(getattr(loaded,"rules",{}))
    provenance = getattr(loaded,"rules_provenance",None) or {"basis":"effective_rules_unverified"}
    formal = rules if provenance.get("basis")=="configured_rule_file" else None
    context = target_context(view, None, options.get("target_versions", []), formal,
                             {"allocation":[t.model_dump(mode="json") for t in get_targets()],
                              "effective_rules":rules,"rule_source":provenance})
    context["effective_rules_provenance"] = provenance
    amount_allowed = bool(view and view.get("context_current") and
        view.get("data_quality", {}).get("analysis_eligibility", {}).get("B", {}).get("allowed"))
    scenarios = None
    if options.get("scenario_legs") and view:
        scenarios = simulate_exit_scenario(view, options["scenario_legs"],
            amount_qualified=amount_allowed, target=context["proposed_stage_targets"])
    payload = dict(mode="restricted_portfolio_analysis", snapshot_id=(view or {}).get("snapshot_id"),
        allocation_context=context, allocation_scenarios=scenarios,
        amount_qualified=amount_allowed, can_propose_rebalance=False, execution_authorized=False,
        blockers=["PORTFOLIO_PRECISE_RECOMMENDATION_QUALIFICATION_UNAVAILABLE"])
    output.structured_payload = payload
    output.chat_answer = ("组合分析仅提供固定资产口径与列明假设的静态情景；当前未建立组合级精确价量资格，"
                          "不能据此准备交易。\n"+json.dumps(payload, ensure_ascii=False, default=str))
    output.raw_text = ""
    output.actionable = False
    output.actionable_hint = None
    if output.llm_result is not None:
        output.llm_result.chat_answer = output.chat_answer
        output.llm_result.raw_payload = payload
        output.llm_result.raw_output = ""
    return output
