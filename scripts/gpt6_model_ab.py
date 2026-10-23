"""Bounded, opt-in model A/B using PR #40 public frozen evidence only.

No application startup, broker, real holdings, database, or provider refresh.
The HTTP hook counts every attempt, including SDK retries, before dispatch.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PRICES = {"gpt-4.1":(2,8), "gpt-4.1-mini":(.4,1.6), "gpt-6.1-sol":(2,10), "gpt-6-luna":(.1,.5)}
FIXTURES = ROOT / "tests/fixtures/v42_real_research"


class Budget:
    def __init__(self, calls=24, dollars=3):
        if not 1 <= calls <= 24 or not 0 < dollars <= 3:
            raise ValueError("AB_LIMIT_INVALID")
        self.calls, self.dollars = calls, dollars
        self.reserved = 0.0
        self.attempts = []
        self.exhausted = False

    def before_send(self, request):
        if request.url.host != "api.openai.com" or request.url.path != "/v1/chat/completions":
            raise RuntimeError("AB_ENDPOINT_NOT_ALLOWED")
        body = json.loads(request.content)
        model = body["model"]
        if model not in PRICES:
            raise RuntimeError("AB_MODEL_NOT_ALLOWED")
        # UTF-8 bytes plus framing conservatively bound text-only input tokens.
        input_bound = len(request.content) + 1024
        output_bound = body.get("max_completion_tokens", body.get("max_tokens", 0))
        if not output_bound or body.get("tools") or body.get("n", 1) != 1:
            raise RuntimeError("AB_UNBOUNDED_REQUEST")
        rates = PRICES[model]
        # New-model cache writes can cost 1.25x uncached input. Apply the
        # margin to the entire reservation, including unknown failed usage.
        reserve = 1.25 * (input_bound * rates[0] + output_bound * rates[1]) / 1_000_000
        if len(self.attempts) >= self.calls or self.reserved + reserve > self.dollars:
            self.exhausted = True
            raise RuntimeError("AB_BUDGET_EXHAUSTED")
        self.reserved += reserve
        self.attempts.append(dict(model=model, reserved_usd=round(reserve,6)))


def assessment(symbol, empty=False):
    from backend.services.market_data.evidence import assess
    source = json.loads((FIXTURES / ("sources-" + symbol.replace(":", "-") + ".json")).read_text())
    at = datetime.fromisoformat(source["finished_at"])
    # Original missing financial publication clocks stay missing. Do not make
    # operating evidence up to populate the comparison's business-thesis cell.
    return assess({} if empty else {"technical":source["technical"]}, symbol=symbol, at=at)


def completion_metrics(response):
    usage = response.usage
    model = response.model
    rate_key = next((name for name in sorted(PRICES,key=len,reverse=True) if model.startswith(name)), None)
    if usage is None or rate_key is None:
        return {"usage_status":"unknown", "returned_model":model}
    inp, out = usage.prompt_tokens, usage.completion_tokens
    reasoning = getattr(getattr(usage,"completion_tokens_details",None),"reasoning_tokens",None)
    rates = PRICES[rate_key]
    return dict(usage_status="reported", returned_model=model, input_tokens=inp, output_tokens=out,
                reasoning_tokens=reasoning, estimated_usd=round((inp*rates[0]+out*rates[1])/1_000_000,6))


def run_case(client, budget, symbol, stage, model, empty=False):
    from app.llm_config import chat_completion, LLMCallError
    from intent_engine.intent_recognizer import _SYSTEM_PROMPT, _extract_json, _build_payload, _validate
    from backend.agents.reviewing_agent import _REVIEWING_PROMPT
    from backend.services.market_data.output_policy import explanation_prompt, explanation_projection
    evidence = assessment(symbol, empty)
    question = f"请评估{symbol}是否应逐步减仓，解释依据与局限；仅研究，不执行交易。"
    options = dict(model=model, max_tokens=2000, messages=[])
    if stage == "planning":
        options.update(messages=[{"role":"system","content":_SYSTEM_PROMPT},{"role":"user","content":question}],max_tokens=512)
    elif stage == "reviewing":
        sample = ("证据存在缺失，只能解释趋势与限制，不具备精确退出资格。" if not empty
                  else "建议立即以现价卖出一百股，缺少证据也可以精确决策。")
        options.update(messages=[{"role":"user","content":_REVIEWING_PROMPT.format(user_query=question,
            intent_type="PositionDecision",failed_rules="无" if not empty else "无合格证据；禁止精确数量和交易价格",chat_answer=sample)}],
            max_tokens=300,reasoning_effort="low")
    else:
        # Same current PEER professional-explanation contract and final owner.
        options.update(messages=[{"role":"system","content":
            "你是WealthPilot专业投研分析师。只输出JSON对象，professional_explanation数组为定性解释；"
            "解释逻辑、竞争风险和反证条件。不可用证据不得补造经营事实。" + explanation_prompt(evidence)},
            {"role":"user","content":question}],max_tokens=2000)
    os.environ["WEALTHPILOT_LLM_PROFILE"] = "legacy-4.1" if model.startswith("gpt-4.1") else "gpt6"
    row = dict(symbol=symbol,stage=stage,requested_model=model,case="no_evidence" if empty else "frozen_real",
               qualification=evidence["qualification"],operating_evidence_available=bool(evidence.get("verified_field_records")))
    started = time.monotonic()
    try:
        response = chat_completion(client,**options)
        row.update(completion_metrics(response),status="completed")
        try:
            data = _extract_json(response.choices[0].message.content)
            row["json_valid"] = True
            if stage == "planning":
                row["correct"] = data.get("primary_intent") == "PositionDecision"
                try:
                    _validate(_build_payload(data))
                    row["contract_valid"] = True
                except (ValueError,TypeError,KeyError):
                    row["contract_valid"] = False
            elif stage == "reviewing":
                score = float(data["score"])
                row.update(score=score,unsafe_sample=empty)
            else:
                candidates = data.get("professional_explanation", [])
                kept, diagnostics = explanation_projection(candidates,evidence)
                row.update(candidate_count=len(candidates) if isinstance(candidates,list) else 0,
                           retained_count=len(kept),diagnostics=diagnostics,professional_explanation=kept)
        except (ValueError,TypeError,KeyError):
            row["json_valid"] = False
    except LLMCallError as error:
        row.update(status="unavailable",error="budget_exhausted" if budget.exhausted else error.kind)
    row["latency_ms"] = round((time.monotonic()-started)*1000)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true")
    parser.add_argument("--env-file",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--max-requests",type=int,default=24)
    parser.add_argument("--max-usd",type=float,default=3)
    args = parser.parse_args()
    if not args.live or os.getenv("WP_MODEL_AB_ALLOW_LIVE") != "1":
        raise SystemExit("AB_EXPLICIT_LIVE_OPT_IN_REQUIRED")
    if args.output.exists():
        raise SystemExit("AB_NEW_OUTPUT_REQUIRED")
    from dotenv import dotenv_values
    if args.env_file:
        # Read exactly one variable. Never import broker/config/database settings.
        key = dotenv_values(args.env_file,interpolate=False).get("WEALTHPILOT_OPENAI_API_KEY")
    else:
        key = os.getenv("WEALTHPILOT_OPENAI_API_KEY")
    if not key:
        raise SystemExit("AB_DEVELOPMENT_KEY_MISSING")
    import httpx
    import openai
    budget = Budget(args.max_requests,args.max_usd)
    report = dict(schema="wealthpilot.model-ab.v1",production_acceptance=False,base_sha="5437da0ad962ac7dc49c68b589b39a31ee5bc3e4",
        fixture_manifest_sha256=hashlib.sha256((FIXTURES/"manifest.json").read_bytes()).hexdigest(),
        max_requests=budget.calls,max_usd=budget.dollars,rows=[],limitations=[
        "Frozen AV publication clocks missing: real operating-thesis comparison unavailable.",
        "Component prompt A/B, not full PEER end-to-end latency or production three-stock UAT.",
        "Costs use standard uncached rates; failed/unknown usage retains upper reservation."])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    def save():
        report.update(http_attempts=len(budget.attempts),attempts=budget.attempts,reserved_upper_usd=round(budget.reserved,6))
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
        args.output.chmod(0o600)
    with openai.OpenAI(api_key=key,base_url="https://api.openai.com/v1",max_retries=0,
        http_client=httpx.Client(event_hooks={"request":[budget.before_send]})) as client:
        for symbol, empty in [("LI:US",False),("PDD:US",False),("3690:HK",False),("LI:US",True)]:
            blocked = False
            for stage in ("planning","explanation","reviewing"):
                models = ("gpt-4.1","gpt-6.1-sol") if stage == "explanation" else ("gpt-4.1-mini","gpt-6-luna")
                for model in models:
                    with contextlib.redirect_stdout(io.StringIO()):
                        row = run_case(client,budget,symbol,stage,model,empty)
                    report["rows"].append(row);save()
                    print(json.dumps({k:row[k] for k in ("symbol","stage","requested_model","status")}))
                    if budget.exhausted:
                        report["status"]="INCOMPLETE_BUDGET_EXHAUSTED";save()
                        print(json.dumps({k:report[k] for k in ("status","http_attempts","reserved_upper_usd")}))
                        return
                    if model.startswith("gpt-6") and row.get("error") in {"model_access","authentication","quota","invalid_request"}:
                        blocked = True
                if blocked and stage == "explanation":
                    break
                if blocked and stage == "reviewing":
                    break
            if blocked:
                report["status"]="BLOCKED_TARGET_ACCESS_OR_COMPATIBILITY";save();break
        else:
            report["status"]=("COMPLETED_REQUIRES_QUALITATIVE_REVIEW"
                if all(row["status"] == "completed" for row in report["rows"])
                else "INCOMPLETE_PROVIDER_FAILURES");save()
    print(json.dumps({k:report[k] for k in ("status","http_attempts","reserved_upper_usd")}))


if __name__ == "__main__":
    main()
