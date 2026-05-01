"""
M1.4 DecisionValidator 验证脚本

调用 /api/decision/chat，解析 SSE 的 done 事件，
检查里面是否有 validator 字段，并打印结果。
"""
import json
import os
import sys
import requests

os.environ.setdefault("AV_DEV_MOCK", "1")

BASE = "http://127.0.0.1:8000/api"

TEST_CASES = [
    {
        "case": "茅台明确标的（期望 validator passed=true）",
        "q": "茅台还能拿吗？",
        "expect_passed": True,
    },
    {
        "case": "组合评估（期望 validator passed=true）",
        "q": "我的组合现在健康吗？",
        "expect_passed": True,
    },
]


def run_and_get_done(question: str) -> dict | None:
    """调接口，收集 done 事件的 data。"""
    try:
        resp = requests.post(
            f"{BASE}/decision/chat",
            json={"message": question, "session_id": "validator-test"},
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
            if line.startswith("data:") and current_event == "done":
                try:
                    return json.loads(line[5:].strip())
                except Exception:
                    return None
    except Exception as e:
        print(f"  请求失败: {e}")
        return None


def main():
    print("\n" + "=" * 60)
    print("M1.4 DecisionValidator 验证")
    print("=" * 60)

    all_pass = True

    for tc in TEST_CASES:
        print(f"\n【{tc['case']}】")
        print(f"  Q: {tc['q']}")

        done_data = run_and_get_done(tc["q"])

        if done_data is None:
            print("  ❌ 未收到 done 事件")
            all_pass = False
            continue

        validator = done_data.get("validator")

        if validator is None:
            print("  ❌ done 事件里没有 validator 字段")
            all_pass = False
            continue

        passed = validator.get("passed")
        action = validator.get("action")
        failures = validator.get("failures", [])

        print(f"  validator.passed  = {passed}")
        print(f"  validator.action  = {action}")
        print(f"  validator.failures = {failures}")

        if passed == tc["expect_passed"] and action == "pass":
            print(f"  ✅ 符合预期")
        else:
            print(f"  ⚠️  预期 passed={tc['expect_passed']}，实际 passed={passed}")
            all_pass = False

    print("\n" + "=" * 60)
    if all_pass:
        print("🎉 所有验证通过，M1.4 Validator 工作正常")
    else:
        print("❌ 有验证项未通过，检查上面的错误信息")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
