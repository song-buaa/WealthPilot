"""
WealthPilot M3 Eval Harness

三层评测体系：
  L1：意图识别准确率（exact match）
  L2：流程链路正确性（stage / candidates / validator）
  L3：决策质量（结构化字段校验 + LLM-as-judge，后期启用）

用法：
  AV_DEV_MOCK=1 python scripts/m3_eval_harness.py
  AV_DEV_MOCK=1 python scripts/m3_eval_harness.py --case PD_001
  AV_DEV_MOCK=1 python scripts/m3_eval_harness.py --category PositionDecision
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import requests
import yaml

os.environ.setdefault("AV_DEV_MOCK", "1")

BASE = "http://127.0.0.1:8000/api"
CASES_DIR = Path(__file__).parent.parent / "m0" / "cases"

# L1 意图映射（SSE intent event 的 primary_intent → yaml expected 的 intent_category）
INTENT_NORMALIZE = {
    "PositionDecision": "PositionDecision",
    "PortfolioReview": "PortfolioReview",
    "AssetAllocation": "AssetAllocation",
    "PerformanceAnalysis": "PerformanceAnalysis",
    "Education": "Education",
    "GeneralChat": "Education",   # GeneralChat 归入 Education 类
}

# L2：哪些 stage 算"走完了决策链路"
FULL_DECISION_STAGES = {
    "done", "reasoning", "llm", "signal", "rule_check", "loaded"
}


# ══════════════════════════════════════════════════════════════════
# SSE 调用 + 事件收集
# ══════════════════════════════════════════════════════════════════

def call_and_collect(query: str, portfolio_id: int = 1) -> dict:
    """调用 /api/decision/chat，收集所有 SSE 事件。"""
    collected = {
        "intent": {},
        "stages": [],
        "full_text": "",
        "candidates": [],
        "done": {},
        "validator": {},
        "error": {},
        "validator_warning": {},
        "has_exception": False,
        "duration_seconds": 0,
    }
    t0 = time.time()
    try:
        resp = requests.post(
            f"{BASE}/decision/chat",
            json={"message": query, "session_id": str(uuid.uuid4()),
                  "portfolio_id": portfolio_id},
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=90,
        )
        current_event = None
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("event:"):
                current_event = line.replace("event:", "").strip()
                continue
            if not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except Exception:
                continue

            if current_event == "intent":
                collected["intent"] = data
            elif current_event == "stage":
                collected["stages"].append(data.get("stage", ""))
            elif current_event == "text":
                collected["full_text"] += data.get("delta", "")
            elif current_event == "candidates":
                collected["candidates"] = data.get("items", [])
            elif current_event == "done":
                collected["done"] = data
                collected["validator"] = data.get("validator", {})
            elif current_event == "error":
                collected["error"] = data
                collected["has_exception"] = True
            elif current_event == "validator_warning":
                collected["validator_warning"] = data
    except Exception as e:
        collected["has_exception"] = True
        collected["error"] = {"message": str(e)}
    collected["duration_seconds"] = round(time.time() - t0, 2)
    return collected


# ══════════════════════════════════════════════════════════════════
# L1 评测：意图识别
# ══════════════════════════════════════════════════════════════════

def eval_l1(expected_l1: dict, collected: dict) -> dict:
    result = {"pass": True, "details": {}}
    if not expected_l1:
        return result

    # 意图类别
    actual_intent_raw = collected["intent"].get("primary_intent", "")
    actual_intent = INTENT_NORMALIZE.get(actual_intent_raw, actual_intent_raw)
    expected_intent = expected_l1.get("intent_category", "")
    intent_match = (actual_intent == expected_intent)
    result["details"]["intent_match"] = {
        "expected": expected_intent,
        "actual": actual_intent,
        "pass": intent_match,
    }
    if not intent_match and not expected_l1.get("skip_intent_check"):
        result["pass"] = False

    # needs_clarification
    if "needs_clarification" in expected_l1:
        actual_clarify = bool(collected["intent"].get("needs_clarification", False))
        # 如果有 candidates 也算 clarification
        if collected["candidates"]:
            actual_clarify = True
        expected_clarify = expected_l1["needs_clarification"]
        clarify_match = (actual_clarify == expected_clarify)
        result["details"]["clarification_match"] = {
            "expected": expected_clarify,
            "actual": actual_clarify,
            "pass": clarify_match,
        }
        if not clarify_match:
            result["pass"] = False

    return result


# ══════════════════════════════════════════════════════════════════
# L2 评测：流程链路
# ══════════════════════════════════════════════════════════════════

def eval_l2(expected_l2: dict, collected: dict) -> dict:
    result = {"pass": True, "details": {}}
    if not expected_l2:
        return result

    # must_reach_stage
    if "must_reach_stage" in expected_l2:
        required_stage = expected_l2["must_reach_stage"]
        actual_stages = set(collected["stages"])
        # done event 里有 conclusion_level 也说明走完了
        if collected["done"]:
            actual_stages.add("done")
        if required_stage == "done":
            stage_ok = bool(collected["done"]) or \
                       bool(actual_stages & FULL_DECISION_STAGES)
        else:
            stage_ok = required_stage in actual_stages
        result["details"]["stage_reached"] = {
            "required": required_stage,
            "actual_stages": list(actual_stages),
            "pass": stage_ok,
        }
        if not stage_ok:
            result["pass"] = False

    # must_not_abort
    if expected_l2.get("must_not_abort", True):
        not_aborted = not collected["has_exception"] and \
                      not collected["error"]
        result["details"]["not_aborted"] = {
            "pass": not_aborted,
            "error": collected["error"] if not not_aborted else None,
        }
        if not not_aborted:
            result["pass"] = False

    # expect_candidates
    if "expect_candidates" in expected_l2:
        expected_cands = expected_l2["expect_candidates"]
        actual_has_cands = len(collected["candidates"]) > 0
        cands_ok = (actual_has_cands == expected_cands)
        result["details"]["candidates"] = {
            "expected": expected_cands,
            "actual_count": len(collected["candidates"]),
            "pass": cands_ok,
        }
        if not cands_ok:
            result["pass"] = False

    # validator_passed（有 done event 且有 validator 字段时才检查）
    if collected["validator"]:
        validator_passed = collected["validator"].get("passed", True)
        validator_action = collected["validator"].get("action", "pass")
        result["details"]["validator"] = {
            "passed": validator_passed,
            "action": validator_action,
            "failures": collected["validator"].get("failures", []),
        }
        if not validator_passed:
            # validator 失败是 soft warning，不直接让 L2 fail
            result["details"]["validator"]["note"] = \
                "validator 未通过，但不计入 L2 pass/fail（记录用）"

    return result


# ══════════════════════════════════════════════════════════════════
# L3 评测：决策质量（结构化字段校验）
# ══════════════════════════════════════════════════════════════════

def eval_l3(expected_l3: dict, collected: dict) -> dict:
    result = {"pass": True, "details": {}, "rubric_score": None}
    if not expected_l3:
        return result

    done_data = collected["done"]
    decision_result = done_data.get("decisionResult") or {}
    chat_answer = collected["full_text"] or \
                  decision_result.get("chat_answer", "")

    # chat_answer 最低长度
    if "chat_answer_min_chars" in expected_l3:
        min_chars = expected_l3["chat_answer_min_chars"]
        actual_len = len(chat_answer.strip())
        len_ok = actual_len >= min_chars
        result["details"]["chat_answer_length"] = {
            "min": min_chars,
            "actual": actual_len,
            "pass": len_ok,
        }
        if not len_ok:
            result["pass"] = False

    # must_not_contain（黑名单词）
    if "must_not_contain" in expected_l3:
        violations = [
            term for term in expected_l3["must_not_contain"]
            if term in chat_answer
        ]
        blacklist_ok = len(violations) == 0
        result["details"]["blacklist"] = {
            "violations": violations,
            "pass": blacklist_ok,
        }
        if not blacklist_ok:
            result["pass"] = False

    # must_cite_terms（白名单词）
    if "must_cite_terms" in expected_l3:
        missing = [
            term for term in expected_l3["must_cite_terms"]
            if term not in chat_answer
        ]
        whitelist_ok = len(missing) == 0
        result["details"]["whitelist"] = {
            "missing": missing,
            "pass": whitelist_ok,
        }
        if not whitelist_ok:
            result["pass"] = False

    # decision_in（PositionDecision 专项）
    if "decision_in" in expected_l3:
        actual_decision = (
            decision_result.get("decisionType", "")
            or done_data.get("conclusion_level", "").lower()
        )
        decision_ok = actual_decision in expected_l3["decision_in"]
        result["details"]["decision_type"] = {
            "expected_in": expected_l3["decision_in"],
            "actual": actual_decision,
            "pass": decision_ok,
        }
        if not decision_ok:
            result["pass"] = False

    # confidence_min
    if "confidence_min" in expected_l3:
        actual_conf = decision_result.get("confidence", 1.0)
        conf_ok = actual_conf >= expected_l3["confidence_min"]
        result["details"]["confidence"] = {
            "min": expected_l3["confidence_min"],
            "actual": actual_conf,
            "pass": conf_ok,
        }
        if not conf_ok:
            result["pass"] = False

    return result


# ══════════════════════════════════════════════════════════════════
# 单用例运行
# ══════════════════════════════════════════════════════════════════

def run_case(case_path: Path) -> dict:
    with open(case_path, encoding="utf-8") as f:
        case = yaml.safe_load(f)

    case_id = case.get("case_id", case_path.stem)
    category = case.get("category", "")
    query = case["input"]["user_query"]
    portfolio_id = case["input"].get("portfolio_id", 1)
    expected = case.get("expected", {})

    print(f"  [{case_id}] {query[:50]}...", end="", flush=True)

    collected = call_and_collect(query, portfolio_id)

    l1 = eval_l1(expected.get("L1", {}), collected)
    l2 = eval_l2(expected.get("L2", {}), collected)
    l3 = eval_l3(expected.get("L3", {}), collected)

    overall = l1["pass"] and l2["pass"] and l3["pass"]
    mark = "✅" if overall else "❌"
    print(f" {mark} ({collected['duration_seconds']}s)")

    return {
        "case_id": case_id,
        "category": category,
        "query": query,
        "overall_pass": overall,
        "L1": l1,
        "L2": l2,
        "L3": l3,
        "duration_seconds": collected["duration_seconds"],
        "raw": {
            "intent": collected["intent"],
            "stages": collected["stages"],
            "candidates_count": len(collected["candidates"]),
            "validator": collected["validator"],
            "done_conclusion": collected["done"].get("conclusion_level", ""),
            "text_length": len(collected["full_text"]),
        },
    }


# ══════════════════════════════════════════════════════════════════
# HTML 报告生成
# ══════════════════════════════════════════════════════════════════

def generate_html_report(results: list, output_path: Path) -> None:
    total = len(results)
    passed = sum(1 for r in results if r["overall_pass"])
    l1_passed = sum(1 for r in results if r["L1"]["pass"])
    l2_passed = sum(1 for r in results if r["L2"]["pass"])
    l3_passed = sum(1 for r in results if r["L3"]["pass"])

    # 按 category 统计
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "passed": 0}
        categories[cat]["total"] += 1
        if r["overall_pass"]:
            categories[cat]["passed"] += 1

    rows = ""
    for r in results:
        mark = "✅" if r["overall_pass"] else "❌"
        l1_mark = "✅" if r["L1"]["pass"] else "❌"
        l2_mark = "✅" if r["L2"]["pass"] else "❌"
        l3_mark = "✅" if r["L3"]["pass"] else "❌"
        failures = []
        for layer, data in [("L1", r["L1"]), ("L2", r["L2"]), ("L3", r["L3"])]:
            for k, v in data.get("details", {}).items():
                if isinstance(v, dict) and not v.get("pass", True):
                    failures.append(f"{layer}.{k}")
        failure_str = ", ".join(failures) if failures else "-"
        rows += f"""
        <tr class="{'pass-row' if r['overall_pass'] else 'fail-row'}">
            <td>{r['case_id']}</td>
            <td>{r['category']}</td>
            <td class="query-cell">{r['query'][:60]}...</td>
            <td>{mark}</td>
            <td>{l1_mark}</td>
            <td>{l2_mark}</td>
            <td>{l3_mark}</td>
            <td>{r['raw']['done_conclusion']}</td>
            <td>{r['duration_seconds']}s</td>
            <td class="failure-cell">{failure_str}</td>
        </tr>"""

    cat_rows = ""
    for cat, stat in categories.items():
        rate = stat["passed"] / stat["total"] * 100
        cat_rows += f"<tr><td>{cat}</td><td>{stat['passed']}/{stat['total']}</td><td>{rate:.0f}%</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>WealthPilot Eval Report</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 32px; color: #1a1a1a; }}
  h1 {{ font-size: 24px; margin-bottom: 4px; }}
  .subtitle {{ color: #666; margin-bottom: 32px; font-size: 14px; }}
  .summary {{ display: flex; gap: 16px; margin-bottom: 32px; flex-wrap: wrap; }}
  .card {{ background: #f5f5f5; border-radius: 8px; padding: 16px 24px; min-width: 120px; }}
  .card .num {{ font-size: 32px; font-weight: bold; }}
  .card .label {{ font-size: 12px; color: #666; }}
  .card.green .num {{ color: #16a34a; }}
  .card.red .num {{ color: #dc2626; }}
  .card.blue .num {{ color: #2563eb; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 32px; font-size: 13px; }}
  th {{ background: #f0f0f0; padding: 8px 12px; text-align: left; border-bottom: 2px solid #ddd; }}
  td {{ padding: 7px 12px; border-bottom: 1px solid #eee; }}
  .pass-row {{ background: #f0fdf4; }}
  .fail-row {{ background: #fff1f2; }}
  .query-cell {{ max-width: 240px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .failure-cell {{ color: #dc2626; font-size: 12px; }}
  h2 {{ font-size: 16px; margin-top: 32px; margin-bottom: 12px; border-bottom: 1px solid #eee; padding-bottom: 6px; }}
</style>
</head>
<body>
<h1>WealthPilot Eval Report</h1>
<div class="subtitle">生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')} &nbsp;|&nbsp; 用例数：{total}</div>

<div class="summary">
  <div class="card {'green' if passed == total else 'red'}">
    <div class="num">{passed}/{total}</div>
    <div class="label">整体通过</div>
  </div>
  <div class="card blue">
    <div class="num">{l1_passed}/{total}</div>
    <div class="label">L1 意图识别</div>
  </div>
  <div class="card blue">
    <div class="num">{l2_passed}/{total}</div>
    <div class="label">L2 流程链路</div>
  </div>
  <div class="card blue">
    <div class="num">{l3_passed}/{total}</div>
    <div class="label">L3 决策质量</div>
  </div>
</div>

<h2>按意图类别</h2>
<table>
  <tr><th>类别</th><th>通过</th><th>通过率</th></tr>
  {cat_rows}
</table>

<h2>用例详情</h2>
<table>
  <tr>
    <th>用例</th><th>类别</th><th>问题</th>
    <th>整体</th><th>L1</th><th>L2</th><th>L3</th>
    <th>决策结果</th><th>耗时</th><th>失败项</th>
  </tr>
  {rows}
</table>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    print(f"\n📊 HTML 报告已生成：{output_path}")


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="WealthPilot M3 Eval Harness")
    parser.add_argument("--case", help="只跑某个 case_id，如 PD_001")
    parser.add_argument("--category", help="只跑某个类别，如 PositionDecision")
    parser.add_argument("--no-report", action="store_true", help="不生成 HTML 报告")
    args = parser.parse_args()

    # 收集 yaml 文件
    case_files = sorted(CASES_DIR.glob("*.yaml"))
    if args.case:
        case_files = [f for f in case_files if f.stem == args.case]
    if args.category:
        filtered = []
        for f in case_files:
            with open(f) as fp:
                c = yaml.safe_load(fp)
            if c.get("category") == args.category:
                filtered.append(f)
        case_files = filtered

    if not case_files:
        print("❌ 没有找到匹配的用例文件")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"WealthPilot M3 Eval Harness")
    print(f"用例数：{len(case_files)}")
    print(f"{'='*60}\n")

    # 健康检查
    try:
        requests.post(f"{BASE}/decision/chat",
                      json={"message": "ping", "session_id": "ping"},
                      timeout=5, stream=True).close()
    except Exception:
        print(f"❌ 无法连接后端 {BASE}，请先启动：uvicorn main:app --reload")
        sys.exit(1)

    results = []
    for case_file in case_files:
        result = run_case(case_file)
        results.append(result)
        time.sleep(1)   # 避免请求过快

    # 汇总
    total = len(results)
    passed = sum(1 for r in results if r["overall_pass"])
    l1_p = sum(1 for r in results if r["L1"]["pass"])
    l2_p = sum(1 for r in results if r["L2"]["pass"])
    l3_p = sum(1 for r in results if r["L3"]["pass"])

    print(f"\n{'='*60}")
    print(f"结果汇总")
    print(f"{'='*60}")
    print(f"整体通过：{passed}/{total} ({passed/total*100:.0f}%)")
    print(f"L1 意图识别：{l1_p}/{total}")
    print(f"L2 流程链路：{l2_p}/{total}")
    print(f"L3 决策质量：{l3_p}/{total}")

    # 失败用例
    failures = [r for r in results if not r["overall_pass"]]
    if failures:
        print(f"\n失败用例：")
        for r in failures:
            print(f"  ❌ {r['case_id']} - {r['query'][:50]}")

    # 生成报告
    if not args.no_report:
        report_dir = Path(__file__).parent.parent / "docs"
        report_dir.mkdir(exist_ok=True)
        report_path = report_dir / "eval_report.html"
        raw_path = report_dir / "eval_report_raw.json"
        generate_html_report(results, report_path)
        raw_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"📄 原始数据：{raw_path}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
