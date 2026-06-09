"""
执行计划 validator 测试 — 债4: 结构化比对(hard) + 文案白名单(soft)。

运行: python -m pytest backend/services/execution_plan/tests/test_validator.py -v
"""
import copy
import pytest
from backend.graph.decision_validator import validate_execution_plan


PLAN_DICT = {
    "symbol": "LI:US",
    "side": "BUY",
    "total_quantity": 80,
    "num_tranches": 3,
    "tranches": [
        {"sequence": 1, "quantity": 26, "trigger_type": "PRICE_BELOW",
         "trigger_price": 2.5, "limit_price": 2.51},
        {"sequence": 2, "quantity": 27, "trigger_type": "PRICE_BELOW",
         "trigger_price": 2.3, "limit_price": 2.31},
        {"sequence": 3, "quantity": 27, "trigger_type": "PRICE_BELOW",
         "trigger_price": 2.1, "limit_price": 2.11},
    ],
    "target_position_pct": 0.08,
    "current_position_pct": 0.0,
    "current_price": 2.8,
}

FACTOR = {
    "current_price": 2.8,
    "volatility_annual": 0.4544,
    "price_percentile": 0.0078,
    "drawdown_from_high": -0.2907,
    "rsi14": 29.7,
    "atr14": 0.12,
}


# ═══════════════════════════════════════════════════════════════════
# Layer 1: plan_summary_block 结构化比对 (hard)
# ═══════════════════════════════════════════════════════════════════

class TestStructuredComparison:

    def test_identical_plan_passes(self):
        """frozen == actual → 通过。"""
        frozen = copy.deepcopy(PLAN_DICT)
        r = validate_execution_plan(PLAN_DICT, "解释", "风险", FACTOR, plan_dict_frozen=frozen)
        assert r.passed is True
        assert all(f.severity != "hard" for f in r.failures)

    def test_tampered_trigger_price_hard(self):
        """篡改 trigger_price (低价 2.5→3.5) → hard 拦截。"""
        frozen = copy.deepcopy(PLAN_DICT)
        tampered = copy.deepcopy(PLAN_DICT)
        tampered["tranches"][0]["trigger_price"] = 3.5
        r = validate_execution_plan(tampered, "", "", FACTOR, plan_dict_frozen=frozen)
        assert r.passed is False
        assert any(f.rule == "plan_value_mismatch" and f.severity == "hard" for f in r.failures)

    def test_tampered_quantity_small_number_hard(self):
        """篡改 quantity (小股数 26→50) → hard 拦截。"""
        frozen = copy.deepcopy(PLAN_DICT)
        tampered = copy.deepcopy(PLAN_DICT)
        tampered["tranches"][0]["quantity"] = 50
        r = validate_execution_plan(tampered, "", "", FACTOR, plan_dict_frozen=frozen)
        assert r.passed is False
        assert any("quantity" in f.message for f in r.failures)

    def test_tampered_total_quantity_hard(self):
        """篡改 total_quantity → hard 拦截。"""
        frozen = copy.deepcopy(PLAN_DICT)
        tampered = copy.deepcopy(PLAN_DICT)
        tampered["total_quantity"] = 999
        r = validate_execution_plan(tampered, "", "", FACTOR, plan_dict_frozen=frozen)
        assert r.passed is False

    def test_tampered_num_tranches_hard(self):
        """篡改批次数 → hard 拦截。"""
        frozen = copy.deepcopy(PLAN_DICT)
        tampered = copy.deepcopy(PLAN_DICT)
        tampered["tranches"].append({"sequence": 4, "quantity": 10, "trigger_type": "PRICE_BELOW",
                                     "trigger_price": 2.0, "limit_price": 2.01})
        tampered["num_tranches"] = 4
        r = validate_execution_plan(tampered, "", "", FACTOR, plan_dict_frozen=frozen)
        assert r.passed is False

    def test_no_frozen_skips_comparison(self):
        """不提供 frozen → 跳过结构化比对，只做其他检查。"""
        r = validate_execution_plan(PLAN_DICT, "解释", "风险", FACTOR, plan_dict_frozen=None)
        assert r.passed is True


