"""Original PEER qualitative value survives, without reopening numeric output."""
from copy import deepcopy
from types import SimpleNamespace
import unicodedata

import pytest

from backend.agents.contracts import ExpressionOutput
from backend.services.market_data.evidence import assess
from backend.services.market_data.output_policy import (
    enforce, enforce_portfolio, explanation_prompt, project_explanation,
)
from tests.test_v42_evidence import AT, verified


TEXT = unicodedata.normalize("NFKC", "若产品结构改善能够持续转化为经营现金流，盈利质量可能增强；仍需关注竞争促销对利润的侵蚀，不能仅凭短期表现确认投资逻辑。")


def assessment():
    return assess(symbol="LI:US", at=AT, research=[verified("research",
        text="产品结构改善，但竞争促销可能影响利润与现金转化。")])


def paragraph(**changes):
    return dict(topic="investment_thesis", basis="inference", text=TEXT,
                evidence_refs=["fixture:research"], **changes)


def test_cited_business_reasoning_retained_as_inference_without_l3_authority():
    from decision_engine.llm_engine import LLMResult
    a = assessment()
    assert not a["qualification"]["L3"]["allowed"]
    llm = LLMResult("SELL", ["unsafe 987654"], ["unsafe 987654"], ["unsafe 987654"],
                    chat_answer="sell 987654", raw_output="987654")
    out = ExpressionOutput(llm_result=llm, structured_payload={"decisionType":"trim",
        "confidence":.99, "professional_explanation":[paragraph()]})
    enforce(out, a)
    p = out.structured_payload["professional_explanation"][0]
    assert p["text"] == TEXT and p["basis"] == "inference"
    assert p["sources"][0]["data_as_of"] == a["records"][0]["data_as_of"]
    assert TEXT in out.chat_answer and "AI专业分析推断" in out.chat_answer
    assert TEXT in llm.reasoning[0]  # The original Explain adapter keeps value too.
    assert "987654" not in str(out) and not out.actionable
    assert out.structured_payload["exitPlan"]["price_trigger"] is None
    enforce(out, a)
    assert len(out.structured_payload["professional_explanation"]) == 1
    assert out.chat_answer.count(TEXT) == 1


@pytest.mark.parametrize("quality", ["seed", "mock", "stale", "unknown", "conflict", "superseded"])
def test_final_recheck_removes_explanation_when_source_becomes_unusable(quality):
    a = assessment()
    safe = project_explanation([paragraph()], a)
    assert safe
    a["records"][0]["quality"] = quality
    assert project_explanation(safe, a) == []


@pytest.mark.parametrize("change", [
    {"evidence_refs":["model-invented"]}, {"evidence_refs":[]},
    {"evidence_refs":["fixture:research", "excluded"]},
    {"basis":"verified_fact"}, {"topic":"order"},
])
def test_models_cannot_self_certify_facts_or_partial_citations(change):
    p = paragraph(); p.update(change)
    assert project_explanation([p], assessment()) == []


@pytest.mark.parametrize("suffix", [
    "建议卖出100股。", "建议卖出１００股。", "建议卖出١٠٠股。",
    "建议减持一百股。", "建议减持两成。", "可按百分之二十退出。", "划分三笔退出。",
    "可卖出壹佰股。", "按前高卖出。", "建议以现价减持。", "建议卖出全部持仓。",
    "应当清仓。", "建议卖出一半。", "sell ten shares.", "buy at market price.",
    "价位区间为１２—１５。", "参考[链接](https://evil.example)。",
    "卖\u200b出一百股。", "建议在压力位减持。",
    "按昨日均价打九折出售该标的以确保交易顺利完成。",
    "以披露当日成交价出售持仓以确保及时退出。", "以最近交易日的价格减持更为合适。",
    "Reduce fifteen units to control risk.", "Exit half the position to control risk.",
    "建议减持廿股以降低集中度。", "建议减持Ⅹ股以降低集中度。", "建议减持貳股以降低集中度。",
    "建议减持拾股以降低集中度。", "建议减持½股以降低集中度。", "建议减持⑩股以降低集中度。",
    "The entire position should be liquidated because the thesis is impaired.",
    "Liquidation of all holdings is warranted because the thesis is impaired.",
    "Reduce the full position because the thesis is impaired.", "应将持仓全部出售以降低集中度。",
])
def test_unqualified_numeric_and_relative_trade_instructions_never_survive(suffix):
    p = paragraph(); p["text"] += suffix
    assert project_explanation([p], assessment()) == []


