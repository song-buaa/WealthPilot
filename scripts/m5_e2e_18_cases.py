"""
M5-2: 18 个预设决策用例端到端测试。

串行触发决策 SSE 接口，收集每个用例的意图识别、数据路径、输出质量。
前端预设问题来源: frontend/src/pages/Decision.tsx

用法: AV_DEV_MOCK=1 python scripts/m5_e2e_18_cases.py
"""

import json
import logging
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AV_DEV_MOCK", "1")

logging.basicConfig(level=logging.WARNING)

import requests

BASE = "http://127.0.0.1:8000/api"

# 18 个预设问题（Decision.tsx L133-183 + L72-76）
CASES = [
    # PositionDecision × 3 (L138-142)
    {"q": "我有一只股票最近涨了不少，该不该趁现在落袋为安？", "expected_intent": "PositionDecision"},
    {"q": "我有一只基金持续亏损，现在止损出来还是继续持有？", "expected_intent": "PositionDecision"},
    {"q": "我看好一个标的想加仓，但它在我组合里已经不轻了，怎么判断能不能加？", "expected_intent": "PositionDecision"},
    # PortfolioReview × 3 (L148-152)
    {"q": "我的持仓里有几只股票集中在同一个行业，这样风险大吗？", "expected_intent": "PortfolioReview"},
    {"q": "我的组合调整过几次了，现在整体是什么状态？", "expected_intent": "PortfolioReview"},
    {"q": "我感觉我的组合在震荡市里跌得比较多，问题出在哪？", "expected_intent": "PortfolioReview"},
    # AssetAllocation × 3 (L158-162)
    {"q": "我有100万准备开始投资，应该怎么分配？", "expected_intent": "AssetAllocation"},
    {"q": "我准备把一笔即将到期的30万理财重新配置，不知道怎么分？", "expected_intent": "AssetAllocation"},
    {"q": "我想把组合调整到更稳健的结构，固收应该加多少？", "expected_intent": "AssetAllocation"},
    # PerformanceAnalysis × 3 (L168-172)
    {"q": "这段时间大盘还行，但我的组合收益明显跑输了，为什么？", "expected_intent": "PerformanceAnalysis"},
    {"q": "我有几笔投资一直是正收益，但整体算下来并不好看，哪里出了问题？", "expected_intent": "PerformanceAnalysis"},
    {"q": "从我现在的持仓来看，哪些标的在拖累整体表现？", "expected_intent": "PerformanceAnalysis"},
    # Education × 3 (L178-182)
    {"q": "我总是在股票涨了之后才后悔没多买，跌了又舍不得止损，怎么破？", "expected_intent": "Education"},
    {"q": "我听说要定期做再平衡，但不知道什么情况下该做、怎么做？", "expected_intent": "Education"},
    {"q": "分散投资和集中持仓我一直没想清楚，对我来说哪种更适合？", "expected_intent": "Education"},
    # 通用推荐 × 3 (L72-76)
    {"q": "如果我准备开始配置权益资产，第一步应该怎么做？", "expected_intent": "Education"},
    {"q": "稳健型投资者应该怎么理解股债的仓位比例？", "expected_intent": "Education"},
    {"q": "同样是买基金，主动型和指数型怎么选？", "expected_intent": "Education"},
]


def run_case(idx: int, case: dict, conversation_id: str) -> dict:
    """调 SSE 接口并收集结果。"""
    result = {
        "idx": idx + 1,
        "question": case["q"],
        "expected_intent": case["expected_intent"],
        "actual_intent": None,
        "asset": None,
        "output_len": 0,
        "output_preview": "",
        "error": None,
        "has_exception": False,
    }

    try:
        resp = requests.post(
            f"{BASE}/decision/chat",
            json={"message": case["q"], "conversation_id": conversation_id},
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=180,
        )

        full_text = ""
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                continue

            event_type = data.get("type") or data.get("event")

            if "primary_intent" in data:
                result["actual_intent"] = data.get("primary_intent")
                result["asset"] = data.get("asset")
            elif "intent" in data and isinstance(data["intent"], dict):
                result["actual_intent"] = data["intent"].get("primary_intent")
                result["asset"] = data["intent"].get("asset")

            if "delta" in data:
                full_text += data["delta"]

            if data.get("type") == "done" or "decision_id" in data:
                break

        result["output_len"] = len(full_text)
        result["output_preview"] = full_text[:200].replace("\n", " ")

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        result["has_exception"] = True

    return result


def main():
    print("=" * 70)
    print("M5-2: 18 个预设决策用例端到端测试")
    print("=" * 70)

    results = []
    for i, case in enumerate(CASES):
        conversation_id = str(uuid.uuid4())
        print(f"\n[{i+1}/18] {case['q'][:40]}...")

        result = run_case(i, case, conversation_id)
        results.append(result)

        intent_ok = "✅" if result["actual_intent"] else "⚠️"
        error_mark = "❌" if result["has_exception"] else ""
        print(f"  intent={result['actual_intent']} output={result['output_len']}字 {intent_ok}{error_mark}")

        if result["error"]:
            print(f"  ERROR: {result['error']}")

        time.sleep(2)

    # 汇总
    print(f"\n{'='*70}")
    print("汇总")
    print(f"{'='*70}")

    passed = 0
    failed = 0
    for r in results:
        ok = not r["has_exception"] and r["output_len"] > 0
        mark = "✅" if ok else "❌"
        intent_match = "✓" if r["actual_intent"] == r["expected_intent"] else f"({r['actual_intent']})"
        print(f"  [{r['idx']:2d}] {mark} intent={r['expected_intent']:20s} {intent_match:22s} output={r['output_len']:4d}字 | {r['question'][:30]}...")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n通过: {passed}/18, 失败: {failed}/18")

    # 写报告
    report_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "m5_e2e_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# M5-2: 18 个预设决策用例端到端测试报告\n\n")
        f.write(f"通过: {passed}/18, 失败: {failed}/18\n\n")
        f.write("| # | 问题 | 预期意图 | 实际意图 | 标的 | 输出长度 | 状态 |\n")
        f.write("|---|------|---------|---------|------|---------|------|\n")
        for r in results:
            ok = "✅" if not r["has_exception"] and r["output_len"] > 0 else "❌"
            f.write(f"| {r['idx']} | {r['question'][:25]}... | {r['expected_intent']} | {r['actual_intent'] or 'N/A'} | {r['asset'] or '-'} | {r['output_len']} | {ok} |\n")
        f.write("\n## 详细输出\n\n")
        for r in results:
            f.write(f"### Case {r['idx']}: {r['question'][:40]}...\n\n")
            f.write(f"- 预期意图: {r['expected_intent']}\n")
            f.write(f"- 实际意图: {r['actual_intent']}\n")
            f.write(f"- 标的: {r['asset']}\n")
            f.write(f"- 输出长度: {r['output_len']} 字\n")
            if r["error"]:
                f.write(f"- 错误: {r['error']}\n")
            f.write(f"- 输出预览: {r['output_preview'][:150]}...\n\n")

    print(f"\n报告已写入: {report_path}")

    if failed > 0:
        print("❌ M5-2 有用例失败")
        sys.exit(1)
    else:
        print("✅ M5-2 PASS: 18/18 全部通过")


if __name__ == "__main__":
    main()
