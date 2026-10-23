"""Opt-in, single-process real PEER acceptance; never starts production services.

Requires separate operator authorization to run. --help performs no business IO.
Uses one existing U1 view and one durable private budget for all three stocks.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def private_write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.flush(); os.fsync(stream.fileno())


def final_record(symbol, result, metadata):
    # Original single-position SSE/MCP contract nests the final projection
    # under decisionResult. Never inspect rawText/raw_output for explanations.
    decision = result.get("professional_result", {}).get("decisionResult") or {}
    return dict(symbol=symbol, status=result["status"], issues=result["issues"],
        conclusion=result["conclusion"], professional_explanation=decision.get("professional_explanation"),
        analysis_qualification=decision.get("analysisQualification"),
        explanation_diagnostics=decision.get("explanationDiagnostics"), llm_observation=metadata)


async def run_campaign(database, portfolio_id, snapshot_id, budget, *, contract=None, synthetic=False):
    from app.asset_views import load_view, frozen_input
    from app.llm_acceptance import SYMBOLS
    from app.llm_observation import analysis_run, safe_metadata
    from pocs.chatgpt_skills.analysis import analyze
    results = []
    # Preflight the original TTL/cache/hash before claiming a run or sending.
    view = load_view(database, portfolio_id, snapshot_id, contract)
    with frozen_input(view):
        pass
    for symbol in SYMBOLS:
        with analysis_run() as (trace, _):
            budget.claim(symbol, trace.analysis_run_id)
            try:
                result = await analyze(database, portfolio_id, "analyze_position",
                    dict(symbol=symbol, snapshot_id=snapshot_id,
                        question=f"评估{symbol}经营、技术趋势及减仓逻辑；仅研究，披露缺失和冲突，不执行交易。"),
                    contract=contract, synthetic=synthetic, worker_process=True)
                metadata = trace.snapshot("completed")
                # Final business projection only, not raw model output, evidence
                # contexts, full holdings or provider responses. Keep private.
                results.append(final_record(symbol, result, safe_metadata(metadata)))
            except Exception as error:
                from app.asset_views import ERRORS
                code = str(error) if str(error) in ERRORS else "ACCEPTANCE_ANALYSIS_STOPPED"
                results.append(dict(symbol=symbol, status="unavailable", issues=[code],
                    llm_observation=safe_metadata(trace.snapshot("failed"))))
                break  # Preserve earlier final results; never replay a stock.
        if result.get("status") == "unavailable":
            break
        if budget.closed or len(budget.snapshot()["attempts"]) >= budget.max_attempts:
            break
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--portfolio-id", type=int, required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--operator-env", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-paid-acceptance", action="store_true", required=True)
    args = parser.parse_args()
    for name in ("database", "contract", "operator_env", "ledger", "output"):
        setattr(args, name, os.path.abspath(getattr(args, name)))
    os.chdir(ROOT)
    # Fail before imports or any SDK construction if the output cannot be new.
    output = Path(args.output)
    if output.exists() or output.is_symlink():
        raise SystemExit("ACCEPTANCE_OUTPUT_MUST_BE_NEW")
    operator = Path(args.operator_env)
    info = operator.lstat()
    if (operator.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise SystemExit("ACCEPTANCE_OPERATOR_FILE_INVALID")
    os.environ.update(PYTHON_DOTENV_DISABLED="1", WEALTHPILOT_EPHEMERAL_ANALYSIS="true",
        IBKR_READ_ONLY_MODE="true", ENABLE_IBKR_LIVE_TRADING="false", ENABLE_TIGER_LIVE_TRADING="false",
        FUTU_READ_ONLY_MODE="true", BROKER_MODE="mock", PUBLIC_DEMO_MODE="false")
    os.environ.pop("OPENAI_API_KEY", None)
    from pocs.chatgpt_skills.worker import load_analysis_environment
    load_analysis_environment(operator, model_policy_only=True)
    load_analysis_environment(operator)
    if not os.environ.get("WEALTHPILOT_OPENAI_API_KEY"):
        raise SystemExit("ACCEPTANCE_OPENAI_KEY_UNAVAILABLE")
    import openai
    if openai.__version__ != "2.29.0" or os.getenv("WEALTHPILOT_LLM_PROFILE", "gpt6") != "gpt6":
        raise SystemExit("ACCEPTANCE_SDK_OR_PROFILE_UNSUPPORTED")
    from app.llm_acceptance import Budget, guarded_transport
    from app.llm_config import model_for, SOL, LUNA
    if (model_for("main"), model_for("light")) != (SOL, LUNA):
        raise SystemExit("ACCEPTANCE_MODEL_UNSUPPORTED")
    budget = Budget(args.ledger)
    receipt = {"status":"BLOCKED", "results":[], "actual_bill":"UNKNOWN"}
    # Match the private worker: drop stdout/stderr/logging, not capture StringIO
    # containing private prompts. Never expose provider errors or a traceback.
    logging.disable(logging.CRITICAL)
    try:
        with guarded_transport(budget, process_lifetime=True), open(os.devnull, "w") as null:
            with contextlib.redirect_stdout(null), contextlib.redirect_stderr(null):
                receipt["results"] = asyncio.run(run_campaign(args.database, args.portfolio_id,
                    args.snapshot_id, budget, contract=args.contract))
                receipt["status"] = ("COMPLETED" if len(receipt["results"]) == 3
                    and all(r["status"] != "unavailable" for r in receipt["results"]) else "PARTIAL")
    except Exception as error:
        # Safe exact error detail remains available in final observation / ledger;
        # generic failure never dumps an exception containing private context.
        receipt["status"] = "BLOCKED"
        from app.asset_views import ERRORS
        receipt["error_code"] = str(error) if str(error) in ERRORS else "ACCEPTANCE_ANALYSIS_FAILED"
    receipt["budget"] = budget.snapshot()
    private_write(output, receipt)
    print(json.dumps(dict(status=receipt["status"], attempt_count=receipt["budget"]["attempt_count"],
        reserved_usd=receipt["budget"]["reserved_usd"], actual_bill="UNKNOWN")))
    # Timed-out non-daemon threads must not keep this dedicated campaign alive.
    sys.stdout.flush()
    os._exit(0 if receipt["status"] == "COMPLETED" else 2)


if __name__ == "__main__":
    main()
