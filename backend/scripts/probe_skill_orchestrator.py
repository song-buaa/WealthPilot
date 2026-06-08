#!/usr/bin/env python3
"""
wp-generate-execution-plan Skill 端到端验证。

验证: invoke_skill 真实调用 → factors → rule_engine → LLM → 返回
"""
import sys, os, json, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

# 开启日志看 orchestrator 步骤
logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")

from backend.skills import invoke_skill


def main():
    print("=" * 60)
    print("  invoke_skill('wp-generate-execution-plan') 端到端验证")
    print("=" * 60)

    result = invoke_skill(
        "wp-generate-execution-plan",
        symbol="LI:US",
        market="US",
        side="BUY",
        target_position_pct=0.08,
        current_position_pct=0.0,
        current_price=14.2,
        total_assets=1_000_000,
        source_decision_ref="manual-probe",
    )

    print("\n" + "=" * 60)
    print("  返回结果")
    print("=" * 60)

    # plan_summary_block
    psb = result["plan_summary_block"]
    print("\n  plan_summary_block:")
    print(json.dumps(psb, indent=4, default=str))

    # rationale / risk_notes
    print(f"\n  rationale:\n    {result['rationale']}")
    print(f"\n  risk_notes:\n    {result['risk_notes']}")

    # constraints
    print(f"\n  constraints_applied (subset):")
    ca = result["constraints_applied"]
    for k in ["max_position_pct", "max_single_add_pct", "n_one_exempt", "requires_review"]:
        print(f"    {k}: {ca.get(k)}")

    # warnings / violations
    print(f"\n  warnings: {result['warnings']}")
    print(f"  violations: {result['violations']}")

    # 人工比对: plan_summary_block 数字 vs rule_engine 输出
    print("\n" + "=" * 60)
    print("  人工比对: plan_summary_block 数字来源")
    print("=" * 60)
    print(f"  total_quantity: {psb['total_quantity']}")
    print(f"  num_tranches: {psb['num_tranches']}")
    for t in psb["tranches"]:
        print(f"    批{t['sequence']}: trigger={t['trigger_price']}, qty={t['quantity']}")
    print("  (以上数字全部来自 rule_engine,LLM 不参与产出)")

    # 检查 rationale 里有没有偷塞数字
    print(f"\n  LLM rationale 长度: {len(result['rationale'])} 字")
    print(f"  LLM risk_notes 长度: {len(result['risk_notes'])} 字")


if __name__ == "__main__":
    main()
