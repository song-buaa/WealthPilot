"""
ViewpointCard → 决策引擎可消费字符串的渲染器。

输出合约（兼容 v1 决策 prompt）：
- [用户资料] 前缀：用户上传来源
- [联网参考] 前缀：Alpha Vantage / Perplexity 来源
- [ref:url] 标签：有 URL 时
- 单行句子，不换行
- 长度 80-200 字
"""

import logging
from typing import Optional

from research_v2.schemas import (
    SourceType,
    ViewpointCard,
)

logger = logging.getLogger(__name__)

_USER_SOURCES = {SourceType.USER_UPLOAD}
_THIRD_PARTY_SOURCES = {
    SourceType.ALPHA_VANTAGE_NEWS,
    SourceType.ALPHA_VANTAGE_FUNDAMENTAL,
    SourceType.ALPHA_VANTAGE_EARNINGS,
}
_ONLINE_SOURCES = {
    SourceType.PERPLEXITY_SEARCH,
}


def _get_prefix(source_type: SourceType) -> str:
    """根据来源类型返回前缀标签。"""
    if source_type in _USER_SOURCES:
        return "[用户资料]"
    if source_type in _THIRD_PARTY_SOURCES:
        return "[第三方数据]"
    if source_type in _ONLINE_SOURCES:
        return "[联网参考]"
    return "[第三方数据]"


def _extract_url(card: ViewpointCard) -> Optional[str]:
    """从 source_refs 中提取第一个 URL。"""
    for ref in card.facts.source_refs:
        if ref.ref_type == "url" and ref.ref_value:
            return ref.ref_value
    return None


def _truncate(text: str, max_len: int = 200) -> str:
    """截断到 max_len，保留完整性。"""
    text = text.replace("\n", " ").replace("\r", "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "…"


def _build_thesis_line(card: ViewpointCard, prefix: str, url: Optional[str]) -> Optional[str]:
    """构建 thesis 行。"""
    thesis = card.narrative.thesis
    if not thesis or len(thesis.strip()) < 5:
        return None
    text = _truncate(thesis.strip())
    if url:
        return f"{prefix}[ref:{url}] {text}"
    return f"{prefix} {text}"


def _build_kpi_line(card: ViewpointCard, prefix: str, url: Optional[str]) -> Optional[str]:
    """从 extracted_kpi 构建关键指标行。"""
    kpi = card.narrative.extracted_kpi
    if kpi is None:
        return None

    parts = []
    if kpi.revenue_yoy is not None:
        parts.append(f"营收同比{kpi.revenue_yoy:+.1f}%")
    if kpi.earnings_yoy is not None:
        parts.append(f"盈利同比{kpi.earnings_yoy:+.1f}%")
    if kpi.gross_margin is not None:
        parts.append(f"毛利率{kpi.gross_margin:.1f}%")
    if kpi.net_margin is not None:
        parts.append(f"净利率{kpi.net_margin:.1f}%")
    if kpi.deliveries_latest is not None:
        parts.append(f"最新交付{kpi.deliveries_latest:.0f}")
    if kpi.deliveries_yoy is not None:
        parts.append(f"交付同比{kpi.deliveries_yoy:+.1f}%")
    if kpi.analyst_target_upside is not None:
        parts.append(f"目标价上行空间{kpi.analyst_target_upside:+.1f}%")

    if not parts:
        return None

    text = _truncate("，".join(parts))
    if url:
        return f"{prefix}[ref:{url}] {text}"
    return f"{prefix} {text}"


def _build_bull_bear_line(card: ViewpointCard, prefix: str) -> Optional[str]:
    """构建 bull/bear case 简要行。"""
    parts = []
    if card.narrative.bull_case and len(card.narrative.bull_case.strip()) >= 10:
        parts.append(f"看多：{_truncate(card.narrative.bull_case.strip(), 80)}")
    if card.narrative.bear_case and len(card.narrative.bear_case.strip()) >= 10:
        parts.append(f"看空：{_truncate(card.narrative.bear_case.strip(), 80)}")
    if not parts:
        return None
    return f"{prefix} {'；'.join(parts)}"


def render_card(card: ViewpointCard) -> list[str]:
    """将一张 ViewpointCard 渲染为决策引擎可消费的字符串列表。

    返回 1-3 行字符串，每行带 [用户资料]/[联网参考] 前缀。
    """
    prefix = _get_prefix(card.facts.source_type)
    url = _extract_url(card)
    lines: list[str] = []

    thesis_line = _build_thesis_line(card, prefix, url)
    if thesis_line:
        lines.append(thesis_line)

    kpi_line = _build_kpi_line(card, prefix, url)
    if kpi_line:
        lines.append(kpi_line)

    bull_bear_line = _build_bull_bear_line(card, prefix)
    if bull_bear_line:
        lines.append(bull_bear_line)

    if not lines:
        summary = card.narrative.narrative_summary
        if summary and len(summary.strip()) >= 5:
            text = _truncate(summary.strip())
            if url:
                lines.append(f"{prefix}[ref:{url}] {text}")
            else:
                lines.append(f"{prefix} {text}")

    return lines


def render_cards(cards: list[ViewpointCard]) -> list[str]:
    """批量渲染多张卡，返回扁平字符串列表。"""
    result: list[str] = []
    for card in cards:
        result.extend(render_card(card))
    return result
