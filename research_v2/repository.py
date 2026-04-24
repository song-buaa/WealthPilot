"""
ViewpointRepository — ViewpointCard 的持久化与查询。

所有方法接受外部传入的 SQLAlchemy session，不自建 session。
"""

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import ViewpointCardV2
from research_v2.renderer import render_cards
from research_v2.schemas import (
    FactsLayer,
    JudgmentLayer,
    NarrativeLayer,
    Relation,
    ViewpointCard,
)
from research_v2.symbol import Symbol, get_registry

logger = logging.getLogger(__name__)


# ── ORM ↔ Pydantic 转换 ─────────────────────────────────────────


def _orm_to_card(row: ViewpointCardV2) -> ViewpointCard:
    """将 ORM 行转为 Pydantic ViewpointCard。"""
    facts = FactsLayer.model_validate_json(row.facts_json)
    narrative = NarrativeLayer.model_validate_json(row.narrative_json)
    judgment = JudgmentLayer.model_validate_json(row.judgment_json)
    relations = []
    if row.relations_json:
        raw_rels = json.loads(row.relations_json)
        relations = [Relation.model_validate(r) for r in raw_rels]

    return ViewpointCard(
        card_id=row.card_id,
        facts=facts,
        narrative=narrative,
        judgment=judgment,
        relations=relations,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _card_to_orm(card: ViewpointCard) -> ViewpointCardV2:
    """将 Pydantic ViewpointCard 转为 ORM 行（新建用）。"""
    facts_json = card.facts.model_dump_json()
    narrative_json = card.narrative.model_dump_json()
    judgment_json = card.judgment.model_dump_json()
    relations_json = json.dumps(
        [r.model_dump(mode="json") for r in card.relations]
    ) if card.relations else None

    # affected_symbols 存为 JSON 字符串，用于 LIKE 查询
    affected_symbols_json = json.dumps(
        [str(s) for s in card.facts.affected_symbols] if isinstance(card.facts.affected_symbols[0], str)
        else [str(s) for s in card.facts.affected_symbols]
    ) if card.facts.affected_symbols else "[]"

    return ViewpointCardV2(
        card_id=card.card_id,
        primary_symbol=card.facts.primary_symbol,
        primary_entity_id=card.facts.primary_entity_id,
        source_type=card.facts.source_type.value,
        as_of=card.facts.as_of,
        ingested_at=card.facts.ingested_at,
        facts_json=facts_json,
        narrative_json=narrative_json,
        judgment_json=judgment_json,
        validity_status=card.judgment.validity_status.value,
        confidence_score=card.judgment.decision_signal.confidence_score,
        user_endorsement=card.judgment.user_endorsement.value,
        stance=card.judgment.stance.value if card.judgment.stance else None,
        action_type=card.judgment.action_type.value if card.judgment.action_type else None,
        event_type=card.narrative.event_type.value,
        relations_json=relations_json,
        status=card.status,
    )


# ── CRUD ─────────────────────────────────────────────────────────


def insert(session: Session, card: ViewpointCard) -> ViewpointCard:
    """写入一张 ViewpointCard。"""
    row = _card_to_orm(card)
    session.add(row)
    session.flush()
    logger.info("ViewpointCard 写入: card_id=%s, symbol=%s", card.card_id, card.facts.primary_symbol)
    return card


def get_by_id(session: Session, card_id: str) -> Optional[ViewpointCard]:
    """按 card_id 取回完整 ViewpointCard。"""
    row = session.query(ViewpointCardV2).filter(ViewpointCardV2.card_id == card_id).first()
    if row is None:
        return None
    return _orm_to_card(row)


def update_judgment(session: Session, card_id: str, judgment_updates: dict, confirm: bool = False) -> Optional[ViewpointCard]:
    """更新判断层字段。

    confirm=True 时额外执行:
      is_ai_prefilled=False, confidence_score=0.6, updated_at=now
    """
    row = session.query(ViewpointCardV2).filter(ViewpointCardV2.card_id == card_id).first()
    if row is None:
        return None

    # 加载现有判断层
    current_judgment = json.loads(row.judgment_json)

    # 合并更新
    for key, value in judgment_updates.items():
        current_judgment[key] = value

    # confirm 逻辑（3 件事在同一事务）
    if confirm:
        current_judgment["is_ai_prefilled"] = False
        current_judgment["confidence"] = "medium"
        ds = current_judgment.get("decision_signal", {})
        ds["confidence_score"] = 0.6
        current_judgment["decision_signal"] = ds

    # 回写 judgment_json
    row.judgment_json = json.dumps(current_judgment, ensure_ascii=False)

    # 同步冗余索引列
    row.validity_status = current_judgment.get("validity_status", row.validity_status)
    row.confidence_score = current_judgment.get("decision_signal", {}).get("confidence_score", row.confidence_score)
    row.user_endorsement = current_judgment.get("user_endorsement", row.user_endorsement)
    row.stance = current_judgment.get("stance", row.stance)
    row.action_type = current_judgment.get("action_type", row.action_type)
    row.updated_at = datetime.now()

    session.flush()
    logger.info("ViewpointCard 判断层更新: card_id=%s, confirm=%s", card_id, confirm)
    return _orm_to_card(row)


def delete(session: Session, card_id: str) -> bool:
    """删除一张 ViewpointCard。返回是否成功。"""
    row = session.query(ViewpointCardV2).filter(ViewpointCardV2.card_id == card_id).first()
    if row is None:
        return False
    session.delete(row)
    session.flush()
    logger.info("ViewpointCard 删除: card_id=%s", card_id)
    return True


# ── 查询 ─────────────────────────────────────────────────────────


def query_cards(
    session: Session,
    symbol: Optional[str] = None,
    since: Optional[datetime] = None,
    validity: Optional[str] = None,
    min_confidence_score: Optional[float] = None,
    status: Optional[str] = None,
    event_type: Optional[str] = None,
    entity_scope: bool = True,
    top_k: int = 10,
) -> list[ViewpointCard]:
    """查询 ViewpointCard，支持 Entity 扩展。"""
    q = session.query(ViewpointCardV2)

    # Symbol 过滤（含 Entity 扩展）
    if symbol:
        sym = Symbol.parse(symbol) if isinstance(symbol, str) else symbol
        if entity_scope:
            registry = get_registry()
            expanded = registry.expand_symbols(sym)
            expanded_strs = [str(s) for s in expanded]
        else:
            expanded_strs = [str(sym)]

        # 两层匹配：primary_symbol 精确匹配 OR facts_json LIKE（affected_symbols）
        conditions = []
        for s in expanded_strs:
            conditions.append(ViewpointCardV2.primary_symbol == s)
            conditions.append(ViewpointCardV2.facts_json.like(f'%"{s}"%'))
        q = q.filter(or_(*conditions))

    if since:
        q = q.filter(ViewpointCardV2.as_of >= since)
    if validity:
        q = q.filter(ViewpointCardV2.validity_status == validity)
    if min_confidence_score is not None:
        q = q.filter(ViewpointCardV2.confidence_score >= min_confidence_score)
    if status:
        q = q.filter(ViewpointCardV2.status == status)
    if event_type:
        q = q.filter(ViewpointCardV2.event_type == event_type)

    q = q.order_by(ViewpointCardV2.as_of.desc()).limit(top_k)

    return [_orm_to_card(row) for row in q.all()]


def query_for_decision(
    session: Session,
    symbol: str,
    since: Optional[datetime] = None,
    top_k: int = 10,
) -> list[str]:
    """决策引擎消费入口。应用 4 条默认过滤后调 Renderer 输出 list[str]。

    过滤规则:
      1. validity_status = active
      2. user_endorsement in [endorse, reference_only]
      3. confidence_score >= 0.5
      4. is_ai_prefilled = False (通过 judgment_json LIKE 检查)
    """
    sym = Symbol.parse(symbol) if isinstance(symbol, str) else symbol
    registry = get_registry()
    expanded = registry.expand_symbols(sym)
    expanded_strs = [str(s) for s in expanded]

    q = session.query(ViewpointCardV2)

    # Symbol + Entity 扩展
    sym_conditions = []
    for s in expanded_strs:
        sym_conditions.append(ViewpointCardV2.primary_symbol == s)
        sym_conditions.append(ViewpointCardV2.facts_json.like(f'%"{s}"%'))
    q = q.filter(or_(*sym_conditions))

    # 4 条业务过滤
    q = q.filter(ViewpointCardV2.validity_status == "active")
    q = q.filter(ViewpointCardV2.user_endorsement.in_(["endorse", "reference_only"]))
    q = q.filter(ViewpointCardV2.confidence_score >= 0.5)
    # is_ai_prefilled=False 通过 judgment_json 检查
    q = q.filter(ViewpointCardV2.judgment_json.like('%"is_ai_prefilled": false%'))

    if since:
        q = q.filter(ViewpointCardV2.as_of >= since)

    q = q.order_by(ViewpointCardV2.as_of.desc()).limit(top_k)

    cards = [_orm_to_card(row) for row in q.all()]
    return render_cards(cards)