def test_everyday_chinese_qualitative_words_are_retained_without_numeric_authority():
    p = paragraph(); p["text"] = "一般而言，单一经营指标不能确认竞争壁垒；需要进一步观察盈利是否转化为现金，避免依赖短期市场叙事。"
    result = project_explanation([p], assessment())
    assert len(result) == 1
    assert result[0]["text"] == unicodedata.normalize("NFKC", p["text"])


def test_wrong_symbol_and_ambiguous_record_ids_do_not_support_explanation():
    a = assessment()
    a["records"][0]["symbol"] = "PDD:US"
    assert project_explanation([paragraph()], a) == []
    a = assessment()
    conflict = deepcopy(a["records"][0]); conflict["values"]["text"] = "conflicting content"
    a["records"].append(conflict)
    assert project_explanation([paragraph()], a) == []


def test_same_news_research_event_alias_preserves_citation_from_actual_prepare():
    from backend.services.position_analysis import prepare
    from tests.test_v42_exit_plan import setup_case
    view, executed, planner, _ = setup_case("LI:US")
    executed.loaded_data.research_evidence = [verified("event",
        text="产品结构改善，但竞争促销可能影响利润与现金转化。",
        metadata={"evidence_kind":"event"})]
    a = prepare(planner, executed, asset_view=view, at=AT)
    source = [record for record in a["records"] if record["record_id"] == "fixture:event"]
    assert {record["kind"] for record in source} == {"research", "event"}
    p = paragraph(); p["evidence_refs"] = ["fixture:event"]
    assert len(project_explanation([p], a)) == 1
    source[-1]["values"]["text"] += " conflicting content"
    assert project_explanation([p], a) == []


def test_prompt_exposes_owner_candidates_for_original_reasoning_comparison():
    from backend.services.market_data.output_policy import prompt_context
    a = assessment()
    a["exit_candidates"] = [{"candidate_id":"account-first:fixture", "tranches":[{
        "account_ref":"fixture-account", "amount_cny":1234, "funds_destination":"offshore_fixed_income"}]}]
    prompt = prompt_context(a)
    assert '"exit_candidates"' in prompt
    assert "account-first:fixture" in prompt and "offshore_fixed_income" in prompt


def test_prompt_contains_only_owner_verified_evidence_with_traceable_refs():
    a = assessment()
    stale = deepcopy(a["records"][0]); stale.update(quality="stale", record_id="stale-secret")
    stale["values"]["text"] = "never-in-model-prompt"
    a["records"].append(stale)
    prompt = explanation_prompt(a)
    assert "产品结构改善" in prompt and "fixture:research" in prompt
    assert "never-in-model-prompt" not in prompt and "stale-secret" not in prompt


def test_no_evidence_downgrades_without_reusing_arbitrary_chat():
    out = ExpressionOutput(chat_answer=TEXT, structured_payload={"professional_explanation":[paragraph()]})
    enforce(out, assess(symbol="LI:US", at=AT))
    assert TEXT not in out.chat_answer
    assert out.structured_payload["professional_explanation"] == []
    assert "缺少可引用的合格证据" in out.chat_answer


def test_partial_fundamental_only_independently_verified_field_can_support_inference():
    a = assessment()
    raw = deepcopy(a["records"][0])
    raw.update(kind="fundamentals", quality="partial", record_id="vendor:report")
    a["records"] = [raw]
    field = deepcopy(raw)
    field.update(kind="fundamental_field", quality="valid", record_id="vendor:report:net_income_ttm",
                 values={"net_income_ttm":100})
    a["verified_field_records"] = [field]
    p = paragraph(); p["evidence_refs"] = [raw["record_id"]]
    assert project_explanation([p], a) == []
    p["evidence_refs"] = [field["record_id"]]
    assert len(project_explanation([p], a)) == 1
    assert raw["quality"] == "partial"


def test_portfolio_uses_only_server_assessment_and_refilters_explain_fields():
    out = ExpressionOutput(structured_payload={"professional_explanation":[paragraph()]})
    # Fake evidence in request options is ignored.
    enforce_portfolio(out, options={"records":assessment()["records"]})
    assert out.structured_payload["professional_explanation"] == []
    out.structured_payload = {"professional_explanation":[paragraph()]}
    enforce_portfolio(out, evidence_assessments=[assessment()])
    assert TEXT in out.chat_answer
    assert not out.structured_payload["can_propose_rebalance"]
    a = assessment(); a["records"][0]["quality"] = "stale"
    enforce_portfolio(out, evidence_assessments=[a])
    assert TEXT not in out.chat_answer


