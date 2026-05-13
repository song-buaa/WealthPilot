"""
Memory Service — 决策历史查询（Long-term Memory）

提供按标的名称或 session 检索历史决策记录的能力。
"""

from __future__ import annotations

from typing import Optional

from backend.utils.datetime_utils import utc_iso


def get_decision_history(
    asset_name: str = "",
    conversation_id: str = "",
    limit: int = 5,
) -> list[dict]:
    """
    查询历史决策记录。
    - 按标的名称查：asset_name="茅台"
    - 按 session 查：conversation_id="xxx"
    - 两者都传：AND 条件
    """
    from app.database import get_session
    from app.models import DecisionHistory
    from sqlalchemy import desc

    db = get_session()
    try:
        query = db.query(DecisionHistory)
        if asset_name:
            query = query.filter(
                DecisionHistory.asset_name.contains(asset_name)
            )
        if conversation_id:
            query = query.filter(
                DecisionHistory.conversation_id == conversation_id
            )
        records = query.order_by(desc(DecisionHistory.created_at)) \
                       .limit(limit).all()
        return [
            {
                "decision_id": r.decision_id,
                "asset_name": r.asset_name,
                "decision_type": r.decision_type,
                "confidence": r.confidence,
                "chat_answer_preview": (r.chat_answer or "")[:100],
                "created_at": utc_iso(r.created_at) or "",
            }
            for r in records
        ]
    finally:
        db.close()
