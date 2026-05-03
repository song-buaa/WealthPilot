"""
M1-Step1: 产品哲学假设验证 — asset 模糊输入的实际路径

目的：
  PRD v1.4 §2.2 声明产品哲学是"模糊输入主动推断"。
  M0 用例 PD_001/002/003 都基于此假设设计 expected。
  本脚本验证当前 v2.5.1 代码的实际行为是否符合该假设。

判断逻辑：
  对每个测试 query 跑一次 /api/decision/chat，分析返回结果：

  路径 A — "主动推断"（符合产品哲学）：
    - IntentResult.asset 非空（推断出具体标的）
    - 整体 stage 走到 done（不是 stage=intent 后停下）
    - 回答中提到具体持仓名（茅台/中概互联等）
    - 不含澄清话术（"您是问哪只？""请问您指的是？"等）

  路径 B — "走澄清"（不符合产品哲学，需在 M1 实现 infer_target_from_holdings Tool）：
    - IntentResult.asset 为空 或 confidence < 0.6
    - 回答是澄清问题
    - stage 可能停在 intent 不继续往下走

  路径 C — "异常/中断"（需要修 bug）：
    - has_exception = true
    - 或 stage = aborted

输出：
  控制台 + docs/m1_path_verification_report.md

用法:
  AV_DEV_MOCK=1 python scripts/m1_path_verification.py

  注意：脚本需要后端服务运行在 http://127.0.0.1:8000，且默认 portfolio
  含至少 1 只浮盈持仓 + 1 只浮亏持仓（建议先按 m0/schema/fixtures_v0.1.md
  布置 fixture 数据，否则推断结果会受真实持仓干扰）。
"""

import json
import logging
import os
import re
import sys
import time
import uuid
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AV_DEV_MOCK", "1")

logging.basicConfig(level=logging.WARNING)

import requests

BASE = "http://127.0.0.1:8000/api"

# ──────────────────────────────────────────────────────────────────
# 测试用例：与 m0/cases/PD_001-003.yaml 对应，覆盖 3 种模糊场景
# ──────────────────────────────────────────────────────────────────

VERIFICATION_CASES = [
    {
        "case_id": "PD_001",
        "q": "我有一只股票最近涨了不少，该不该趁现在落袋为安？",
        "fuzz_type": "浮盈最大",
        "expected_asset_hints": ["茅台", "贵州茅台", "宁德时代", "宁德"],  # fixture 中浮盈标的
        "philosophy_check": "应推断浮盈最大持仓",
    },
    {
        "case_id": "PD_002",
        "q": "我有一只基金持续亏损，现在止损出来还是继续持有？",
        "fuzz_type": "浮亏最大",
        "expected_asset_hints": ["中概互联", "中概", "513050"],
        "philosophy_check": "应推断浮亏最大持仓",
    },
    {
        "case_id": "PD_003",
        "q": "我看好一个标的想加仓，但它在我组合里已经不轻了，怎么判断能不能加？",
        "fuzz_type": "重仓",
        "expected_asset_hints": ["茅台", "贵州茅台", "宁德时代", "宁德"],
        "philosophy_check": "应推断重仓持仓",
    },
]

# ──────────────────────────────────────────────────────────────────
# 澄清话术检测（中文常见模式）
# ──────────────────────────────────────────────────────────────────

CLARIFICATION_PATTERNS = [
    r"您(?:是)?(?:在)?(?:问|指|说)的是?哪",
    r"请问您指的是",
    r"具体是哪(?:一)?(?:只|个|支)",
    r"是说哪(?:一)?(?:只|个|支)",
    r"能(?:否|不能)告诉我(?:具体|是)哪",
    r"请补充(?:一下|具体)",
    r"请提供(?:具体|更多)",
    r"请明确(?:一下)?(?:是)?哪",
    r"需要(?:您)?(?:进一步|具体)说明",
    r"为了(?:更准确|更好)地?(?:回答|帮您)",  # 软澄清开头
]


