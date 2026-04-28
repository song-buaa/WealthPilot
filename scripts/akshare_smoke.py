"""
AKShareAdapter 端到端 smoke test。

用法: AKSHARE_DEV_MOCK=1 python scripts/akshare_smoke.py 3690:HK
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AKSHARE_DEV_MOCK", "1")

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/akshare_smoke.py <SYMBOL>")
        print("示例: AKSHARE_DEV_MOCK=1 python scripts/akshare_smoke.py 3690:HK")
        sys.exit(1)

    symbol_str = sys.argv[1]

    from research_v2.symbol import Symbol
    from research_v2.router import InfoRouter
    from research_v2 import processor

    symbol = Symbol.parse(symbol_str)
    router = InfoRouter()

    print(f"=== AKShare Smoke Test: {symbol} ===")
    print(f"AKSHARE_DEV_MOCK={os.environ.get('AKSHARE_DEV_MOCK', '未设置')}")

    raw_facts = router.fetch_all(symbol)
    print(f"\nRawFacts: {len(raw_facts)} 条")

    cards = []
    for i, rf in enumerate(raw_facts):
        print(f"\n--- [{i}] {rf.source_type.value} ---")
        try:
            card = processor.process(rf)
            cards.append(card)
            print(f"✅ → event={card.narrative.event_type.value}, thesis={card.narrative.thesis[:60]}...")
        except Exception as e:
            print(f"❌ → {type(e).__name__}: {e}")

    print(f"\n=== 结果: {len(cards)}/{len(raw_facts)} 张卡 ===")

    if cards:
        print(f"\n第一张卡完整 JSON:")
        print(json.dumps(cards[0].model_dump(mode="json"), ensure_ascii=False, indent=2)[:2000])


if __name__ == "__main__":
    main()
