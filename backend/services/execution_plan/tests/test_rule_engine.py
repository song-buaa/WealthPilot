"""
规则引擎单元测试 — 纯确定性验证。

运行: python -m pytest backend/services/execution_plan/tests/test_rule_engine.py -v
"""
import pytest
from backend.services.execution_plan.rule_engine import generate_plan, PlanInput
from backend.services.execution_plan.hk_tick import hk_tick_size, round_to_tick


class TestHKTick:
    def test_common_prices(self):
        assert hk_tick_size(5.0)[0] == 0.010    # $0.50-$10 区间
        assert hk_tick_size(50.0)[0] == 0.050   # $20-$100
        assert hk_tick_size(150.0)[0] == 0.100  # $100-$200
        assert hk_tick_size(350.0)[0] == 0.200  # $200-$500
        assert hk_tick_size(450.0)[0] == 0.200  # 腾讯价位

    def test_round_hk(self):
        p, deg = round_to_tick(445.13, "HK")
        assert p == 445.2  # 向 0.2 档取整
        assert deg is False

    def test_round_us(self):
        p, deg = round_to_tick(14.567, "US")
        assert p == 14.57
        assert deg is False

    def test_fallback(self):
        _, deg = hk_tick_size(99999.0)
        assert deg is True


class TestBatchCount:
    def _make(self, **kw):
        defaults = dict(
            symbol="TEST:US", market="US", side="BUY",
            target_position_pct=0.08, current_position_pct=0.0,
            current_price=100.0, total_assets=1_000_000,
            atr14=5.0, volatility_annual=0.35,
        )
        defaults.update(kw)
        return PlanInput(**defaults)

    def test_no_anchor_normal_vol(self):
        r = generate_plan(self._make(), {})
        assert r.plan_summary_block["num_tranches"] == 2  # min_batches_required

    def test_high_vol_adds_one(self):
        r = generate_plan(self._make(volatility_annual=0.50), {})
        assert r.plan_summary_block["num_tranches"] == 3  # 2 + 1

    def test_anchor_prices_set_n(self):
        r = generate_plan(self._make(user_anchor_prices=[95, 90, 85, 80]), {})
        assert r.plan_summary_block["num_tranches"] == 4

    def test_max_batches_cap(self):
        # target=40%, single=10%, n_min=4, vol=50%→+1=5, capped at 5
        r = generate_plan(self._make(
            target_position_pct=0.40, volatility_annual=0.50,
        ), {})
        assert r.plan_summary_block["num_tranches"] <= 5

    def test_quick_buy_n1(self):
        r = generate_plan(self._make(
            side="BUY", target_position_pct=0.05, quick_mode=True,
        ), {})
        assert r.plan_summary_block["num_tranches"] == 1
        assert r.constraints_applied["n_one_exempt"] is True

    def test_quick_add_no_exempt(self):
        r = generate_plan(self._make(
            side="ADD", target_position_pct=0.10,
            current_position_pct=0.05, quick_mode=True,
        ), {})
        assert r.plan_summary_block["num_tranches"] >= 2
        assert r.constraints_applied["n_one_exempt"] is False


class TestHardConstraints:
    def _make(self, **kw):
        defaults = dict(
            symbol="TEST:US", market="US", side="BUY",
            target_position_pct=0.08, current_position_pct=0.0,
            current_price=100.0, total_assets=1_000_000,
            atr14=5.0, volatility_annual=0.35,
        )
        defaults.update(kw)
        return PlanInput(**defaults)

    def test_target_exceeds_max(self):
        r = generate_plan(self._make(target_position_pct=0.50), {})
        assert any("修正" in v for v in r.violations)
        assert r.plan_summary_block["target_position_pct"] == 0.40

    def test_drawdown_requires_review(self):
        r = generate_plan(self._make(drawdown_from_high=-0.35), {})
        assert r.constraints_applied["requires_review"] is True
        assert any("复盘线" in w for w in r.warnings)

    def test_zero_increment(self):
        r = generate_plan(self._make(
            target_position_pct=0.05, current_position_pct=0.05,
        ), {})
        assert r.plan_summary_block == {}


class TestTriggerPrices:
    def _make(self, **kw):
        defaults = dict(
            symbol="TEST:US", market="US", side="BUY",
            target_position_pct=0.08, current_position_pct=0.0,
            current_price=100.0, total_assets=1_000_000,
            atr14=5.0, volatility_annual=0.35,
        )
        defaults.update(kw)
        return PlanInput(**defaults)

    def test_buy_prices_below_current(self):
        r = generate_plan(self._make(), {})
        for t in r.plan_summary_block["tranches"]:
            assert t["trigger_price"] < 100.0

    def test_sell_prices_above_current(self):
        r = generate_plan(self._make(
            side="REDUCE",
            target_position_pct=0.02, current_position_pct=0.08,
            current_price=100.0,
        ), {})
        for t in r.plan_summary_block["tranches"]:
            if t["trigger_price"] is not None:
                assert t["trigger_price"] > 100.0

    def test_max_deviation_respected(self):
        r = generate_plan(self._make(), {})
        P = 100.0
        for t in r.plan_summary_block["tranches"]:
            deviation = abs(t["trigger_price"] - P) / P
            assert deviation <= 0.26  # 25% + small rounding margin

    def test_high_percentile_delays_first(self):
        r = generate_plan(self._make(price_percentile=0.9), {})
        assert any("分位" in w for w in r.warnings)