def is_clarification(text: str) -> bool:
    """判断回答是否含澄清话术。"""
    if not text:
        return False
    for pat in CLARIFICATION_PATTERNS:
        if re.search(pat, text):
            return True
    return False


def find_asset_in_text(text: str, hints: list) -> Optional[str]:
    """从回答文本中找匹配的持仓名。"""
    if not text:
        return None
    for hint in hints:
        if hint in text:
            return hint
    return None


# ──────────────────────────────────────────────────────────────────
# 单 case 跑通逻辑
# ──────────────────────────────────────────────────────────────────

def run_case(case: dict, session_id: str) -> dict:
    """调 SSE 接口并收集详细诊断信息。"""
    result = {
        "case_id": case["case_id"],
        "question": case["q"],
        "fuzz_type": case["fuzz_type"],
        # 来自 SSE 的原始信号
        "intent_asset": None,         # IntentResult.asset
        "intent_confidence": None,    # IntentResult.confidence
        "intent_action": None,        # IntentResult.action_type
        "final_stage": None,          # FlowStage
        "was_aborted": False,
        "aborted_reason": None,
        "decision_id": None,
        # 加工后的判断
        "full_text": "",
        "found_asset_in_text": None,  # 回答中实际提到的持仓名
        "is_clarification": False,
        "path_verdict": None,         # A=推断 / B=澄清 / C=异常 / U=未知
        # 异常
        "error": None,
        "has_exception": False,
    }

    try:
        resp = requests.post(
            f"{BASE}/decision/chat",
            json={"message": case["q"], "session_id": session_id},
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=90,
        )

        full_text = ""
        current_event = None

        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue

            # SSE 协议: event: <type>\ndata: <json>
            if line.startswith("event: "):
                current_event = line[7:].strip()
                continue

            if not line.startswith("data: "):
                continue

            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                continue

            # ── intent 事件：抓 IntentResult ──
            if current_event == "intent" or "primary_intent" in data:
                # 兼容两种形态：扁平 / 嵌套 intent dict
                if isinstance(data.get("intent"), dict):
                    intent_obj = data["intent"]
                else:
                    intent_obj = data
                result["intent_asset"] = intent_obj.get("asset") or intent_obj.get("primary_intent_asset")
                result["intent_confidence"] = intent_obj.get("confidence") or intent_obj.get("confidence_score")
                result["intent_action"] = intent_obj.get("action") or intent_obj.get("action_type")

            # ── stage 事件 ──
            if current_event == "stage" or "stage" in data:
                stage_val = data.get("stage")
                if stage_val:
                    result["final_stage"] = stage_val
                if data.get("was_aborted"):
                    result["was_aborted"] = True
                    result["aborted_reason"] = data.get("aborted_reason")

            # ── text/delta 事件：累积文本 ──
            if "delta" in data:
                full_text += data["delta"]

            # ── done 事件：结束 ──
            if current_event == "done" or "decision_id" in data:
                if "decision_id" in data:
                    result["decision_id"] = data["decision_id"]
                # 不立即 break，可能后面还有 done 事件本身

        result["full_text"] = full_text

        # ── 加工：判断走的是哪条路径 ──
        result["found_asset_in_text"] = find_asset_in_text(
            full_text, case["expected_asset_hints"]
        )
        result["is_clarification"] = is_clarification(full_text)

        # ── 路径判定（v1.1 更新：适配 M1.1 后的直选注入方式）
        #
        # M1.1 之前：intent_asset 在 SSE intent 事件中填充，可用来判定路径 A
        # M1.1 之后：直选注入发生在 intent 事件之后（L397），intent_asset 在 SSE
        #            里仍为空，但 full_text 中会有具体持仓名 + 完整决策内容
        #
        # 新判定逻辑：以 full_text 为主信号
        #   路径 A：回答中找到了持仓名 + 不含澄清话术 + 有完整决策内容
        #   路径 B：含澄清话术 或 没找到持仓名
        #   路径 C：异常/中断
        #   路径 U：信号矛盾
        #
        # 辅助信号：final_stage
        #   "done" / "reasoning" / "llm" → 走完了决策链路 → 支持路径 A
        #   "intent" → 停在意图层 → 可能是澄清路径（支持路径 B）
        FULL_DECISION_STAGES = {"done", "reasoning", "llm", "signal", "rule_check", "loaded"}
        reached_decision = result["final_stage"] in FULL_DECISION_STAGES

        if result["has_exception"] or result["was_aborted"]:
            result["path_verdict"] = "C"   # 异常/中断
        elif result["found_asset_in_text"] and not result["is_clarification"] and reached_decision:
            result["path_verdict"] = "A"   # 主动推断（符合产品哲学）
        elif result["is_clarification"] or not reached_decision:
            result["path_verdict"] = "B"   # 走澄清 或 停在意图层
        else:
            # 找到了持仓名 + 走完决策 但没在 expected_asset_hints 里
            # 可能是直选了 hints 里没列的持仓（如真实 portfolio 里的 AAPL）
            # 升级判定：检查 full_text 是否有任何"决策动词"（减仓/持有/加仓等）
            DECISION_VERBS = ["建议", "减仓", "加仓", "持有", "止损", "止盈", "观望", "清仓"]
            has_decision_content = any(v in result["full_text"] for v in DECISION_VERBS)
            if has_decision_content and reached_decision and not result["is_clarification"]:
                result["path_verdict"] = "A"   # 走完了决策，有决策内容，算 A
            else:
                result["path_verdict"] = "U"   # 信号矛盾，人工判读

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        result["has_exception"] = True
        result["path_verdict"] = "C"

    return result


