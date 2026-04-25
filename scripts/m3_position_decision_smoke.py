"""
M3 PositionDecision 3 个示例端到端 smoke 测试。

M3 临时 smoke，M5 替换为完整 18 用例验证。

验证 _load_research v2 改造后，PositionDecision 链路不报错、返回合理数据。
不走完整 LLM 决策链路（太慢），只验证 _load_research 层。

前端 PositionDecision 预设问题（frontend/src/pages/Decision.tsx L138-142）:
  1. "我有一只股票最近涨了不少，该不该趁现在落袋为安？"
  2. "我有一只基金持续亏损，现在止损出来还是继续持有？"
  3. "我看好一个标的想加仓，但它在我组合里已经不轻了，怎么判断能不能加？"

这些问题不含具体标的名，决策模块会先走意图识别 → 标的澄清。
为直接验证 _load_research，本脚本用带标的名的等价问题。
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AV_DEV_MOCK", "1")

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("m3_smoke")


SMOKE_CASES = [
    {
        "input": "理想汽车 仓位偏重，是不是该考虑减仓或再平衡了？",
        "asset_name": "理想汽车",
    },
    {
        "input": "英伟达 现在还能继续持有吗？",
        "asset_name": "英伟达",
    },
    {
        "input": "对特斯拉现在适合做什么操作？",
        "asset_name": "特斯拉",
    },
]


def test_load_research(asset_name: str) -> dict:
    """直接调用 _load_research 验证。"""
    from app.database import get_session
    from decision_engine.data_loader import _load_research

    session = get_session()
    try:
        result = _load_research(session, pid=1, asset_name=asset_name)
        return {
            "success": True,
            "count": len(result),
            "first_3": result[:3],
            "error": None,
        }
    except Exception as e:
        return {
            "success": False,
            "count": 0,
            "first_3": [],
            "error": f"{type(e).__name__}: {e}",
        }
    finally:
        session.close()


def detect_path(lines: list[str]) -> str:
    """根据输出判断命中路径。"""
    if not lines:
        return "mock"
    if any("[用户资料]" in l for l in lines):
        return "v2"
    if any("[联网参考]" in l for l in lines):
        if any("暂无" in l for l in lines):
            return "mock"
        return "online_fallback"
    return "mock"


def main():
    print("=" * 70)
    print("M3 PositionDecision 端到端 Smoke Test")
    print("=" * 70)

    all_pass = True
    for i, case in enumerate(SMOKE_CASES, 1):
        print(f"\n--- Case {i}: {case['input'][:40]}... ---")
        print(f"  asset_name: {case['asset_name']}")

        result = test_load_research(case["asset_name"])

        path = detect_path(result["first_3"])
        print(f"  成功: {result['success']}")
        print(f"  命中路径: {path}")
        print(f"  返回行数: {result['count']}")

        if result["first_3"]:
            for j, line in enumerate(result["first_3"]):
                print(f"  [{j}] {line[:100]}{'...' if len(line) > 100 else ''}")
        else:
            print(f"  (无数据)")

        if result["error"]:
            print(f"  ❌ 错误: {result['error']}")
            all_pass = False
        elif result["count"] == 0:
            print(f"  ⚠️  返回 0 条（走 mock）")
        else:
            print(f"  ✅")

    print("\n" + "=" * 70)
    if all_pass:
        print("✅ 3 个 PositionDecision 示例全部通过（无异常）")
    else:
        print("❌ 有示例失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
