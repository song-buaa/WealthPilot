"""
ViewpointRepository — ViewpointCard 的持久化与查询。

所有方法接受外部传入的 SQLAlchemy session，不自建 session。
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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


# ── 去重工具 ──────────────────────────────────────────────────────

_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                    "ref", "source", "fbclid", "gclid"}


def _normalize_url(url: str) -> str:
    """规范化 URL：去掉 tracking 参数 + fragment，小写 host。"""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        query_params = [(k, v) for k, v in parse_qsl(parsed.query) if k.lower() not in _TRACKING_PARAMS]
        query_params.sort()
        normalized = parsed._replace(
            netloc=parsed.netloc.lower(),
            query=urlencode(query_params),
            fragment="",
        )
        return urlunparse(normalized)
    except Exception:
        return url.strip()


def _bucket_minute(dt: datetime) -> datetime:
    """对齐到分钟级（忽略秒和微秒）。"""
    return dt.replace(second=0, microsecond=0)


def _find_duplicate(session: Session, card: "ViewpointCard") -> Optional[ViewpointCardV2]:
    """按去重规则查找已存在的卡。返回 ORM 对象或 None。"""
    source_type = card.facts.source_type.value
    primary_symbol = card.facts.primary_symbol
    if not primary_symbol:
        return None

    as_of_bucket = _bucket_minute(card.facts.as_of)
    as_of_end = as_of_bucket + timedelta(minutes=1)

    query = (
        session.query(ViewpointCardV2)
        .filter(ViewpointCardV2.source_type == source_type)
        .filter(ViewpointCardV2.primary_symbol == primary_symbol)
        .filter(ViewpointCardV2.as_of >= as_of_bucket)
        .filter(ViewpointCardV2.as_of < as_of_end)
        .filter(ViewpointCardV2.validity_status != "invalidated")
    )

    refs = card.facts.source_refs
    if refs and refs[0].ref_type == "url":
        # URL 类：精确到分钟桶 + URL 比对
        target_url = _normalize_url(refs[0].ref_value)
        candidates = query.all()
        for cand in candidates:
            cand_facts = json.loads(cand.facts_json)
            cand_refs = cand_facts.get("source_refs", [])
            if cand_refs and cand_refs[0].get("ref_type") == "url":
                cand_url = _normalize_url(cand_refs[0].get("ref_value", ""))
                if cand_url == target_url:
                    return cand
        return None
    else:
        # api_call_id 类（fundamental/earnings）：放宽到日级别去重
        # 因为 fundamental 的 as_of 是拉取时间（每次不同），分钟桶太严
        day_start = as_of_bucket.replace(hour=0, minute=0)
        day_end = day_start + timedelta(days=1)
        day_query = (
            session.query(ViewpointCardV2)
            .filter(ViewpointCardV2.source_type == source_type)
            .filter(ViewpointCardV2.primary_symbol == primary_symbol)
            .filter(ViewpointCardV2.as_of >= day_start)
            .filter(ViewpointCardV2.as_of < day_end)
            .filter(ViewpointCardV2.validity_status != "invalidated")
        )
        return day_query.first()


# ── CRUD ─────────────────────────────────────────────────────────


def insert(session: Session, card: ViewpointCard) -> ViewpointCard:
    """写入一张 ViewpointCard。如已存在相同内容的卡则跳过，返回已存在的 card。"""
    existing = _find_duplicate(session, card)
    if existing:
        logger.info(
            "检测到重复卡，跳过插入: source=%s symbol=%s as_of=%s existing_id=%s",
            card.facts.source_type.value,
            card.facts.primary_symbol,
            card.facts.as_of,
            existing.card_id,
        )
        return _orm_to_card(existing)

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
    # 第 5 条过滤：过期的卡不进决策（expires_at 在 judgment_json 里）
    now = datetime.now()
    cards = [c for c in cards if c.judgment.expires_at is None or c.judgment.expires_at > now]
    return render_cards(cards)


def query_recent_av(
    session: Session,
    symbol: str,
    days: int = 3,
    top_k: int = 10,
) -> list[str]:
    """查最近 N 天内生成的 AV 卡（不管是否 confirmed），用于补充实时数据。

    返回 Renderer 输出的 list[str]，pending 卡带"(AI 自动生成，未经人工审核)"标注。
    """
    sym = Symbol.parse(symbol) if isinstance(symbol, str) else symbol
    registry = get_registry()
    expanded = registry.expand_symbols(sym)
    expanded_strs = [str(s) for s in expanded]

    cutoff = datetime.now() - timedelta(days=days)

    q = session.query(ViewpointCardV2)

    sym_conditions = []
    for s in expanded_strs:
        sym_conditions.append(ViewpointCardV2.primary_symbol == s)
    q = q.filter(or_(*sym_conditions))

    q = q.filter(ViewpointCardV2.source_type.in_([
        "alpha_vantage_news", "alpha_vantage_fundamental", "alpha_vantage_earnings",
    ]))
    q = q.filter(ViewpointCardV2.ingested_at >= cutoff)
    q = q.filter(ViewpointCardV2.validity_status == "active")

    q = q.order_by(ViewpointCardV2.as_of.desc()).limit(top_k)

    cards = [_orm_to_card(row) for row in q.all()]

    result = []
    for card in cards:
        card_lines = render_cards([card])
        for cl in card_lines:
            if card.judgment.is_ai_prefilled:
                result.append(f"{cl} (AI 自动生成，未经人工审核)")
            else:
                result.append(cl)
    return result


def query_for_decision_relaxed(
    session: Session,
    symbol: str,
    since: Optional[datetime] = None,
    top_k: int = 10,
) -> list[str]:
    """宽松版决策查询：包含 pending 卡（is_ai_prefilled=True 也返回）。

    用于自动拉取后的即时使用场景。只过滤 validity_status=active。
    输出每行追加 "(AI 自动生成，未经人工审核)" 标注。
    """
    sym = Symbol.parse(symbol) if isinstance(symbol, str) else symbol
    registry = get_registry()
    expanded = registry.expand_symbols(sym)
    expanded_strs = [str(s) for s in expanded]

    q = session.query(ViewpointCardV2)

    sym_conditions = []
    for s in expanded_strs:
        sym_conditions.append(ViewpointCardV2.primary_symbol == s)
        sym_conditions.append(ViewpointCardV2.facts_json.like(f'%"{s}"%'))
    q = q.filter(or_(*sym_conditions))

    q = q.filter(ViewpointCardV2.validity_status == "active")
    q = q.filter(ViewpointCardV2.user_endorsement.in_(["endorse", "reference_only"]))

    if since:
        q = q.filter(ViewpointCardV2.as_of >= since)

    q = q.order_by(ViewpointCardV2.as_of.desc()).limit(top_k)

    cards = [_orm_to_card(row) for row in q.all()]
    # 过期过滤
    now = datetime.now()
    cards = [c for c in cards if c.judgment.expires_at is None or c.judgment.expires_at > now]
    lines = render_cards(cards)
    # 给 pending 卡的输出行加标注
    result = []
    card_map = {c.card_id: c for c in cards}
    line_idx = 0
    for card in cards:
        card_lines = render_cards([card])
        for cl in card_lines:
            if card.judgment.is_ai_prefilled:
                result.append(f"{cl} (AI 自动生成，未经人工审核)")
            else:
                result.append(cl)
    return result
