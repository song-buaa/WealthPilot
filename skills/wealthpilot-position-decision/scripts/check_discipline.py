"""
WealthPilot Position Decision Skill - 纪律校验脚本

演示 Skill 的 scripts/ 目录如何承载真实可执行代码。
本脚本通过 WealthPilot 的 Tool Layer 调用 rule_engine
（决策管道简化版），完成 SOP Step 3 的纪律校验。

使用方式：
  python -m skills.wealthpilot_position_decision.scripts.check_discipline \
      --asset 茅台 --portfolio_id 1 --action HOLD
"""
import argparse
import os
import sys

# 确保能 import 项目内的 Tool Layer
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", ".."
)))


def check_discipline(asset_name: str, portfolio_id: int, action: str) -> dict:
    """调用 WealthPilot Tool Layer 的纪律校验 Tool"""
    from backend.graph.tools import call_tool

    result = call_tool(
        "check_discipline_rules",
        asset_name=asset_name,
        portfolio_id=portfolio_id,
        action_type=action,
    )
    return {
        "violation": result.violation,
        "warning": result.warning,
        "current_weight": result.current_weight,
        "max_position": result.max_position,
        "rule_details": result.rule_details,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", required=True, help="标的名称")
    parser.add_argument("--portfolio_id", type=int, default=1)
    parser.add_argument("--action", default="HOLD",
                       choices=["BUY", "HOLD", "SELL", "REDUCE",
                               "TAKE_PROFIT", "STOP_LOSS"])
    args = parser.parse_args()

    result = check_discipline(args.asset, args.portfolio_id, args.action)
    print(f"标的: {args.asset}")
    print(f"操作: {args.action}")
    print(f"违规: {'是' if result['violation'] else '否'}")
    print(f"当前仓位: {result['current_weight']:.2%}")
    print(f"上限: {result['max_position']:.2%}")
    if result['warning']:
        print(f"警告: {result['warning']}")
    print(f"\n纪律明细:")
    for d in result['rule_details']:
        print(f"  - {d}")


if __name__ == "__main__":
    main()
