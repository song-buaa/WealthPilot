"""
ViewpointProcessor — 把 RawFact 加工成三层 ViewpointCard。

核心职责:
1. 根据 source_type 选择 prompt 模板
2. 调 LLM (gpt-4.1-mini) 生成 narrative + judgment
3. 强制 override 三个字段（不管 LLM 输出什么）:
   - is_ai_prefilled = True
   - confidence = low
   - decision_signal.confidence_score = 0.3
4. Pydantic 校验失败时重试一次
"""

import json
import logging
import os
import uuid
from datetime import datetime

import openai

from research_v2.adapters.base import RawFact
from research_v2.schemas import (
    Confidence,
    DecisionSignal,
    EventType,
    FactsLayer,
    JudgmentLayer,
    NarrativeLayer,
    SourceRef,
    SourceType,
    ViewpointCard,
)
from research_v2.symbol import Symbol

logger = logging.getLogger(__name__)

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

_LLM_MODEL = "gpt-4.1-mini"
_LLM_MAX_TOKENS = 2000
_LLM_TIMEOUT = 30


def _load_prompt(source_type: SourceType) -> str:
    """根据 source_type 加载对应的 prompt 模板。"""
    if source_type == SourceType.USER_UPLOAD:
        filename = "user_upload.txt"
    else:
        filename = "alpha_vantage.txt"
    path = os.path.join(_PROMPTS_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _build_user_message(raw_fact: RawFact) -> str:
    """构建发给 LLM 的 user message。"""
    primary_symbol = None
    if raw_fact.affected_symbols:
        primary_symbol = str(raw_fact.affected_symbols[0])

    input_data = {
        "source_type": raw_fact.source_type.value,
        "primary_symbol": primary_symbol,
        "raw_facts": raw_fact.payload,
    }
    return json.dumps(input_data, ensure_ascii=False, indent=2)


def _call_llm(system_prompt: str, user_message: str) -> str:
    """调用 LLM 返回原始文本。"""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("未配置 OPENAI_API_KEY 环境变量")

    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=_LLM_MODEL,
        max_tokens=_LLM_MAX_TOKENS,
        timeout=_LLM_TIMEOUT,
        temperature=0.3,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    return response.choices[0].message.content.strip()


def _parse_llm_output(raw_output: str) -> dict:
    """解析 LLM 返回的 JSON。处理可能的 markdown 包裹。"""
    text = raw_output.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end])
    return json.loads(text)


def _build_facts_layer(raw_fact: RawFact) -> FactsLayer:
    """从 RawFact 构建事实层。"""
    affected_symbols_str = [str(s) for s in raw_fact.affected_symbols]
    primary_symbol_str = affected_symbols_str[0] if affected_symbols_str else None

    return FactsLayer(
        affected_symbols=affected_symbols_str,
        primary_symbol=primary_symbol_str,
        source_type=raw_fact.source_type,
        source_refs=raw_fact.source_refs,
        as_of=raw_fact.as_of,
        ingested_at=datetime.now(),
        raw_facts=raw_fact.payload,
        sentiment_raw={"ticker_sentiment": raw_fact.payload.get("ticker_sentiment", []),
                       "overall_sentiment_score": raw_fact.payload.get("overall_sentiment_score"),
                       "overall_sentiment_label": raw_fact.payload.get("overall_sentiment_label")}
        if raw_fact.source_type == SourceType.ALPHA_VANTAGE_NEWS else None,
    )


def _build_narrative_from_llm(llm_data: dict) -> NarrativeLayer:
    """从 LLM 输出构建叙事层。"""
    narr = llm_data.get("narrative", {})
    return NarrativeLayer(
        thesis=narr.get("thesis"),
        bull_case=narr.get("bull_case"),
        bear_case=narr.get("bear_case"),
        narrative_summary=narr.get("narrative_summary"),
        event_type=EventType(narr.get("event_type", "other")),
        topics=narr.get("topics", []),
        extracted_kpi=narr.get("extracted_kpi"),
    )


def _build_judgment_from_llm(llm_data: dict) -> JudgmentLayer:
    """从 LLM 输出构建判断层，然后强制 override 关键字段。"""
    judg = llm_data.get("judgment", {})

    ds_data = judg.get("decision_signal", {})
    decision_signal = DecisionSignal(
        direction=ds_data.get("direction", 0),
        strength=ds_data.get("strength", 0.5),
        confidence_score=0.3,  # 强制 override
    )

    judgment = JudgmentLayer(
        is_ai_prefilled=True,  # 强制 override
        user_endorsement=judg.get("user_endorsement", "reference_only"),
        stance=judg.get("stance", "neutral"),
        horizon=judg.get("horizon", "medium"),
        confidence=Confidence.LOW,  # 强制 override
        decision_signal=decision_signal,
        action_type=judg.get("action_type", "hold_observe"),
        trigger_conditions=judg.get("trigger_conditions"),
        invalidation_conditions=judg.get("invalidation_conditions"),
        key_metrics_to_watch=judg.get("key_metrics_to_watch", []),
        validity_status=judg.get("validity_status", "active"),
    )
    return judgment


def _attempt_build_card(raw_fact: RawFact, llm_data: dict) -> ViewpointCard:
    """尝试从 LLM 输出构建 ViewpointCard，可能抛 ValidationError。"""
    facts = _build_facts_layer(raw_fact)
    narrative = _build_narrative_from_llm(llm_data)
    judgment = _build_judgment_from_llm(llm_data)

    return ViewpointCard(
        card_id=str(uuid.uuid4()),
        facts=facts,
        narrative=narrative,
        judgment=judgment,
        status="pending_review",
    )


def process(raw_fact: RawFact) -> ViewpointCard:
    """将 RawFact 加工为 ViewpointCard。

    流程: 加载 prompt → 调 LLM → 解析 JSON → 构建 ViewpointCard → 强制 override
    Pydantic 校验失败时重试一次，再失败抛到上层。
    """
    system_prompt = _load_prompt(raw_fact.source_type)
    user_message = _build_user_message(raw_fact)

    last_error = None
    for attempt in range(2):
        try:
            raw_output = _call_llm(system_prompt, user_message)
            llm_data = _parse_llm_output(raw_output)
            card = _attempt_build_card(raw_fact, llm_data)

            # 最终防御性检查（双保险）
            assert card.judgment.is_ai_prefilled is True
            assert card.judgment.confidence == Confidence.LOW
            assert card.judgment.decision_signal.confidence_score == 0.3

            logger.info(
                "ViewpointCard 生成成功: symbol=%s, source=%s, event=%s (attempt=%d)",
                card.facts.primary_symbol,
                card.facts.source_type.value,
                card.narrative.event_type.value,
                attempt + 1,
            )
            return card

        except (json.JSONDecodeError, KeyError, ValueError, AssertionError) as e:
            last_error = e
            if attempt == 0:
                logger.warning(
                    "ViewpointCard 构建失败 (attempt=%d), 重试: %s",
                    attempt + 1,
                    e,
                )
            continue

    raise RuntimeError(
        f"ViewpointCard 构建失败，已重试 2 次: {last_error}"
    ) from last_error