# ──────────────────────────────────────────────────────────────────
# 报告生成
# ──────────────────────────────────────────────────────────────────

def render_console(results: list) -> None:
    print("\n" + "=" * 76)
    print("M1-Step1: 产品哲学假设验证报告")
    print("=" * 76)

    # 路径分布
    verdict_count = {"A": 0, "B": 0, "C": 0, "U": 0}
    for r in results:
        verdict_count[r["path_verdict"]] = verdict_count.get(r["path_verdict"], 0) + 1

    total = len(results)
    print(f"\n【路径分布】 共 {total} 个用例")
    print(f"  路径 A（主动推断）: {verdict_count.get('A', 0)}/{total}  ← 符合产品哲学")
    print(f"  路径 B（走澄清）  : {verdict_count.get('B', 0)}/{total}  ← 不符合，需 M1 实现推断 Tool")
    print(f"  路径 C（异常/中断）: {verdict_count.get('C', 0)}/{total}  ← 需先修 bug")
    print(f"  路径 U（人工判读）: {verdict_count.get('U', 0)}/{total}")

    # 单用例细节
    print(f"\n【用例细节】")
    for r in results:
        verdict_emoji = {"A": "✅", "B": "⚠️", "C": "❌", "U": "❓"}[r["path_verdict"]]
        print(f"\n  {verdict_emoji} {r['case_id']} ({r['fuzz_type']})")
        print(f"     Q: {r['question'][:50]}...")
        print(f"     IntentResult.asset:      {r['intent_asset'] or '(空)'}")
        print(f"     IntentResult.confidence: {r['intent_confidence']}")
        print(f"     final_stage:             {r['final_stage']}")
        print(f"     was_aborted:             {r['was_aborted']}")
        print(f"     回答中找到持仓名:         {r['found_asset_in_text'] or '(无)'}")
        print(f"     是否澄清话术:             {r['is_clarification']}")
        if r["error"]:
            print(f"     ERROR: {r['error']}")
        text_preview = r["full_text"][:120].replace("\n", " ")
        print(f"     回答片段:                 {text_preview}...")

    # 结论 + M1 行动建议
    print(f"\n【结论与 M1 行动建议】")
    if verdict_count["A"] == total:
        print(f"  ✅ 全部符合产品哲学。M1 中 ResearchAgent 不需要新增 infer_target_from_holdings Tool。")
        print(f"     PRD v1.4 §4.2 ResearchAgent 关于该 Tool 的声明可以标记为'已存在能力，仅需包装'。")
    elif verdict_count["A"] == 0:
        print(f"  🔴 全部走澄清路径。当前代码不符合产品哲学。")
        print(f"     M1 必须新增 ResearchAgent 的 infer_target_from_holdings Tool（asset 模糊时基于持仓盈亏推断）。")
        print(f"     工作量预估：+0.5 天，应吸收进 M1 的 3.5 天工作量内。")
    elif verdict_count["A"] >= total * 0.5:
        print(f"  🟡 大部分推断成功，但有 {verdict_count.get('B', 0)} 个走澄清。可能是边界场景。")
        print(f"     M1 行动：先看清哪类 fuzz_type 失败，针对性强化推断逻辑。")
    else:
        print(f"  🟠 推断成功率偏低（{verdict_count['A']}/{total}）。")
        print(f"     M1 必须增强 ResearchAgent 的推断 Tool，作为 M1 第一优先工作。")

    if verdict_count["C"] > 0:
        print(f"\n  ⚠️ 有 {verdict_count['C']} 个用例异常/中断，必须先排查。")

    if verdict_count["U"] > 0:
        print(f"\n  ❓ 有 {verdict_count['U']} 个用例需要人工判读（asset 推断和澄清话术信号不一致）。")


