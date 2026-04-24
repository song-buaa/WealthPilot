"""
M1 端到端 smoke test。

用法:
  AV_DEV_MOCK=1 python scripts/m1_smoke.py LI:US

流程: Router → Adapter.fetch → Processor.process → 打印 ViewpointCard JSON
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python scripts/m1_smoke.py <SYMBOL>")
        print("示例: AV_DEV_MOCK=1 python scripts/m1_smoke.py LI:US")
        sys.exit(1)

    symbol_str = sys.argv[1]

    from research_v2.symbol import Symbol
    from research_v2.router import InfoRouter
    from research_v2 import processor

    symbol = Symbol.parse(symbol_str)
    router = InfoRouter()

    logger.info("=== M1 Smoke Test: %s ===", symbol)
    logger.info("AV_DEV_MOCK=%s", os.environ.get("AV_DEV_MOCK", "未设置"))

    # 分别调用三个子能力
    av = router.av_adapter

    sources = [
        ("news", av.fetch_news),
        ("fundamental", av.fetch_fundamental),
        ("earnings", av.fetch_earnings),
    ]

    cards = []
    for name, fetch_fn in sources:
        logger.info("--- Fetching %s ---", name)
        try:
            raw_facts = fetch_fn(symbol)
        except Exception as e:
            logger.error("Fetch %s 失败: %s", name, e)
            continue

        if not raw_facts:
            logger.warning("Fetch %s 返回 0 条 RawFact", name)
            continue

        # 只取第一条做 process
        rf = raw_facts[0]
        logger.info(
            "RawFact: source_type=%s, as_of=%s, payload_keys=%s",
            rf.source_type.value,
            rf.as_of.isoformat(),
            list(rf.payload.keys()),
        )

        try:
            card = processor.process(rf)
            cards.append((name, card))
            logger.info("✅ %s → ViewpointCard 生成成功", name)
        except Exception as e:
            logger.error("❌ %s → Processor 失败: %s", name, e)

    # 打印结果
    logger.info("\n=== 结果汇总: %d / %d 张卡生成成功 ===", len(cards), len(sources))

    for name, card in cards:
        print(f"\n{'='*60}")
        print(f"[{name}] card_id={card.card_id}")
        print(f"{'='*60}")
        card_dict = card.model_dump(mode="json")
        print(json.dumps(card_dict, ensure_ascii=False, indent=2))

        # 关键字段校验
        checks = []
        checks.append(("facts.affected_symbols 不空", len(card.facts.affected_symbols) > 0))
        checks.append(("facts.primary_symbol 合法", card.facts.primary_symbol is not None))
        checks.append(("facts.source_type 正确", card.facts.source_type.value.startswith("alpha_vantage")))
        checks.append(("facts.raw_facts 非空", bool(card.facts.raw_facts)))
        checks.append(("narrative.thesis 不空", bool(card.narrative.thesis)))
        checks.append(("narrative.event_type != OTHER", card.narrative.event_type.value != "other"))
        checks.append(("judgment.is_ai_prefilled=True", card.judgment.is_ai_prefilled is True))
        checks.append(("judgment.confidence=low", card.judgment.confidence.value == "low"))
        checks.append(("judgment.decision_signal.confidence_score=0.3", card.judgment.decision_signal.confidence_score == 0.3))
        checks.append(("judgment.stance 不 null", card.judgment.stance is not None))

        print(f"\n--- 校验 [{name}] ---")
        all_pass = True
        for desc, ok in checks:
            status = "✅" if ok else "❌"
            print(f"  {status} {desc}")
            if not ok:
                all_pass = False

        if not all_pass:
            logger.warning("[%s] 部分校验未通过", name)

    if len(cards) == 0:
        logger.error("没有生成任何 ViewpointCard，smoke test 失败")
        sys.exit(1)

    logger.info("\n=== M1 Smoke Test 完成 ===")


if __name__ == "__main__":
    main()
