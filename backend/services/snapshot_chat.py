"""Snapshot Web chat via an isolated original PEER worker.

Reuses U1 frozen_input and the MCP read-only database boundary. Never rebinds
app.database in the shared Web process and never starts a backend lifespan.
"""
from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import logging
import os
from pathlib import Path
import sys
import uuid
import time
from app.llm_observation import observed_result, run_id, safe_metadata, unavailable


MAX_OUTPUT = 8 * 1024 * 1024
logger = logging.getLogger(__name__)


def _sse(kind, payload):
    return "event: " + kind + "\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _worker_environment(database_path):
    # Same server provider configuration, but analysis has no write/trade grant.
    env = dict(os.environ)
    env.update(PYTHON_DOTENV_DISABLED="1", WEALTHPILOT_DB_PATH=str(database_path),
        WEALTHPILOT_EPHEMERAL_ANALYSIS="true", BROKER_MODE="mock",
        IBKR_READ_ONLY_MODE="true", ENABLE_IBKR_LIVE_TRADING="false",
        ENABLE_TIGER_LIVE_TRADING="false", FUTU_READ_ONLY_MODE="true")
    return env


async def _invoke_worker(payload):
    payload = dict(payload, analysis_run_id=run_id(payload.get("analysis_run_id")))
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pocs.chatgpt_skills.analysis", "--snapshot-chat-worker",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, env=_worker_environment(payload["database_path"]),
        cwd=str(Path(__file__).resolve().parents[2]))
    try:
        output, _ = await asyncio.wait_for(process.communicate(json.dumps(payload).encode()), timeout=270)
        if process.returncode or len(output) > MAX_OUTPUT:
            raise ValueError("SNAPSHOT_ANALYSIS_UNAVAILABLE")
        result = json.loads(output)
        if not isinstance(result, dict) or result.get("ok") is not True:
            error = ValueError("SNAPSHOT_ANALYSIS_UNAVAILABLE")
            error.llm_observation = safe_metadata(result.get("llm_observation")) if isinstance(result, dict) else None
            if error.llm_observation and error.llm_observation["analysis_run_id"] != payload["analysis_run_id"]:
                raise ValueError("ANALYSIS_RUN_ID_MISMATCH")
            raise error
        metadata = safe_metadata(result["result"].get("llm_observation"))
        if metadata and metadata["analysis_run_id"] != payload["analysis_run_id"]:
            raise ValueError("ANALYSIS_RUN_ID_MISMATCH")
        return result["result"]
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def _owner_evidence_current(assessments, portfolio_id, *, at=None):
    """IPC adds latency: recheck original source clocks without another model.

    Assessment records come only from the isolated owner, never request options.
    A newly stale citation rejects the projected result, rather than upgrading or
    partially repairing its numerical plan in the Web process.
    """
    from datetime import datetime, timezone
    from backend.services.market_data.evidence import record, fundamental_field_quality, technical_verified, technical_period_quality, stamp
    from backend.services.stage_targets import resolve_trusted_targets
    if not isinstance(assessments, list):
        return False
    at = at or datetime.now(timezone.utc)
    try:
        for assessment in assessments:
            symbol = assessment.get("symbol")
            for original in assessment.get("records", []):
                kind, values = original["kind"], original["values"]
                if original.get("quality") == "valid":
                    checked = record(kind, values, at=at, symbol=symbol)
                    if checked["quality"] != "valid":
                        return False
                    if kind == "technical" and not technical_verified(values, symbol, at):
                        return False
                if kind == "technical" and original.get("period_quality"):
                    checked_periods=technical_period_quality(values,symbol,at)
                    if any(state["quality"]=="valid" and checked_periods.get(period,{}).get("quality")!="valid"
                           for period,state in original["period_quality"].items()):
                        return False
                if kind == "fundamentals" and original.get("field_quality"):
                    checked_fields = fundamental_field_quality(values, at=at, symbol=symbol)
                    if any(state["quality"] == "valid" and checked_fields.get(field, {}).get("quality") != "valid"
                           for field, state in original["field_quality"].items()):
                        return False
            context = assessment.get("allocation_context", {})
            original_at = stamp(assessment.get("as_of"))
            if original_at:
                for security in context.get("security_inputs", []):
                    for kind, key in (("trade_unit", "trade_unit_evidence"), ("exit_cost", "exit_cost_evidence")):
                        value = security.get(key)
                        if (value and record(kind, value, at=original_at, symbol=security.get("security_symbol"))["quality"] == "valid"
                                and record(kind, value, at=at, symbol=security.get("security_symbol"))["quality"] != "valid"):
                            return False
            confirmed = context.get("confirmed_stage_targets")
            proposal = context.get("proposed_stage_targets")
            target = confirmed or proposal
            if target and (not stamp(target.get("valid_until")) or stamp(target["valid_until"]) <= at):
                return False
            if confirmed:
                current = resolve_trusted_targets(portfolio_id, at=at)
                if not current or current["digest"] != confirmed.get("digest"):
                    return False
    except (ValueError, TypeError, KeyError, OSError):
        return False
    return True


