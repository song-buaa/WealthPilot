"""
执行计划 validator 测试 — plan_value_mismatch 正向+负向。

运行: python -m pytest backend/services/execution_plan/tests/test_validator.py -v
"""
import pytest
from backend.graph.decision_validator import validate_execution_plan


# ── 共用测试数据 ──────────────────────────────────────────────

PLAN_DICT = {
    "symbol": "LI:US",
    "side": "BUY",
    "total_quantity": 5633,
    "num_tranches": 3,
    "tranches": [
        {"sequence": 1, "quantity": 1877, "trigger_type": "PRICE_BELOW",
         "trigger_price": 13.91, "limit_price": 13.94},
        {"sequence": 2, "quantity": 1877, "trigger_type": "PRICE_BELOW",
         "trigger_price": 13.22, "limit_price": 13.25},
        {"sequence": 3, "quantity": 1879, "trigger_type": "PRICE_BELOW",
         "trigger_price": 12.24, "limit_price": 12.26},
    ],
    "target_position_pct": 0.08,
    "current_position_pct": 0.0,
    "current_price": 14.2,
}

FACTOR_SNAPSHOT = {
    "current_price": 14.2,
    "volatility_annual": 0.4544,
    "price_percentile": 0.0078,
    "drawdown_from_high": -0.2907,
    "trend_signal": "bearish",
    "rsi14": 29.7,
    "atr14": 0.6545,
    "ma5": 14.75,
    "ma20": 16.52,
    "macd": -0.9208,
    "macd_signal": -0.7255,
    "macd_hist": -0.1954,
}


# ═══════════════════════════════════════════════════════════════════
# (a) 正常计划 → validator 通过
# ═══════════════════════════════════════════════════════════════════

class TestValidatorHappyPath:

    def test_normal_plan_passes(self):
        """正常的 rationale/risk_notes 不含计划外数字 → 通过。"""
        result = validate_execution_plan(
            plan_dict=PLAN_DICT,
            llm_rationale=(
                "本次分批买入计划基于当前市场处于弱势趋势，"
                "价格接近历史低分位水平，波动率较高。"
                "分批执行有助于分散入场时点的风险。"
            ),
            llm_risk_notes=(
                "当前市场波动较大且处于弱势趋势，"
                "价格虽接近低点但仍存在进一步下跌风险。"
            ),
            factor_snapshot=FACTOR_SNAPSHOT,
        )
        assert result.passed is True
        assert result.action == "pass"
        assert len(result.failures) == 0


# ═══════════════════════════════════════════════════════════════════
# (b) 篡改 plan_summary_block 的 trigger_price → 被拦
# ═══════════════════════════════════════════════════════════════════

class TestValidatorTamperedPlan:

    def test_tampered_trigger_price_in_rationale(self):
        """LLM 在 rationale 里写了一个 plan dict 之外的价格 → 被拦。"""
        result = validate_execution_plan(
            plan_dict=PLAN_DICT,
            llm_rationale=(
                "建议在 15.50 美元附近开始建仓，"  # 15.50 不在 plan dict 里
                "分批逐步买入。"
            ),
            llm_risk_notes="注意风险。",
            factor_snapshot=FACTOR_SNAPSHOT,
        )
        assert result.passed is False
        assert result.action == "retry"
        assert any("15.50" in f.message for f in result.failures)
        assert any(f.rule == "plan_value_mismatch" for f in result.failures)


# ═══════════════════════════════════════════════════════════════════
# (c) LLM 文案里塞 plan dict 之外的数字 → 被拦
# ═══════════════════════════════════════════════════════════════════

class TestValidatorFabricatedNumber:

    def test_fabricated_quantity_in_rationale(self):
        """LLM 在 rationale 里编了一个不在计划中的数量 → 被拦。"""
        result = validate_execution_plan(
            plan_dict=PLAN_DICT,
            llm_rationale="建议每批买入 2500 股，逐步建仓。",  # 2500 不在 plan dict
            llm_risk_notes="控制仓位。",
            factor_snapshot=FACTOR_SNAPSHOT,
        )
        assert result.passed is False
        assert any("2500" in f.message for f in result.failures)

    def test_fabricated_price_in_risk_notes(self):
        """LLM 在 risk_notes 里编了一个不在计划中的价格 → 被拦。"""
        result = validate_execution_plan(
            plan_dict=PLAN_DICT,
            llm_rationale="分批买入计划已生成。",
            llm_risk_notes="若价格跌破 10.50 美元应止损。",  # 10.50 不在 plan dict
            factor_snapshot=FACTOR_SNAPSHOT,
        )
        assert result.passed is False
        assert any("10.50" in f.message or "10.5" in f.message for f in result.failures)


# ═══════════════════════════════════════════════════════════════════
# (d) 合法自然语言数字 → 不被误伤
# ═══════════════════════════════════════════════════════════════════

class TestValidatorNoFalsePositive:

    def test_percentage_not_flagged(self):
        """百分比数字如"45%波动""5%"不被误伤。"""
        result = validate_execution_plan(
            plan_dict=PLAN_DICT,
            llm_rationale=(
                "当前年化波动率约45%，处于较高水平。"
                "价格分位接近0%，在52周范围底部。"
                "不要在5%波动内频繁调整。"
            ),
            llm_risk_notes="RSI约30，处于超卖区间。",
            factor_snapshot=FACTOR_SNAPSHOT,
        )
        assert result.passed is True

    def test_factor_snapshot_values_allowed(self):
        """引用 factor_snapshot 里的具体数值不被误伤。"""
        result = validate_execution_plan(
            plan_dict=PLAN_DICT,
            llm_rationale=(
                f"当前价格处于52周低分位(仅0.78%)，"
                f"年化波动率达到45.44%，RSI为29.7。"
                f"均线位置显示价格低于MA5({FACTOR_SNAPSHOT['ma5']})和"
                f"MA20({FACTOR_SNAPSHOT['ma20']})。"
            ),
            llm_risk_notes="回撤达29.07%，接近纪律复盘线。",
            factor_snapshot=FACTOR_SNAPSHOT,
        )
        assert result.passed is True

    def test_small_natural_numbers_allowed(self):
        """小数字(0/1/2)自然语言常见,不被误伤。"""
        result = validate_execution_plan(
            plan_dict=PLAN_DICT,
            llm_rationale="第1批为首次建仓，后续2批逐步加仓。",
            llm_risk_notes="每1天至少间隔执行。",
            factor_snapshot=FACTOR_SNAPSHOT,
        )
        assert result.passed is True
