"""
v1 _distill_research_cards 备份 — 仅供 M3-6 格式合约对比使用。

从 decision_engine/data_loader.py 原样复制，不在生产代码中使用。
"""

import json
import os
import time

_CARD_DISTILL_CACHE_V1: dict[str, tuple[float, list[str]]] = {}
_CARD_DISTILL_TTL_V1 = 24 * 3600


def distill_research_cards_v1(session, asset_name: str) -> list[str]:
    """v1 版本的 _distill_research_cards，原样保留用于格式对比。"""
    from app.models import ResearchCard, ResearchDocument

    cached = _CARD_DISTILL_CACHE_V1.get(asset_name)
    if cached is not None:
        ts, data = cached
        if time.time() - ts <= _CARD_DISTILL_TTL_V1:
            return data

    try:
        cards = (
            session.query(ResearchCard)
            .join(ResearchDocument, ResearchCard.document_id == ResearchDocument.id)
            .filter(ResearchDocument.object_name.ilike(f"%{asset_name}%"))
            .filter(ResearchDocument.parse_status.in_(["parsed", "saved_only"]))
            .order_by(ResearchDocument.uploaded_at.desc())
            .limit(5)
            .all()
        )

        if not cards:
            return []

        sections = []
        for card in cards:
            card_parts = []
            if card.thesis:
                card_parts.append(f"核心论点：{card.thesis}")
            if card.bull_case:
                card_parts.append(f"看多逻辑：{card.bull_case}")
            if card.bear_case:
                card_parts.append(f"看空风险：{card.bear_case}")
            if card.key_drivers:
                try:
                    drivers = json.loads(card.key_drivers)
                    if isinstance(drivers, list) and drivers:
                        card_parts.append("关键驱动：" + "；".join(str(d) for d in drivers[:4]))
                except Exception:
                    pass
            if card.risks:
                try:
                    risks = json.loads(card.risks)
                    if isinstance(risks, list) and risks:
                        card_parts.append("主要风险：" + "；".join(str(r) for r in risks[:3]))
                except Exception:
                    pass
            if card.action_suggestion:
                card_parts.append(f"操作建议：{card.action_suggestion}")
            if card_parts:
                sections.append("\n".join(card_parts))

        if not sections:
            return []

        combined = f"\n\n---\n".join(sections)

        openai_key = os.environ.get("OPENAI_API_KEY")
        if not openai_key:
            return []

        import openai as _openai
        client = _openai.OpenAI(api_key=openai_key)
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=400,
            timeout=15,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是投研助手，擅长从结构化投研资料中提炼关键投资观点。"
                        "输出语言为中文，简洁专业。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"以下是用户上传的关于「{asset_name}」的投研资料解析内容：\n\n"
                        f"{combined}\n\n"
                        f"请从中提炼出最重要的3-5个投资观点，按重要性从高到低排序。\n"
                        f"要求：\n"
                        f"- 每条必须是完整的结论性句子，不少于15字，不超过60字\n"
                        f"- 禁止输出标题、前言、分节符\n"
                        f"- 每条以「- 」开头\n"
                        f"- 如果多份资料有矛盾，保留最重要的正反两面各一条"
                    ),
                },
            ],
        )
        raw = response.choices[0].message.content.strip()

        lines = []
        for line in raw.split("\n"):
            cleaned = line.strip().lstrip("-•·*1234567890. \t").strip()
            if len(cleaned) >= 15:
                lines.append(cleaned)

        result = [f"[用户资料] {l}" for l in lines[:5] if l]

        if result:
            _CARD_DISTILL_CACHE_V1[asset_name] = (time.time(), result)

        return result

    except Exception as e:
        print(f"[v1_baseline] 卡片提炼失败 ({asset_name}): {e}", flush=True)
        return []