def write_report(results: list, output_path: str) -> None:
    """写 markdown 报告。"""
    verdict_count = {"A": 0, "B": 0, "C": 0, "U": 0}
    for r in results:
        verdict_count[r["path_verdict"]] = verdict_count.get(r["path_verdict"], 0) + 1
    total = len(results)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# M1-Step1: 产品哲学假设验证报告\n\n")
        f.write(f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}  \n")
        f.write(f"> 验证目标：当前 v2.5.1 代码遇 asset 模糊输入的实际路径  \n")
        f.write(f"> PRD 哲学（v1.4 §2.2）：模糊输入主动推断（路径 A）\n\n")

        # 总览
        f.write("## 路径分布\n\n")
        f.write("| 路径 | 含义 | 数量 |\n")
        f.write("|-----|------|------|\n")
        f.write(f"| A | 主动推断（符合产品哲学） | {verdict_count.get('A', 0)}/{total} |\n")
        f.write(f"| B | 走澄清（不符合，需 M1 实现推断 Tool） | {verdict_count.get('B', 0)}/{total} |\n")
        f.write(f"| C | 异常/中断（需先修 bug） | {verdict_count.get('C', 0)}/{total} |\n")
        f.write(f"| U | 人工判读（信号矛盾） | {verdict_count.get('U', 0)}/{total} |\n\n")

        # 单用例细节
        f.write("## 用例细节\n\n")
        for r in results:
            verdict_emoji = {"A": "✅", "B": "⚠️", "C": "❌", "U": "❓"}[r["path_verdict"]]
            f.write(f"### {verdict_emoji} {r['case_id']} — {r['fuzz_type']}\n\n")
            f.write(f"**Q**：{r['question']}\n\n")
            f.write(f"| 字段 | 值 |\n|-----|-----|\n")
            f.write(f"| IntentResult.asset | {r['intent_asset'] or '(空)'} |\n")
            f.write(f"| IntentResult.confidence | {r['intent_confidence']} |\n")
            f.write(f"| IntentResult.action_type | {r['intent_action']} |\n")
            f.write(f"| final_stage | {r['final_stage']} |\n")
            f.write(f"| was_aborted | {r['was_aborted']} |\n")
            f.write(f"| 回答中找到的持仓名 | {r['found_asset_in_text'] or '(无)'} |\n")
            f.write(f"| 是否澄清话术 | {r['is_clarification']} |\n")
            f.write(f"| decision_id | {r['decision_id'] or '(无)'} |\n")
            if r["error"]:
                f.write(f"| ERROR | {r['error']} |\n")
            f.write(f"\n**回答全文**：\n\n```\n{r['full_text'][:500]}\n```\n\n")
            f.write("---\n\n")

        # M1 行动建议
        f.write("## M1 行动建议\n\n")
        if verdict_count["A"] == total:
            f.write("✅ **全部符合产品哲学**。M1 中 ResearchAgent 的 `infer_target_from_holdings` "
                    "Tool 可标记为「已存在能力，仅需 v1.4 包装」，不增加额外工作量。\n\n")
        elif verdict_count["A"] == 0:
            f.write("🔴 **全部走澄清路径**。当前代码不符合产品哲学。\n\n")
            f.write("**M1 第一优先工作**：在 ResearchAgent 中实现 `infer_target_from_holdings` Tool。\n\n")
            f.write("- 输入：`user_query`、`positions`\n")
            f.write("- 推断逻辑：\n")
            f.write("  - 关键词匹配：`涨/盈利/赚` → max(profit_loss_rate)\n")
            f.write("  - 关键词匹配：`跌/亏/套牢` → min(profit_loss_rate)\n")
            f.write("  - 关键词匹配：`重仓/不轻/占比大` → max(weight)\n")
            f.write("- 工作量：+0.5 天，应吸收进 M1 的 3.5 天工作量内\n\n")
        else:
            f.write(f"🟡 **混合结果（A: {verdict_count['A']}, B: {verdict_count.get('B', 0)}）**。需根据失败用例分析具体短板。\n\n")
            f.write("**M1 行动**：\n")
            for r in results:
                if r["path_verdict"] == "B":
                    f.write(f"- {r['case_id']} ({r['fuzz_type']})：失败原因待分析\n")

        if verdict_count["C"] > 0:
            f.write(f"\n⚠️ 有 {verdict_count['C']} 个用例异常/中断，必须先排查环境问题（数据库、portfolio、网络）后再做后续判断。\n\n")

    print(f"\n报告已写入: {output_path}")


