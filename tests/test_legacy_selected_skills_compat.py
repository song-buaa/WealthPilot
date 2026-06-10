"""v3.8.2 LEGACY 兼容性单测。断言 LEGACY 表 == v3.8.1 旧 bundle 逐字不变。"""
from backend.agents.planning_agent import (
    LEGACY_SELECTED_SKILLS_BY_ROUTE,
    _SKILL_BUNDLES_BY_ROUTE,
    _select_skills_for_route,
)


# v3.8.1 旧 bundle 完整快照（逐字复制，作为 ground truth）
V381_BUNDLE = {
    "position_single": [
        "wp-fetch-holdings", "wp-fetch-research", "wp-retrieve-principles",
        "wp-check-discipline", "wp-generate-signals",
        "wp-reasoning", "wp-citation-rules", "wp-output-validator",
    ],
    "position_multi": [
        "wp-fetch-holdings", "wp-fetch-research", "wp-retrieve-principles",
        "wp-check-discipline", "wp-generate-signals",
        "wp-reasoning", "wp-citation-rules", "wp-output-validator",
    ],
    "portfolio": [
        "wp-fetch-holdings", "wp-fetch-research", "wp-retrieve-principles",
        "wp-calc-allocation-deviation", "wp-propose-allocation",
        "wp-reasoning", "wp-citation-rules", "wp-output-validator",
    ],
    "general": [
        "wp-retrieve-principles", "wp-reasoning",
    ],
    "clarify": [],
    "low_confidence": [],
}


def test_legacy_routes_match_v381():
    """LEGACY 表的路由集合与 v3.8.1 完全一致。"""
    assert set(LEGACY_SELECTED_SKILLS_BY_ROUTE.keys()) == set(V381_BUNDLE.keys())


def test_legacy_content_matches_v381_per_route():
    """LEGACY 表每个路由的 Skill 列表与 v3.8.1 逐元素一致（顺序也一致）。"""
    for route, expected_skills in V381_BUNDLE.items():
        actual_skills = LEGACY_SELECTED_SKILLS_BY_ROUTE[route]
        assert actual_skills == expected_skills, \
            f"route={route}: expected={expected_skills}, actual={actual_skills}"


def test_deprecated_alias_is_same_object():
    """deprecated alias _SKILL_BUNDLES_BY_ROUTE 指向同一个 dict。"""
    assert _SKILL_BUNDLES_BY_ROUTE is LEGACY_SELECTED_SKILLS_BY_ROUTE


def test_select_skills_for_route_unchanged():
    """_select_skills_for_route 输出与 v3.8.1 一致。"""
    for route, expected in V381_BUNDLE.items():
        assert _select_skills_for_route(route) == expected, \
            f"_select_skills_for_route('{route}') mismatch"


def test_select_skills_unknown_route():
    """未知路由返回空列表。"""
    assert _select_skills_for_route("nonexistent_route") == []
