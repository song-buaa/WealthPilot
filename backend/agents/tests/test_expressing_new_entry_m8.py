"""
M8.4 单元测试 — ExpressingAgent 新建仓 prompt 模板。

验证:
- is_new_entry=True 时走新建仓 prompt
- payload 不包含持仓字段
- av_data None 字段鲁棒处理
- decisionType 强制 buy_init
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from typing import Optional

import pytest

from backend.agents.expressing_agent import (
    _build_new_entry_payload,
    _CHAT_FORMAT_NEW_ENTRY,
    _fmt,
    _fmt_pct,
)


# ── Helpers ───────────────────────────────────────────────

def _make_av_data(**overrides):
    av = MagicMock()
    av.pe_ttm = overrides.get("pe_ttm", 28.5)
    av.eps_ttm = overrides.get("eps_ttm", 6.42)
    av.high_52w = overrides.get("high_52w", 200.0)
    av.low_52w = overrides.get("low_52w", 140.0)
    av.roe = overrides.get("roe", 34.5)
    av.gross_margin = overrides.get("gross_margin", 46.2)
    av.revenue_yoy = overrides.get("revenue_yoy", 12.3)
    av.net_income_yoy = overrides.get("net_income_yoy", 8.7)
    av.pb = overrides.get("pb", None)
    av.peg_ratio = overrides.get("peg_ratio", 1.8)

    analyst = MagicMock()
    analyst.target_price_avg = overrides.get("target_price_avg", 195.0)
    analyst.consensus = overrides.get("consensus", "Buy")
    analyst.strong_buy = 15
    analyst.buy = 12
    analyst.hold = 5
    analyst.sell = 2
    analyst.strong_sell = 0
    av.analyst = analyst

    return av


# ============================================================
# _build_new_entry_payload
# ============================================================

class TestBuildNewEntryPayload:
    def test_basic_fields(self):
        av = _make_av_data()
        payload = _build_new_entry_payload(
            asset_name="苹果", ticker="AAPL", av_data=av,
            total_assets=500000, rule_result=None,
        )
        assert payload["asset_name"] == "苹果"
        assert payload["ticker"] == "AAPL"
        assert "28.50" in payload["pe_ttm"]
        assert "$6.42" in payload["eps_ttm"]
        assert "$200.00" in payload["high_52w"]
        assert "$140.00" in payload["low_52w"]
        assert "500,000" in payload["total_assets"]

    def test_excludes_position_fields(self):
        """payload 不包含 weight / market_value / profit_loss 等持仓字段"""
        av = _make_av_data()
        payload = _build_new_entry_payload(
            asset_name="苹果", ticker="AAPL", av_data=av,
            total_assets=500000, rule_result=None,
        )
        forbidden_keys = {"current_weight", "weight", "market_value",
                         "profit_loss", "profit_loss_rate", "cost_price"}
        for key in forbidden_keys:
            assert key not in payload, f"payload 不应包含持仓字段: {key}"

    def test_handles_missing_av_data(self):
        """av_data 为 None 时,所有字段用 '数据暂缺' 占位"""
        payload = _build_new_entry_payload(
            asset_name="苹果", ticker="AAPL", av_data=None,
            total_assets=500000, rule_result=None,
        )
        assert payload["pe_ttm"] == "数据暂缺"
        assert payload["eps_ttm"] == "数据暂缺"
        assert payload["high_52w"] == "数据暂缺"
        assert payload["analyst_target"] == "数据暂缺"
        assert payload["analyst_ratings"] == "暂无数据"

    def test_handles_partial_av_data(self):
        """av_data 部分字段为 None"""
        av = _make_av_data(pe_ttm=None, eps_ttm=None, roe=None)
        payload = _build_new_entry_payload(
            asset_name="苹果", ticker="AAPL", av_data=av,
            total_assets=500000, rule_result=None,
        )
        assert payload["pe_ttm"] == "数据暂缺"
        assert payload["eps_ttm"] == "数据暂缺"
        assert payload["roe"] == "数据暂缺"
        # Other fields should still work
        assert "$200.00" in payload["high_52w"]

    def test_analyst_ratings_formatted(self):
        av = _make_av_data()
        payload = _build_new_entry_payload(
            asset_name="苹果", ticker="AAPL", av_data=av,
            total_assets=500000, rule_result=None,
        )
        assert "强买:15" in payload["analyst_ratings"]
        assert "Buy" in payload["analyst_ratings"]

    def test_rule_violation_message(self):
        rule = MagicMock()
        rule.violation = True
        rule.warning = False
        payload = _build_new_entry_payload(
            asset_name="苹果", ticker="AAPL", av_data=None,
            total_assets=500000, rule_result=rule,
        )
        assert "违规" in payload["rule_summary"]

    def test_rule_warning_message(self):
        rule = MagicMock()
        rule.violation = False
        rule.warning = True
        payload = _build_new_entry_payload(
            asset_name="苹果", ticker="AAPL", av_data=None,
            total_assets=500000, rule_result=rule,
        )
        assert "警告" in payload["rule_summary"]


# ============================================================
# Prompt template
# ============================================================

class TestNewEntryPromptTemplate:
    def test_template_contains_required_sections(self):
        assert "建仓建议" in _CHAT_FORMAT_NEW_ENTRY
        assert "基本面解读" in _CHAT_FORMAT_NEW_ENTRY
        assert "建仓价区间" in _CHAT_FORMAT_NEW_ENTRY
        assert "仓位建议" in _CHAT_FORMAT_NEW_ENTRY
        assert "风险提示" in _CHAT_FORMAT_NEW_ENTRY

    def test_template_no_position_references(self):
        """模板中不应有持仓相关占位符"""
        forbidden = ["{current_weight}", "{market_value}", "{profit_loss}",
                    "{profit_loss_rate}", "{cost_price}"]
        for f in forbidden:
            assert f not in _CHAT_FORMAT_NEW_ENTRY, f"模板不应包含: {f}"

    def test_template_renders_with_payload(self):
        """模板可以用 payload 正确渲染"""
        av = _make_av_data()
        payload = _build_new_entry_payload(
            asset_name="苹果", ticker="AAPL", av_data=av,
            total_assets=500000, rule_result=None,
        )
        rendered = _CHAT_FORMAT_NEW_ENTRY.format(**payload)
        assert "苹果" in rendered
        assert "AAPL" in rendered
        assert "28.50" in rendered


# ============================================================
# Format helpers
# ============================================================

class TestFormatHelpers:
    def test_fmt_none(self):
        assert _fmt(None) == "数据暂缺"

    def test_fmt_float(self):
        assert _fmt(28.5) == "28.50"

    def test_fmt_prefix(self):
        assert _fmt(100.0, prefix="$") == "$100.00"

    def test_fmt_pct_none(self):
        assert _fmt_pct(None) == "数据暂缺"

    def test_fmt_pct_value(self):
        assert _fmt_pct(12.3) == "12.3%"


# ============================================================
# ExpressingAgent routing
# ============================================================

class TestExpressingAgentRouting:
    def test_routes_to_new_entry_when_is_new_entry_true(self):
        """is_new_entry=True → 走 _express_new_entry"""
        from backend.agents.expressing_agent import ExpressingAgent
        from backend.agents.contracts import PlanningOutput, ExecutionOutput

        agent = ExpressingAgent()

        plan = PlanningOutput()
        plan.task_id = "test"
        plan.route = "position_single"
        plan.intent = {"primary_intent": "PositionDecision", "asset": "苹果", "confidence": 0.9}

        exec_out = ExecutionOutput(task_id="test")
        loaded = MagicMock()
        loaded.is_new_entry = True
        loaded.market_not_supported_message = None
        loaded.target_position = MagicMock(name="苹果", ticker="AAPL")
        loaded.av_fundamentals = _make_av_data()
        loaded.total_assets = 500000
        loaded.full_discipline_rules = None  # M8.5: avoid MagicMock format errors
        exec_out.loaded_data = loaded
        exec_out.rule_result = None

        # Mock LLM call
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "## 建仓建议\n建议观察"

        with patch("intent_engine._llm_client.get_client") as mock_gc, \
             patch("intent_engine._llm_client.MODEL_MAIN", "test-model"):
            mock_gc.return_value.chat.completions.create.return_value = mock_response

            chunks = []
            loop = asyncio.new_event_loop()
            async def _run():
                async for chunk in agent.run_streaming(plan, exec_out, "苹果能不能买"):
                    chunks.append(chunk)
            loop.run_until_complete(_run())
            loop.close()

        assert len(chunks) > 0
        assert agent.last_output.structured_payload.get("decisionType") == "buy_init"
        assert agent.last_output.structured_payload.get("is_new_entry") is True
        assert agent.last_output.prompt_template_id == "new_entry_evaluation"

    def test_routes_to_existing_when_is_new_entry_false(self):
        """is_new_entry=False → 走原有 llm_engine.reason()"""
        from backend.agents.expressing_agent import ExpressingAgent
        from backend.agents.contracts import PlanningOutput, ExecutionOutput

        agent = ExpressingAgent()

        plan = PlanningOutput()
        plan.task_id = "test"
        plan.route = "position_single"
        plan.intent = {"primary_intent": "PositionDecision", "asset": "理想汽车", "confidence": 0.9}

        exec_out = ExecutionOutput(task_id="test")
        loaded = MagicMock()
        loaded.is_new_entry = False
        exec_out.loaded_data = loaded
        exec_out.rule_result = MagicMock()
        exec_out.signal_result = MagicMock()

        mock_llm_result = MagicMock()
        mock_llm_result.chat_answer = "理想汽车分析..."
        mock_llm_result.raw_output = "理想汽车分析..."
        mock_llm_result.is_fallback = False
        mock_llm_result.structured_result = {"decisionType": "hold"}

        with patch("decision_engine.llm_engine.reason", return_value=mock_llm_result):
            chunks = []
            loop = asyncio.new_event_loop()
            async def _run():
                async for chunk in agent.run_streaming(plan, exec_out, "理想汽车还能拿吗"):
                    chunks.append(chunk)
            loop.run_until_complete(_run())
            loop.close()

        # Should NOT be new_entry template
        assert agent.last_output.prompt_template_id != "new_entry_evaluation"
