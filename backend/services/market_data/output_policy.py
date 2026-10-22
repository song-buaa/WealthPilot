"""Output enforcement shared by original Expressing and MCP final recheck."""
import json


def prompt_context(assessment):
    records = [{k:v for k,v in r.items() if k != "values"} for r in assessment["records"]]
    return "\n确定性证据门槛（优先于旧实时行情模板；未知不得补全）："+json.dumps(
        dict(records=records,qualification=assessment["qualification"],issues=assessment["issues"],
             planner=assessment["planner"],confidence_ceiling=assessment["confidence_ceiling"]),
        ensure_ascii=False,default=str)


def enforce(output, assessment):
    """Model structure/confidence cannot grant qualification to itself."""
    payload = dict(output.structured_payload or {})
    prior = payload.get("confidence")
    try:
        prior = min(max(float(prior),0),1)
    except (ValueError,TypeError):
        prior = 0
    if not assessment["qualification"]["L3"]["allowed"]:
        # Remove ALL unverified model numerical fields, including nested snapshots,
        # free-form rationale, position size and stale valuation displays.
        decision = payload.get("decisionType", "hold")
        if decision not in {"hold","trim","exit","buy_init","buy_more","watch"}:
            decision = "watch"
        payload = dict(decisionType=decision,recommendedAction="仅供方向性研究，待补证后复核",
                       is_new_entry=bool(payload.get("is_new_entry",False)))
        output.chat_answer = ("单标方向性研究："+decision+"。证据未达到精确减仓资格，不提供价格或股数。\n"
            +"待补证："+"、".join(assessment["infoNeeded"]))
        output.raw_text = ""
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
    return output
