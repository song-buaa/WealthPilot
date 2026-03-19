"""
WealthPilot - 投研观点检索模块
提供结构化字段检索 + 关键词匹配 + 综合排序，MVP 阶段不使用 embedding。

后续升级为正式 RAG 时，只需替换 retrieve_research_context() 的内部实现，
调用接口和返回结构保持不变。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from app.models import ResearchViewpoint, get_session


# ──────────────────────────────────────────────────────────
# 内部工具
# ──────────────────────────────────────────────────────────

def _parse_json_list(s: Optional[str]) -> list[str]:
    """安全解析 JSON 列表字段，失败时返回空列表"""
    if not s:
        return []
    try:
        val = json.loads(s)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _keyword_score(query: str, *text_fields: Optional[str]) -> int:
    """
    简单关键词得分：query 中每个词（2字以上）在任意 field 中出现则 +1。
    中文不分词，按字符滑动窗口匹配。
    """
    if not query:
        return 0
    # 简单分词：按空格和标点切分，保留 2+ 字符的词
    import re
    words = [w for w in re.split(r'[\s，。、？！,.!?]+', query) if len(w) >= 2]
    combined = " ".join(f or "" for f in text_fields).lower()
    return sum(1 for w in words if w.lower() in combined)


_APPROVAL_WEIGHT = {"strong": 3, "partial": 2, "reference": 1}
_VALIDITY_WEIGHT = {"active": 10, "watch": 5, "outdated": 1, "invalid": 0}


def _score_viewpoint(vp: ResearchViewpoint, query: str,
                     object_name: Optional[str], market_name: Optional[str]) -> float:
    """
    综合评分（越高越相关）：
      - object_name 精确匹配：+20 / 模糊包含：+10
      - market_name 匹配：+8
      - topic_tags 关键词匹配：+5 per hit
      - thesis / supporting_points / risks 关键词匹配：+1 per hit
      - validity_status 权重：active=10, watch=5, outdated=1, invalid=0
      - user_approval_level 权重：strong=3, partial=2, reference=1
      - 新鲜度：updated_at 越新越高（天数换算，最多 +5）
    """
    score = 0.0

    # object_name 匹配
    if object_name and vp.object_name:
        if vp.object_name.lower() == object_name.lower():
            score += 20
        elif object_name.lower() in vp.object_name.lower() or vp.object_name.lower() in object_name.lower():
            score += 10

    # market_name 匹配
    if market_name and vp.market_name:
        if market_name.lower() in vp.market_name.lower():
            score += 8

    # topic_tags 匹配
    tags = _parse_json_list(vp.topic_tags)
    if query:
        import re
        words = [w for w in re.split(r'[\s，。、？！,.!?]+', query) if len(w) >= 2]
        score += sum(5 for tag in tags for w in words if w.lower() in tag.lower())

    # 全文关键词匹配
    score += _keyword_score(
        query,
        vp.thesis,
        vp.supporting_points,
        vp.opposing_points,
        vp.risks,
        vp.action_suggestion,
    )

    # validity_status 权重
    score += _VALIDITY_WEIGHT.get(vp.validity_status or "invalid", 0)

    # user_approval_level 权重
    score += _APPROVAL_WEIGHT.get(vp.user_approval_level or "reference", 1)

    # 新鲜度（最多 +5，180天内线性衰减）
    if vp.updated_at:
        days_old = max(0, (datetime.now() - vp.updated_at).days)
        freshness = max(0.0, 5.0 * (1 - days_old / 180))
        score += freshness

    return score


# ──────────────────────────────────────────────────────────
# 公开检索接口
# ──────────────────────────────────────────────────────────

def retrieve_research_context(
    query: str,
    object_name: Optional[str] = None,
    market_name: Optional[str] = None,
    top_k: int = 5,
    include_inactive: bool = False,
) -> list[dict]:
    """
    从 research_viewpoints 中召回最相关的投研观点。

    MVP 实现：结构化字段 + 关键词匹配 + 综合排序（无 embedding）。
    后续升级为 RAG 时只需替换此函数内部实现，调用接口不变。

    参数：
        query          自然语言查询，如"美团现在适不适合加仓"
        object_name    精确/模糊匹配标的名（可选）
        market_name    市场筛选（可选）
        top_k          最多返回条数
        include_inactive 是否包含 outdated/invalid 状态的观点

    返回：
        list[dict]，每个 dict 包含观点关键字段 + 相关性 score
    """
    session = get_session()
    try:
        q = session.query(ResearchViewpoint)
        if not include_inactive:
            q = q.filter(ResearchViewpoint.validity_status.in_(["active", "watch"]))

        viewpoints = q.all()
        if not viewpoints:
            return []

        scored = [
            (vp, _score_viewpoint(vp, query, object_name, market_name))
            for vp in viewpoints
        ]
        scored.sort(key=lambda x: -x[1])

        results = []
        for vp, score in scored[:top_k]:
            results.append({
                "id":                   vp.id,
                "title":                vp.title,
                "object_name":          vp.object_name,
                "market_name":          vp.market_name,
                "thesis":               vp.thesis,
                "stance":               vp.stance,
                "horizon":              vp.horizon,
                "action_suggestion":    vp.action_suggestion,
                "validity_status":      vp.validity_status,
                "user_approval_level":  vp.user_approval_level,
                "topic_tags":           _parse_json_list(vp.topic_tags),
                "supporting_points":    _parse_json_list(vp.supporting_points),
                "risks":                _parse_json_list(vp.risks),
                "invalidation_conditions": vp.invalidation_conditions,
                "updated_at":           vp.updated_at.strftime("%Y-%m-%d") if vp.updated_at else "",
                "_score":               round(score, 1),
            })
        return results
    finally:
        session.close()