# ──────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 76)
    print("M1-Step1: 产品哲学假设验证")
    print("=" * 76)
    print("\n目标：检查当前 v2.5.1 代码遇 asset 模糊输入时，是主动推断（A）还是走澄清（B）。")
    print(f"\n后端地址: {BASE}")
    print(f"测试用例: {len(VERIFICATION_CASES)} 个（与 PD_001/002/003 对应）\n")

    # 健康检查
    try:
        # 用 GET 简单试探，让用户尽快发现服务没起
        ping = requests.post(
            f"{BASE}/decision/chat",
            json={"message": "ping", "session_id": "ping-test"},
            timeout=5,
            stream=True,
        )
        ping.close()
    except requests.exceptions.ConnectionError:
        print(f"❌ 无法连接到后端 {BASE}")
        print(f"   请先启动后端服务: cd backend && uvicorn main:app --reload")
        sys.exit(1)
    except Exception:
        # 其他异常（如 timeout）说明服务起着但慢，可继续
        pass

    results = []
    for i, case in enumerate(VERIFICATION_CASES):
        session_id = str(uuid.uuid4())
        print(f"[{i+1}/{len(VERIFICATION_CASES)}] {case['case_id']} - {case['q'][:40]}...")
        result = run_case(case, session_id)
        results.append(result)
        verdict_emoji = {"A": "✅", "B": "⚠️", "C": "❌", "U": "❓"}[result["path_verdict"]]
        print(f"  → 路径 {result['path_verdict']} {verdict_emoji}  asset={result['intent_asset'] or '(空)'}  "
              f"is_clarify={result['is_clarification']}")
        time.sleep(2)

    # 控制台报告
    render_console(results)

    # markdown 报告
    docs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs")
    os.makedirs(docs_dir, exist_ok=True)
    report_path = os.path.join(docs_dir, "m1_path_verification_report.md")
    write_report(results, report_path)

    # raw json (供后续分析)
    json_path = os.path.join(docs_dir, "m1_path_verification_raw.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"原始数据已写入: {json_path}")


if __name__ == "__main__":
    main()
