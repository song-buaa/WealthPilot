"""M7 盈米 MCP 端到端验证。"""
import os
import sys
import json
import requests
import uuid

os.environ.setdefault("AV_DEV_MOCK", "1")
os.environ.setdefault("YINGMI_API_KEY", "8TiRdtPwvewqeP_ckn5KsQ")

BASE = "http://127.0.0.1:8000/api"

TEST_CASES = [
    {
        "name": "持仓内基金（广发纳指100ETF）",
        "query": "分析一下我的纳指100ETF",
        "expect_yingmi": True,
        "expect_target": True,
    },
    {
        "name": "持仓外基金（用户明确说基金）",
        "query": "分析一下000001这只基金",
        "expect_yingmi": True,
        "expect_target": True,
    },
    {
        "name": "歧义代码（不调盈米）",
        "query": "分析一下000001",
        "expect_yingmi": False,
        "expect_target": False,
    },
    {
        "name": "存量股票（不应触发盈米）",
        "query": "茅台还能拿吗",
        "expect_yingmi": False,
        "expect_target": True,
    },
]


def run_case(case: dict) -> dict:
    """调接口收集 SSE，看 done 事件 + 是否走了盈米。"""
    print(f"\n【{case['name']}】Q: {case['query']}")

    full_text = ""
    done_data = None

    try:
        resp = requests.post(
            f"{BASE}/decision/chat",
            json={"message": case["query"], "session_id": str(uuid.uuid4())},
            headers={"Accept": "text/event-stream"},
            stream=True, timeout=120,
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
            if current_event == "text":
                full_text += data.get("delta", "")
            elif current_event == "done":
                done_data = data
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return {"pass": False}

    # 看 chat_answer 里是否提到了盈米的诊断特征（"诊断"、"基金经理"、"夏普"、"回撤"等）
    yingmi_signals = ["诊断", "基金经理", "夏普", "最大回撤", "持仓集中度", "近一年收益"]
    has_yingmi = any(sig in full_text for sig in yingmi_signals)

    print(f"  done.conclusion = {done_data.get('conclusion_level') if done_data else 'None'}")
    print(f"  text 长度 = {len(full_text)}")
    print(f"  含盈米诊断特征 = {has_yingmi}")

    case_pass = (has_yingmi == case["expect_yingmi"])
    print(f"  {'✅' if case_pass else '❌'} 预期 yingmi={case['expect_yingmi']}, 实际={has_yingmi}")
    return {"pass": case_pass}


def main():
    # 健康检查
    try:
        requests.post(f"{BASE}/decision/chat",
                      json={"message": "ping", "session_id": "ping"},
                      timeout=5, stream=True).close()
    except Exception:
        print(f"❌ 后端未启动 {BASE}")
        sys.exit(1)

    results = [run_case(tc) for tc in TEST_CASES]
    passed = sum(1 for r in results if r.get("pass"))
    print(f"\n{'='*60}")
    print(f"M7 验证: {passed}/{len(results)}")
    print('='*60)


if __name__ == "__main__":
    main()
