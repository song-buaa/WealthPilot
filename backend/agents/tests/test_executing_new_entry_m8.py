"""
M8.2 单元测试 — ExecutingAgent 新建仓分支。

验证:
- is_new_entry=True 时走新建仓分支
- 港股/未识别标的 → abort + 友好消息
- 美股新建仓 → 跳过 signal_engine,保留部分 rule_engine
- 现有持仓路径不受影响(回归)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch
from typing import Optional

import pytest

from backend.agents.contracts import ExecutionOutput, PlanningOutput, AgentTaskStatus
from backend.agents.executing_agent import ExecutingAgent


# ── Mock helpers ─────────────────────────────────────────

def _make_planning_output(asset="苹果", route="position_single", **kw):
    p = PlanningOutput()
    p.task_id = "test-task"
    p.route = route
    p.intent = {"asset": asset, "action_type": "买入判断", "confidence": 0.9}
    p.portfolio_id = 1
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _make_loaded_data(is_new_entry=False, market_not_supported_message=None,
                      has_target=True, av_fundamentals=None, **kw):
    loaded = MagicMock()
    loaded.is_new_entry = is_new_entry
    loaded.market_not_supported_message = market_not_supported_message
    loaded.ambiguous_matches = []
    loaded.has_data_errors = False
    loaded.data_warnings = []
    loaded.positions = [MagicMock()] if not is_new_entry else [MagicMock()]
    loaded.total_assets = 100000
    loaded.research = []
    loaded.av_fundamentals = av_fundamentals

    if has_target:
        tp = MagicMock()
        tp.ticker = "AAPL"
        tp.weight = 0.0 if is_new_entry else 0.1
        tp.is_virtual = is_new_entry
        tp.currency = "USD"
        loaded.target_position = tp
    else:
        loaded.target_position = None

    for k, v in kw.items():
        setattr(loaded, k, v)
    return loaded


def _make_ctx_output(loaded_data):
    ctx = MagicMock()
    ctx.error = None
    ctx.loaded_data = loaded_data
    return ctx


# ============================================================
# Tests
# ============================================================

class TestNewEntryRouting:
    def test_new_entry_routes_to_new_entry_branch(self):
        """is_new_entry=True → 走 _execute_new_entry,不走 pre_check/signal_engine"""
        agent = ExecutingAgent()
        loaded = _make_loaded_data(is_new_entry=True)

        with patch("backend.agents.executing_agent.invoke_skill") as mock_invoke:
            # wp-load-context returns our loaded
            mock_invoke.side_effect = [
                _make_ctx_output(loaded),          # wp-load-context
                MagicMock(error=None),              # wp-check-discipline (partial)
            ]
            with patch("backend.agents.executing_agent.discipline_output_to_rule_result") as mock_d2r:
                mock_d2r.return_value = MagicMock(violation=False, warning=False, current_weight=0)

                out = agent.run(_make_planning_output(asset="AAPL"), "苹果能不能买")

        assert not out.aborted
        assert "m8-new-entry-analysis" in out.invoked_skills
        # signal_engine should be skipped
        assert out.signal_result is None
        skipped = out.skill_results.get("wp-generate-signals", {})
        assert skipped.get("skipped") is True

    def test_existing_position_does_not_trigger_new_entry(self):
        """is_new_entry=False → 走原有持仓分析路径"""
        agent = ExecutingAgent()
        loaded = _make_loaded_data(is_new_entry=False)

        with patch("backend.agents.executing_agent.invoke_skill") as mock_invoke:
            mock_invoke.side_effect = [
                _make_ctx_output(loaded),  # wp-load-context
                MagicMock(error=None),     # wp-check-discipline
                MagicMock(error=None),     # wp-generate-signals
            ]
            with patch("backend.agents.executing_agent.discipline_output_to_rule_result") as mock_d2r, \
                 patch("backend.agents.executing_agent.signals_output_to_signal_result") as mock_s2r, \
                 patch("decision_engine.pre_check.check") as mock_pre:
                mock_d2r.return_value = MagicMock(violation=False, warning=False, current_weight=0.1)
                mock_s2r.return_value = MagicMock(to_dict=lambda: {})
                mock_pre.return_value = MagicMock(passed=True)

                out = agent.run(_make_planning_output(asset="理想汽车"), "理想汽车还能拿吗")

        assert "m8-new-entry-analysis" not in out.invoked_skills


class TestNewEntryHkIntercepted:
    def test_hk_stock_aborts_with_message(self):
        """港股新建仓 → abort + market_not_supported_message"""
        agent = ExecutingAgent()
        loaded = _make_loaded_data(
            is_new_entry=True,
            market_not_supported_message="小米集团(港股)新建仓评估将在 v3.5 接入",
        )

        with patch("backend.agents.executing_agent.invoke_skill") as mock_invoke:
            mock_invoke.return_value = _make_ctx_output(loaded)

            out = agent.run(_make_planning_output(asset="小米集团"), "小米集团能不能买")

        assert out.aborted
        assert out.abort_reason == "new_entry_market_not_supported"
        assert "v3.5" in out.abort_chat_answer


class TestNewEntrySkipsSignalEngine:
    def test_signal_engine_not_invoked(self):
        """新建仓分支不调用 wp-generate-signals"""
        agent = ExecutingAgent()
        loaded = _make_loaded_data(is_new_entry=True)

        with patch("backend.agents.executing_agent.invoke_skill") as mock_invoke:
            mock_invoke.side_effect = [
                _make_ctx_output(loaded),
                MagicMock(error=None),  # wp-check-discipline
            ]
            with patch("backend.agents.executing_agent.discipline_output_to_rule_result") as mock_d2r:
                mock_d2r.return_value = MagicMock(violation=False, warning=False, current_weight=0)
                out = agent.run(_make_planning_output(asset="AAPL"), "苹果能不能买")

        # wp-generate-signals should NOT be in invoked_skills
        assert "wp-generate-signals" not in out.invoked_skills
        assert out.signal_result is None


class TestNewEntryRuleEngine:
    def test_partial_rule_engine_runs(self):
        """新建仓 → wp-check-discipline 被调用(部分校验)"""
        agent = ExecutingAgent()
        loaded = _make_loaded_data(is_new_entry=True)

        with patch("backend.agents.executing_agent.invoke_skill") as mock_invoke:
            mock_invoke.side_effect = [
                _make_ctx_output(loaded),
                MagicMock(error=None),
            ]
            with patch("backend.agents.executing_agent.discipline_output_to_rule_result") as mock_d2r:
                mock_d2r.return_value = MagicMock(violation=False, warning=False, current_weight=0)
                out = agent.run(_make_planning_output(asset="AAPL"), "苹果能不能买")

        assert "wp-check-discipline-partial" in out.invoked_skills
        assert out.skill_results.get("wp-check-discipline", {}).get("is_new_entry") is True

    def test_rule_engine_failure_does_not_block(self):
        """新建仓 rule_engine 失败 → 不阻塞(warning only)"""
        agent = ExecutingAgent()
        loaded = _make_loaded_data(is_new_entry=True)

        with patch("backend.agents.executing_agent.invoke_skill") as mock_invoke:
            mock_invoke.side_effect = [
                _make_ctx_output(loaded),
                Exception("rule engine down"),  # wp-check-discipline fails
            ]
            out = agent.run(_make_planning_output(asset="AAPL"), "苹果能不能买")

        # Should still complete, not fail
        assert not out.aborted or out.status != AgentTaskStatus.FAILED


class TestNewEntryAvFundamentals:
    def test_av_data_propagated_to_output(self):
        """av_fundamentals 正确传递到 ExecutionOutput"""
        agent = ExecutingAgent()
        mock_av = MagicMock()
        mock_av.pe_ttm = 28.5
        loaded = _make_loaded_data(is_new_entry=True, av_fundamentals=mock_av)

        with patch("backend.agents.executing_agent.invoke_skill") as mock_invoke:
            mock_invoke.side_effect = [
                _make_ctx_output(loaded),
                MagicMock(error=None),
            ]
            with patch("backend.agents.executing_agent.discipline_output_to_rule_result") as mock_d2r:
                mock_d2r.return_value = MagicMock(violation=False, warning=False, current_weight=0)
                out = agent.run(_make_planning_output(asset="AAPL"), "苹果能不能买")

        assert out.loaded_data.av_fundamentals is mock_av
        assert out.loaded_data.is_new_entry is True