def _projection_current(projected, view, options, portfolio_id, *, amount_scope=None, at=None):
    """Recheck repayment-scope B and target authority after child IPC latency."""
    from backend.services.allocation_scenarios import qualification_view
    legs = list((options or {}).get("scenario_legs", []))
    contexts = []
    qualified = False
    def visit(value):
        nonlocal qualified
        if isinstance(value, dict):
            qualified |= value.get("mode") == "qualified_static_scenario"
            if isinstance(value.get("allocation_context"), dict):
                contexts.append({"allocation_context":value["allocation_context"], "records":[]})
            if isinstance(value.get("legs"), list):
                legs.extend(value["legs"])
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(projected)
    if not _owner_evidence_current(contexts, portfolio_id, at=at):
        return False
    if amount_scope is not None:
        legs = amount_scope["legs"]
        qualified |= amount_scope["allowed"]
    if qualified:
        scoped = qualification_view(view, legs, at=at) if at is not None else qualification_view(view, legs)
        return bool(scoped.get("context_current") and scoped.get("data_quality", {}).get("analysis_eligibility", {}).get("B", {}).get("allowed"))
    return True


async def stream_snapshot_chat(message, conversation_id, portfolio_id, view, *, options=None):
    """Parent adapter: only server-selected DB/config paths cross the process edge."""
    from app import database
    from backend.services.decision_service_v3 import _final_analysis_view
    from backend.services import decision_service
    payload = dict(database_path=str(database.DB_PATH),
        contract=os.environ.get("WEALTHPILOT_SQLITE_CONTRACT"),
        portfolio_id=portfolio_id, snapshot_id=view["snapshot_id"], message=message,
        conversation_id=conversation_id, options=options or {})
    payload["analysis_run_id"] = run_id()
    started = time.monotonic()
    initial = _final_analysis_view(view)
    if initial.get("portfolio_id") != portfolio_id or not initial.get("context_current"):
        yield _sse("error", {"code":"ANALYSIS_CONTEXT_EXPIRED", "message":"固定资产视图无效或已过期，请重新准备。"})
        return
    yield _sse("stage", {"stage":"loading", "label":"在固定资产快照上进行只读分析…"})
    try:
        try:
            payload["conversation_history"] = await asyncio.to_thread(
                decision_service.get_conversation_history, conversation_id)
        except Exception:
            logger.debug("Snapshot conversation history unavailable", exc_info=True)
            payload["conversation_history"] = []
        result = await _invoke_worker(payload)
        observation = safe_metadata(result.get("llm_observation"))
        if observation and observation["analysis_run_id"] != payload["analysis_run_id"]:
            raise ValueError("ANALYSIS_RUN_ID_MISMATCH")
        if (result.get("snapshot_id") != view["snapshot_id"] or result.get("portfolio_id") != portfolio_id):
            raise ValueError("SNAPSHOT_ANALYSIS_UNAVAILABLE")
        final = _final_analysis_view(view)
        precision_expired = (initial["data_quality"].get("can_propose_rebalance")
            and not final["data_quality"].get("can_propose_rebalance"))
        amount_expired = (initial["data_quality"].get("analysis_eligibility", {}).get("B", {}).get("allowed")
            and not final["data_quality"].get("analysis_eligibility", {}).get("B", {}).get("allowed"))
        if (not final.get("context_current") or precision_expired or amount_expired
                or not _owner_evidence_current(result.get("owner_assessments"), portfolio_id)):
            yield _sse("error", {"code":"ANALYSIS_CONTEXT_EXPIRED", "message":"分析期间资产证据到期，请重新准备后复核。"})
            return
        answer = result.get("answer")
        projected = result.get("analysis_result")
        if not isinstance(answer, str) or not isinstance(projected, dict):
            raise ValueError("SNAPSHOT_ANALYSIS_UNAVAILABLE")
        if not _projection_current(projected, final, options, portfolio_id, amount_scope=result.get("amount_scope")):
            yield _sse("error", {"code":"ANALYSIS_CONTEXT_EXPIRED", "message":"情景所需资产或目标证据到期，请重新复核。"})
            return
        decision_id = "snapshot_" + uuid.uuid4().hex[:16]
        intent = result.get("intent") or {"primary_intent":"PositionDecision"}
        # Store only the worker's final projection, never raw model/data objects.
        explain = dict(decision_id=decision_id, conversation_id=conversation_id,
            analysisSnapshot=view["snapshot_id"], intent=intent,
            data={"snapshot_id":view["snapshot_id"], "portfolio_id":portfolio_id},
            llm={"chat_answer":answer, "structured_result":projected, "raw_output":"",
                 "reasoning":[], "risk":[], "strategy":[]},
            analysis_result=projected, actionable=False, llm_observation=observation)
        decision_service._ALLOC_EXPLAIN_STORE[f"{conversation_id}:{decision_id}"] = explain
        from backend.services.explain_policy import remember
        remember(conversation_id, decision_id, portfolio_id=portfolio_id,
            assessments=result.get("owner_assessments"), projected=projected, asset_view=final,
            options=options, amount_scope=result.get("amount_scope"))
        assistant_message_id = None
        try:
            assistant_message_id = await asyncio.to_thread(
                decision_service.save_conversation_turn, conversation_id, message, answer,
                intent.get("primary_intent"), None,
                assistant_metadata={"analysis_snapshot":view["snapshot_id"], "analysis_only":True,
                                    **({"llm_observation":observation} if observation else {})})
        except Exception:
            # Match the original best-effort chat persistence contract. The
            # child remains asset-read-only and raw model output is never saved.
            logger.debug("Snapshot final conversation save unavailable", exc_info=True)
        yield _sse("intent", intent)
        yield _sse("text", {"delta":answer})
        yield _sse("done", dict(decision_id=decision_id, decisionResult=projected,
            analysisSnapshot=view["snapshot_id"], assistant_message_id=assistant_message_id,
            actionable=False, actionable_hint=None,
            conclusion_level="snapshot_analysis", conclusion_label="固定快照只读分析",
            llm_observation=observation))
    except (OSError, ValueError, KeyError, TypeError, asyncio.TimeoutError) as error:
        observation = safe_metadata(getattr(error, "llm_observation", None))
        if observation and observation["analysis_run_id"] != payload["analysis_run_id"]:
            observation = None
        observation = observation or unavailable(
            payload["analysis_run_id"], status="timeout" if isinstance(error, TimeoutError) else "failed",
            elapsed_ms=int((time.monotonic()-started)*1000))
        yield _sse("error", {"code":"SNAPSHOT_ANALYSIS_UNAVAILABLE", "message":"固定快照分析未完成，请稍后重试；未创建交易。",
                             "llm_observation":observation})