# ═══════════════════════════════════════════════════════════════════
# Layer 2: 文案数字白名单 (soft)
# ═══════════════════════════════════════════════════════════════════

class TestTextNumberSoft:

    def test_clean_text_no_warning(self):
        """正常文案(无计划外数字) → 无 soft 警告。"""
        r = validate_execution_plan(
            PLAN_DICT, "分批买入以降低风险", "波动较大需谨慎", FACTOR,
        )
        assert len([f for f in r.failures if f.severity == "soft"]) == 0

    def test_fabricated_price_soft_warning(self):
        """文案编了 15.50(不在 plan/factor 里) → soft 警告但不阻断。"""
        r = validate_execution_plan(
            PLAN_DICT,
            "建议在 15.50 美元附近开始建仓",
            "",
            FACTOR,
        )
        assert r.passed is True  # soft 不阻断
        soft = [f for f in r.failures if f.severity == "soft"]
        assert len(soft) >= 1
        assert any("15.50" in f.message for f in soft)

    def test_fabricated_quantity_soft_warning(self):
        """文案编了 2500 股 → soft 警告。"""
        r = validate_execution_plan(
            PLAN_DICT,
            "每批买入 2500 股",
            "",
            FACTOR,
        )
        assert r.passed is True
        assert any("2500" in f.message for f in r.failures)

    def test_percentage_not_flagged(self):
        """"5%波动""45%"→ 不告警(百分号后缀排除)。"""
        r = validate_execution_plan(
            PLAN_DICT,
            "当前年化波动率约45%，不要在5%波动内频繁调整",
            "RSI约30，处于超卖区间",
            FACTOR,
        )
        soft = [f for f in r.failures if f.severity == "soft"]
        assert len(soft) == 0

    def test_factor_value_not_flagged(self):
        """引用 factor_snapshot 里的值 → 不告警。"""
        r = validate_execution_plan(
            PLAN_DICT,
            f"ATR 为 {FACTOR['atr14']}，价格分位仅 {FACTOR['price_percentile']}",
            f"回撤已达 {abs(FACTOR['drawdown_from_high']):.1%}",
            FACTOR,
        )
        soft = [f for f in r.failures if f.severity == "soft"]
        assert len(soft) == 0

    def test_plan_value_in_text_not_flagged(self):
        """文案里引用 plan dict 中的数字(如触发价 2.5) → 不告警。"""
        r = validate_execution_plan(
            PLAN_DICT,
            "第一批在 2.5 美元触发",
            "",
            FACTOR,
        )
        soft = [f for f in r.failures if f.severity == "soft"]
        assert len(soft) == 0

    def test_small_natural_numbers_not_flagged(self):
        """小数字(0/1/2)不告警。"""
        r = validate_execution_plan(
            PLAN_DICT,
            "第1批为首次建仓，后续2批逐步加仓",
            "每1天间隔执行",
            FACTOR,
        )
        soft = [f for f in r.failures if f.severity == "soft"]
        assert len(soft) == 0


# ═══════════════════════════════════════════════════════════════════
# 组合: hard + soft 同时存在
# ═══════════════════════════════════════════════════════════════════

class TestCombined:

    def test_hard_blocks_even_with_soft(self):
        """同时有 hard(结构化不一致) + soft(文案编数) → 拦截(hard 优先)。"""
        frozen = copy.deepcopy(PLAN_DICT)
        tampered = copy.deepcopy(PLAN_DICT)
        tampered["total_quantity"] = 999

        r = validate_execution_plan(
            tampered,
            "建议在 15.50 建仓",
            "",
            FACTOR,
            plan_dict_frozen=frozen,
        )
        assert r.passed is False
        hard = [f for f in r.failures if f.severity == "hard"]
        soft = [f for f in r.failures if f.severity == "soft"]
        assert len(hard) >= 1
        assert len(soft) >= 1
