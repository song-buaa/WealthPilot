"""
M8.5 单元测试 — 新建仓纪律规则视图。

验证 build_new_entry_discipline_summary 输出内容:
- 适用规则正确包含
- 不适用规则正确排除
- 空 dict 鲁棒处理
"""
from __future__ import annotations

import pytest

from app.discipline.new_entry_view import build_new_entry_discipline_summary
from app.discipline.config import _DEFAULT_RULES


class TestBuildSummary:
    def test_includes_single_asset_max_pct(self):
        result = build_new_entry_discipline_summary(_DEFAULT_RULES)
        assert "40%" in result
        assert "单标的上限" in result

    def test_includes_preferred_range(self):
        result = build_new_entry_discipline_summary(_DEFAULT_RULES)
        assert "20%-30%" in result
        assert "建议仓位区间" in result

    def test_includes_first_position_pct(self):
        """语义改写: '加仓上限' → '首次建仓比例'"""
        result = build_new_entry_discipline_summary(_DEFAULT_RULES)
        assert "10%" in result
        assert "首次建仓" in result

    def test_includes_batch_requirement(self):
        result = build_new_entry_discipline_summary(_DEFAULT_RULES)
        assert "分批建仓" in result
        assert "2" in result

    def test_includes_min_cash_pct(self):
        result = build_new_entry_discipline_summary(_DEFAULT_RULES)
        assert "20%" in result
        assert "流动性" in result or "现金" in result

    def test_includes_circuit_breaker(self):
        result = build_new_entry_discipline_summary(_DEFAULT_RULES)
        assert "25%" in result
        assert "熔断" in result

    def test_includes_asset_allocation(self):
        result = build_new_entry_discipline_summary(_DEFAULT_RULES)
        assert "80%" in result or "资产配置" in result

    def test_excludes_leverage_limits(self):
        result = build_new_entry_discipline_summary(_DEFAULT_RULES)
        assert "杠杆ETF持仓上限" not in result
        assert "Level0" not in result
        assert "融资融券" not in result

    def test_excludes_stop_loss_rules(self):
        result = build_new_entry_discipline_summary(_DEFAULT_RULES)
        assert "硬止损" not in result
        assert "软止损" not in result
        assert "逻辑破坏" not in result

    def test_excludes_rebalancing_from_applicable(self):
        """再平衡/偏离度只出现在跳过区,不出现在适用区"""
        result = build_new_entry_discipline_summary(_DEFAULT_RULES)
        applicable_section = result.split("【不适用规则")[0]
        assert "偏离度" not in applicable_section
        assert "强制再平衡" not in applicable_section

    def test_skip_section_present(self):
        """显式标注跳过的规则"""
        result = build_new_entry_discipline_summary(_DEFAULT_RULES)
        assert "不适用规则" in result
        assert "止损规则" in result
        assert "冷静期" in result

    def test_handles_empty_rules(self):
        result = build_new_entry_discipline_summary({})
        assert "纪律配置暂缺" in result

    def test_handles_none_rules(self):
        result = build_new_entry_discipline_summary(None)
        assert "纪律配置暂缺" in result

    def test_output_is_multiline(self):
        result = build_new_entry_discipline_summary(_DEFAULT_RULES)
        lines = result.strip().split("\n")
        assert len(lines) >= 8  # 标题 + 至少 5 条适用 + 标题 + 至少 4 条跳过


class TestPayloadIntegration:
    def test_payload_rule_summary_is_full(self):
        """验证 _build_new_entry_payload 接入后 rule_summary 长度 > 100"""
        from backend.agents.expressing_agent import _build_new_entry_payload
        payload = _build_new_entry_payload(
            asset_name="苹果",
            ticker="AAPL",
            av_data=None,
            total_assets=500000,
            rule_result=None,
            full_discipline_rules=_DEFAULT_RULES,
        )
        assert len(payload["rule_summary"]) > 100
        assert "适用纪律" in payload["rule_summary"]

    def test_payload_rule_summary_with_violation(self):
        """violation 时追加硬约束触发提示"""
        from unittest.mock import MagicMock
        from backend.agents.expressing_agent import _build_new_entry_payload
        rule = MagicMock()
        rule.violation = True
        rule.warning = False
        payload = _build_new_entry_payload(
            asset_name="苹果",
            ticker="AAPL",
            av_data=None,
            total_assets=500000,
            rule_result=rule,
            full_discipline_rules=_DEFAULT_RULES,
        )
        assert "硬约束触发" in payload["rule_summary"]

    def test_payload_without_full_rules_fallback(self):
        """full_discipline_rules=None 时使用 fallback"""
        from backend.agents.expressing_agent import _build_new_entry_payload
        payload = _build_new_entry_payload(
            asset_name="苹果",
            ticker="AAPL",
            av_data=None,
            total_assets=500000,
            rule_result=None,
            full_discipline_rules=None,
        )
        assert "纪律配置暂缺" in payload["rule_summary"]