@observed_result
async def _run_frozen_chat(payload):
    """Child only. All original service reads use the same immutable SQLite."""
    from app.asset_views import load_view, frozen_input
    from backend.services.decision_service_v3 import _final_analysis_view
    from pocs.chatgpt_skills.analysis import readonly_business_database
    from backend.services.decision_service_v3 import run_chat_stream_v3
    from backend.services import decision_service
    from backend.services.position_analysis import prepare, prepare_portfolio
    from backend.services.market_data.output_policy import enforce, enforce_portfolio
    from backend.services.allocation_scenarios import ScenarioLeg, StageTargets
    from app.database import get_session
    from app.models import Portfolio
    from sqlalchemy import select
    expected = {"database_path", "contract", "portfolio_id", "snapshot_id", "message", "conversation_id", "conversation_history", "options"}
    if not isinstance(payload, dict) or set(payload) - {"analysis_run_id"} != expected:
        raise ValueError("SNAPSHOT_WORKER_REQUEST_INVALID")
    pid = payload["portfolio_id"]
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("SNAPSHOT_WORKER_REQUEST_INVALID")
    history = payload["conversation_history"]
    if (not isinstance(history, list) or any(not isinstance(turn, dict)
            or set(turn) != {"role", "content"}
            or turn["role"] not in {"system", "user", "assistant"}
            or not isinstance(turn["content"], str) for turn in history)):
        raise ValueError("SNAPSHOT_WORKER_HISTORY_INVALID")
    options = payload["options"]
    if not isinstance(options, dict) or set(options) - {"scenario_legs", "target_versions"}:
        raise ValueError("SNAPSHOT_WORKER_REQUEST_INVALID")
    for raw in options.get("target_versions", []):
        StageTargets.model_validate(raw)
    for raw in options.get("scenario_legs", []):
        if ScenarioLeg.model_validate(raw).snapshot_id != payload["snapshot_id"]:
            raise ValueError("CROSS_SNAPSHOT_SCENARIO_REJECTED")
    view = load_view(payload["database_path"], pid, payload["snapshot_id"], payload["contract"])
    captured = {}
    text = []
    done = {}
    errors = []
    def capture(plan, executed, expressed, reviewed):
        captured.update(plan=plan, executed=executed, expressed=expressed)
    with frozen_input(view) as fixed_path:
        with readonly_business_database(fixed_path, worker_process=True):
            with get_session() as session:
                if session.scalar(select(Portfolio.id).where(Portfolio.id == pid)) is None:
                    raise ValueError("PORTFOLIO_NOT_FOUND")
            async for event in run_chat_stream_v3(payload["message"], payload["conversation_id"], pid,
                    conversation_history=history, result_callback=capture, analysis_only=True,
                    analysis_context=_final_analysis_view(view), analysis_options=options):
                kind = next((line[7:] for line in event.splitlines() if line.startswith("event: ")), "")
                data = next((json.loads(line[6:]) for line in event.splitlines() if line.startswith("data: ")), {})
                if kind == "text":
                    text.append(data.get("delta", ""))
                elif kind == "done":
                    done = data
                elif kind == "error":
                    errors.append(data)
            if errors:
                raise ValueError("SNAPSHOT_ANALYSIS_UNAVAILABLE")
            final_view = _final_analysis_view(view)
            if not final_view["context_current"]:
                raise ValueError("ANALYSIS_CONTEXT_EXPIRED")
            # The same final owner policies as MCP; no model or provider re-run.
            owner_assessments = []
            if captured:
                plan, executed, expressed = captured["plan"], captured["executed"], captured["expressed"]
                if plan.route == "position_single":
                    assessment = prepare(plan, executed, asset_view=final_view, options=options)
                    enforce(expressed, assessment)
                    owner_assessments = [assessment]
                elif plan.route == "portfolio":
                    assessments = prepare_portfolio(plan, executed)
                    owner_assessments = assessments
                    enforce_portfolio(expressed, asset_view=final_view, options=options,
                        loaded=executed.loaded_data, evidence_assessments=assessments)
                else:
                    # Snapshot research is bounded to the professional paths.
                    return dict(snapshot_id=view["snapshot_id"], portfolio_id=pid,
                        answer="请明确需要分析的单标、多标或组合问题。",
                        analysis_result={"mode":"clarification_required", "actionable":False},
                        owner_assessments=[], intent={"primary_intent":"Education"})
                answer = expressed.chat_answer
                projected = expressed.structured_payload or {}
                primary = plan.intent.get("primary_intent", "PositionDecision") if isinstance(plan.intent, dict) else "PositionDecision"
            elif done.get("position_qualifications") is not None:
                # Multi-position uses the original per-position final gates.
                # Project its already-filtered stored results, not free text/LLM.
                items = []
                for result in decision_service._DECISION_STORE.get(payload["conversation_id"], {}).values():
                    llm = getattr(result, "llm", None)
                    assessment = getattr(result, "_position_evidence", None)
                    if llm is not None and assessment and assessment.get("snapshot_id") == view["snapshot_id"]:
                        owner_assessments.append(assessment)
                        items.append({"symbol":assessment["symbol"], "analysis_result":deepcopy(llm.structured_result or {})})
                projected = dict(mode="multi_snapshot_analysis", items=items, actionable=False)
                answer = "".join(text)
                primary = "PositionDecision"
            else:
                projected = {"mode":"clarification_required", "actionable":False}
                answer = "请补充明确的分析标的或组合问题；固定快照未生成交易建议。"
                primary = "Education"
            from backend.services.allocation_scenarios import qualification_view
            scope_legs = list(options.get("scenario_legs", []))
            for assessment in owner_assessments:
                scope_legs.extend(leg for candidate in assessment.get("exit_candidates", []) for leg in candidate.get("legs", []))
            scope_legs.extend(leg for candidate in projected.get("exit_candidates", []) for leg in candidate.get("legs", []))
            scoped = qualification_view(final_view, scope_legs)
            amount_scope = {"legs":scope_legs, "allowed":bool(scoped.get("data_quality", {}).get("analysis_eligibility", {}).get("B", {}).get("allowed"))}
            return dict(snapshot_id=view["snapshot_id"], portfolio_id=pid, answer=answer,
                analysis_result=deepcopy(projected), owner_assessments=owner_assessments, amount_scope=amount_scope, intent={"primary_intent":primary, "action":None,
                    "needs_clarification":projected.get("mode")=="clarification_required"})


def worker_main():
    """One-shot private protocol, reached only by the explicit module CLI flag."""
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("SNAPSHOT_WORKER_REQUEST_INVALID")
        payload = json.loads(raw)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = asyncio.run(_run_frozen_chat(payload))
        response = {"ok":True, "result":result}
    except Exception as error:
        response = {"ok":False, "error":"SNAPSHOT_ANALYSIS_UNAVAILABLE",
                    "llm_observation":safe_metadata(getattr(error, "llm_observation", None))}
    sys.stdout.write(json.dumps(response, ensure_ascii=False, default=str))