def test_portfolio_final_policy_cleans_all_legacy_explain_surfaces():
    from decision_engine.llm_engine import LLMResult
    legacy = LLMResult("SELL", ["987654"], ["987654"], ["987654"],
        original_decision="987654", raw_output="987654", chat_answer="987654",
        structured_result={"quantity":987654})
    out = ExpressionOutput(llm_result=legacy, structured_payload={
        "professional_explanation":[paragraph()], "quantity":987654})
    enforce_portfolio(out, evidence_assessments=[assessment()])
    assert "987654" not in str(out)
    assert TEXT in legacy.reasoning[0]
    assert legacy.strategy == [] and legacy.original_decision is None
    assert legacy.structured_result == out.structured_payload
    assert legacy.raw_payload == out.structured_payload
    assert legacy.decision == "HOLD"


def test_original_peer_model_explanation_contract_and_filtered_stream(monkeypatch):
    import asyncio
    from backend.agents.expressing_agent import ExpressingAgent
    from backend.agents.contracts import PlanningOutput, ExecutionOutput
    from decision_engine import llm_engine
    from tests.test_v42_exit_plan import setup_case
    _, executed, plan, _ = setup_case("LI:US")
    a = assessment()
    executed.position_evidence = a
    captured = {}
    def reason(question, *args, **kwargs):
        captured["question"] = question
        return llm_engine.LLMResult("SELL", [], [], [], chat_answer="unsafe sell 987654",
            structured_result={"decisionType":"trim", "professional_explanation":[paragraph()]})
    monkeypatch.setattr(llm_engine, "reason", reason)
    agent = ExpressingAgent()
    out = ExpressionOutput()
    async def collect():
        return [chunk async for chunk in agent._express_position_decision(out, plan, executed, "评估经营质量", [])]
    chunks = asyncio.run(collect())
    assert "professional_explanation" in captured["question"]
    assert TEXT in "".join(chunks)
    assert "987654" not in "".join(chunks)


def test_original_portfolio_model_receives_qualified_context_without_second_model(monkeypatch):
    import asyncio
    from backend.agents.expressing_agent import ExpressingAgent
    from backend.agents.contracts import PlanningOutput, ExecutionOutput
    from decision_engine import llm_engine
    captured = []
    def review(question, loaded, **kwargs):
        captured.append(question)
        assert "excluded-legacy" not in str(loaded.research)
        assert "产品结构改善" in str(loaded.research)
        return llm_engine.GenericLLMResult("PortfolioReview", "unsafe 987654", raw_payload={
            "professional_explanation":[paragraph()]})
    monkeypatch.setattr(llm_engine, "review_portfolio", review)
    loaded = SimpleNamespace(research=["excluded-legacy"], retrieved_research_views=["excluded-legacy"])
    executed = ExecutionOutput(loaded_data=loaded)
    executed.portfolio_evidence = [assessment()]
    out = ExpressionOutput()
    async def collect():
        return [chunk async for chunk in ExpressingAgent()._express_portfolio(
            out, PlanningOutput(), executed, "评估组合", "PortfolioReview", [])]
    asyncio.run(collect())
    enforce_portfolio(out, evidence_assessments=executed.portfolio_evidence)
    assert len(captured) == 1 and "professional_explanation" in captured[0]
    assert TEXT in out.chat_answer and "987654" not in out.chat_answer
    assert loaded.research == ["excluded-legacy"]  # no mutation of original owner data


@pytest.mark.parametrize("fenced", [False, True])
def test_original_new_entry_small_json_retains_only_cited_explanation(monkeypatch, fenced):
    import asyncio
    import json
    from backend.agents.expressing_agent import ExpressingAgent
    from intent_engine import _llm_client
    from tests.test_v42_exit_plan import setup_case
    _, executed, plan, _ = setup_case("LI:US")
    executed.position_evidence = assessment()
    executed.loaded_data.is_new_entry = True
    executed.loaded_data.total_assets = 1000
    executed.loaded_data.av_fundamentals = None
    captured = []
    def create(**kwargs):
        captured.append(kwargs)
        response = json.dumps({"chat_answer":"买入987654股，限价987654",
                               "professional_explanation":[paragraph()]}, ensure_ascii=False)
        if fenced:
            response = "```json\n"+response+"\n```"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=response))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(_llm_client, "get_client", lambda: client)
    out = ExpressionOutput()
    async def collect():
        return [chunk async for chunk in ExpressingAgent()._express_position_decision(
            out, plan, executed, "研究是否适合建仓", [])]
    chunks = asyncio.run(collect())
    assert len(captured) == 1
    assert "professional_explanation" in captured[0]["messages"][0]["content"]
    assert TEXT in "".join(chunks) and TEXT in out.chat_answer
    assert "987654" not in str(out)
    assert out.structured_payload["decisionType"] == "buy_init"
    assert not out.actionable
