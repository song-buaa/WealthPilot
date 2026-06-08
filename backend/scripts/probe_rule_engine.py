#!/usr/bin/env python3
"""
规则引擎验证脚本 — 4 组用例。
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

from services.execution_plan.rule_engine import generate_plan, PlanInput
from services.execution_plan.factors import build_factor_snapshot


def _pp(result, label):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  violations: {result.violations}")
    print(f"  warnings:   {result.warnings}")
    print(f"  tick_degraded: {result.tick_degraded}")
    print(f"\n  plan_summary_block:")
    print(json.dumps(result.plan_summary_block, indent=4, default=str))
    print(f"\n  constraints_applied (subset):")
    ca = result.constraints_applied
    for k in ["max_position_pct", "max_single_add_pct", "min_batches_required",
              "n_one_exempt", "requires_review"]:
        print(f"    {k}: {ca.get(k)}")
    print()


def main():
    # ── (a) LI:US BUY 无锚点 ──
    print("\n" + "#"*60)
    print("# (a) LI:US — BUY 8%, 无锚点, percentile≈0.008, vol≈45%")
    print("#"*60)
    snap_li = build_factor_snapshot("LI:US", "US")
    inp_a = PlanInput(
        symbol="LI:US", market="US", side="BUY",
        target_position_pct=0.08, current_position_pct=0.0,
        current_price=snap_li.current_price or 14.2,
        total_assets=1_000_000,
        atr14=snap_li.atr14,
        volatility_annual=snap_li.volatility_annual,
        price_percentile=snap_li.price_percentile,
        drawdown_from_high=snap_li.drawdown_from_high,
    )
    result_a = generate_plan(inp_a, snap_li.to_dict())
    _pp(result_a, "(a) LI:US BUY 8% 无锚点")

    # ── (b) 00700:HK ADD 锚点价 ──
    print("\n" + "#"*60)
    print("# (b) 00700:HK — ADD, 锚点价 [440, 420, 400], vol≈38%")
    print("#"*60)
    snap_hk = build_factor_snapshot("00700:HK", "HK")
    inp_b = PlanInput(
        symbol="00700:HK", market="HK", side="ADD",
        target_position_pct=0.15, current_position_pct=0.05,
        current_price=snap_hk.current_price or 446.2,
        total_assets=1_000_000,
        user_anchor_prices=[440, 420, 400],
        atr14=snap_hk.atr14,
        volatility_annual=snap_hk.volatility_annual,
        price_percentile=snap_hk.price_percentile,
        drawdown_from_high=snap_hk.drawdown_from_high,
    )
    result_b = generate_plan(inp_b, snap_hk.to_dict())
    _pp(result_b, "(b) 00700:HK ADD 锚点价 [440,420,400]")

    # ── (c) 撞硬约束 ──
    print("\n" + "#"*60)
    print("# (c) LI:US — BUY 目标仓位 50% (超 max 40%)")
    print("#"*60)
    inp_c = PlanInput(
        symbol="LI:US", market="US", side="BUY",
        target_position_pct=0.50, current_position_pct=0.0,
        current_price=14.2, total_assets=1_000_000,
        atr14=0.6545, volatility_annual=0.4544,
        price_percentile=0.008,
    )
    result_c = generate_plan(inp_c, {})
    _pp(result_c, "(c) 硬约束: 目标 50% → 被修正为 40%")

    # ── (d) 退化单笔 ──
    print("\n" + "#"*60)
    print("# (d1) BUY 5% + quick_mode → N=1 豁免")
    print("#"*60)
    inp_d1 = PlanInput(
        symbol="LI:US", market="US", side="BUY",
        target_position_pct=0.05, current_position_pct=0.0,
        current_price=14.2, total_assets=1_000_000,
        quick_mode=True,
        atr14=0.6545, volatility_annual=0.4544,
    )
    result_d1 = generate_plan(inp_d1, {})
    _pp(result_d1, "(d1) BUY 快速单笔 → N=1")

    print("\n" + "#"*60)
    print("# (d2) ADD 5% + quick_mode → 不豁免, 回落分批")
    print("#"*60)
    inp_d2 = PlanInput(
        symbol="LI:US", market="US", side="ADD",
        target_position_pct=0.10, current_position_pct=0.05,
        current_price=14.2, total_assets=1_000_000,
        quick_mode=True,
        atr14=0.6545, volatility_annual=0.4544,
    )
    result_d2 = generate_plan(inp_d2, {})
    _pp(result_d2, "(d2) ADD 快速模式 → 不豁免, N≥2")


if __name__ == "__main__":
    main()
