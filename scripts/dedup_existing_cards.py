"""
一次性清理 viewpoint_cards_v2 表里的历史重复卡。
策略：按去重键分组，每组保留最新 created_at 的，其余标记 validity_status='invalidated'。
"""

import json
import logging
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_session
from app.models import ViewpointCardV2
from research_v2.repository import _normalize_url, _bucket_minute

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def dedup_existing():
    session = get_session()
    try:
        all_cards = session.query(ViewpointCardV2).filter(
            ViewpointCardV2.validity_status != "invalidated"
        ).all()

        logger.info("扫描 %d 张 active 卡", len(all_cards))

        groups = defaultdict(list)
        for card in all_cards:
            facts = json.loads(card.facts_json)
            refs = facts.get("source_refs", [])

            as_of_bucket = _bucket_minute(card.as_of)

            if refs and refs[0].get("ref_type") == "url":
                key = (
                    card.source_type,
                    card.primary_symbol,
                    _normalize_url(refs[0].get("ref_value", "")),
                    as_of_bucket,
                )
            else:
                key = (
                    card.source_type,
                    card.primary_symbol,
                    "__api__",
                    as_of_bucket,
                )

            groups[key].append(card)

        invalidated_count = 0
        kept_count = 0
        for key, cards in groups.items():
            if len(cards) == 1:
                kept_count += 1
                continue

            cards.sort(key=lambda c: c.created_at, reverse=True)
            kept = cards[0]
            to_invalidate = cards[1:]

            logger.info(
                "重复组: source=%s symbol=%s 共 %d 张, 保留 %s, invalidate %d 张",
                key[0], key[1], len(cards), kept.card_id[:8], len(to_invalidate),
            )

            for c in to_invalidate:
                c.validity_status = "invalidated"
                invalidated_count += 1

            kept_count += 1

        session.commit()
        logger.info("清理完成: 保留 %d 张唯一卡, invalidate %d 张重复卡", kept_count, invalidated_count)

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    dedup_existing()
