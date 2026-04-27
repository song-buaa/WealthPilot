"""
M5-1: 持仓覆盖度验证。

检查每只 :US 持仓标的在 v2 体系内的 ViewpointCard 覆盖情况。
不 confirm 任何卡，只验证链路通畅。
"""

import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AV_DEV_MOCK", "1")

logging.basicConfig(level=logging.WARNING)

import requests

BASE = "http://127.0.0.1:8000/api"


def main():
    print("=" * 70)
    print("M5-1: 持仓覆盖度验证")
    print("=" * 70)

    # 1. 获取 US 持仓
    resp = requests.get(f"{BASE}/research/v2/holdings_us", timeout=10)
    holdings = resp.json()
    print(f"\n持仓标的数: {len(holdings)}")

    results = []
    for h in holdings:
        symbol = h["symbol"]
        name = h["name"]

        # 2. 查现有 v2 卡
        resp = requests.get(f"{BASE}/research/v2/cards", params={"symbol": symbol, "top_k": 50}, timeout=10)
        data = resp.json()
        cards = data.get("cards", [])

        confirmed = sum(1 for c in cards if not c["judgment"]["is_ai_prefilled"] and c["judgment"]["validity_status"] == "active")
        pending = sum(1 for c in cards if c["judgment"]["is_ai_prefilled"] and c["judgment"]["validity_status"] == "active")

        fetch_status = "已有卡"
        if confirmed == 0 and pending == 0:
            # 3. 触发拉取
            print(f"  {symbol} ({name}): 无卡，触发拉取...")
            try:
                resp = requests.post(f"{BASE}/research/v2/ingest/alpha_vantage",
                                     json={"symbol": symbol}, timeout=120)
                ingest = resp.json()
                new_cards = len(ingest.get("cards", []))
                new_errors = len(ingest.get("errors", []))
                fetch_status = f"拉取成功({new_cards}张)" if new_cards > 0 else f"拉取空({new_errors}错误)"

                # 重新查
                resp = requests.get(f"{BASE}/research/v2/cards", params={"symbol": symbol, "top_k": 50}, timeout=10)
                cards = resp.json().get("cards", [])
                confirmed = sum(1 for c in cards if not c["judgment"]["is_ai_prefilled"] and c["judgment"]["validity_status"] == "active")
                pending = sum(1 for c in cards if c["judgment"]["is_ai_prefilled"] and c["judgment"]["validity_status"] == "active")
            except Exception as e:
                fetch_status = f"拉取失败: {e}"

        results.append({
            "symbol": symbol,
            "name": name,
            "confirmed": confirmed,
            "pending": pending,
            "total_active": confirmed + pending,
            "fetch_status": fetch_status,
        })

    # 输出表格
    print(f"\n{'Symbol':12s} {'名称':12s} {'confirmed':>9s} {'pending':>7s} {'total':>5s} {'拉取状态'}")
    print("-" * 80)
    all_covered = True
    for r in results:
        covered = r["total_active"] >= 1
        mark = "✅" if covered else "❌"
        print(f"{r['symbol']:12s} {r['name']:12s} {r['confirmed']:>9d} {r['pending']:>7d} {r['total_active']:>5d} {r['fetch_status']} {mark}")
        if not covered:
            all_covered = False

    print(f"\n{'='*70}")
    if all_covered:
        print("✅ M5-1 PASS: 全部持仓标的至少 1 张 active 卡")
    else:
        print("❌ M5-1 FAIL: 有标的无任何 active 卡")


if __name__ == "__main__":
    main()
