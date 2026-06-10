"""v3.8.2 manifest 忠实性单测。断言每个意图情况的 executing/expressing 列与 PRD 3.1 一致。"""
from backend.agents.skill_manifest import SKILL_MANIFEST


def _names(items):
    """提取动作列表中的 name 字段。"""
    return [item["name"] for item in items]


def _kinds(items):
    """提取动作列表中的 kind 字段。"""
    return [item["kind"] for item in items]


# ── position_single 主干 ──

def test_position_main_executing():
    m = SKILL_MANIFEST["position_single::PositionDecision::main"]
    names = _names(m["executing"])
    kinds = _kinds(m["executing"])
    assert names == ["wp-load-context", "wp-check-discipline", "wp-generate-signals"]
    assert kinds == ["orchestrator", "skill", "skill"]


def test_position_main_expressing():
    m = SKILL_MANIFEST["position_single::PositionDecision::main"]
    assert _names(m["expressing"]) == ["wp-reasoning"]
    assert m["expressing"][0]["impl"] == "llm_engine.reason"


# ── position_single 新建仓 ──

def test_position_new_entry_executing():
    m = SKILL_MANIFEST["position_single::PositionDecision::new_entry"]
    names = _names(m["executing"])
    kinds = _kinds(m["executing"])
    assert names == ["wp-load-context", "m8-new-entry-analysis", "wp-check-discipline-partial"]
    assert kinds == ["orchestrator", "append_marker", "append_marker"]


def test_position_new_entry_expressing():
    m = SKILL_MANIFEST["position_single::PositionDecision::new_entry"]
    assert m["expressing"][0]["impl"] == "llm_engine.reason_new_entry"


# ── portfolio 三子意图 ──

def test_portfolio_review_executing():
    m = SKILL_MANIFEST["portfolio::PortfolioReview"]
    names = _names(m["executing"])
    assert names == ["wp-load-context", "wp-fetch-research"]
    assert m["executing"][0]["kind"] == "orchestrator"
    assert m["executing"][1]["kind"] == "skill"


def test_portfolio_review_expressing():
    m = SKILL_MANIFEST["portfolio::PortfolioReview"]
    assert m["expressing"][0]["impl"] == "llm_engine.review_portfolio"


def test_portfolio_allocation_executing():
    m = SKILL_MANIFEST["portfolio::AssetAllocation"]
    assert _names(m["executing"]) == ["wp-load-context"]
    assert m["executing"][0]["kind"] == "orchestrator"


def test_portfolio_allocation_expressing():
    m = SKILL_MANIFEST["portfolio::AssetAllocation"]
    assert m["expressing"][0]["impl"] == "llm_engine.analyze_allocation"


def test_portfolio_performance_executing():
    m = SKILL_MANIFEST["portfolio::PerformanceAnalysis"]
    assert _names(m["executing"]) == ["wp-load-context"]


def test_portfolio_performance_expressing():
    m = SKILL_MANIFEST["portfolio::PerformanceAnalysis"]
    assert m["expressing"][0]["impl"] == "llm_engine.analyze_performance"


# ── general ──

def test_general_keyword_hit_executing():
    m = SKILL_MANIFEST["general::keyword_hit"]
    assert _names(m["executing"]) == ["wp-retrieve-principles"]
    assert m["executing"][0]["kind"] == "skill"


def test_general_keyword_hit_expressing():
    m = SKILL_MANIFEST["general::keyword_hit"]
    assert m["expressing"][0]["impl"] == "llm_engine.chat"


def test_general_keyword_miss_executing():
    m = SKILL_MANIFEST["general::keyword_miss"]
    assert m["executing"] == []


def test_general_keyword_miss_expressing():
    m = SKILL_MANIFEST["general::keyword_miss"]
    assert m["expressing"][0]["impl"] == "llm_engine.chat"


# ── clarify / low_confidence ──

def test_clarify_empty():
    m = SKILL_MANIFEST["clarify"]
    assert m["executing"] == []
    assert m["expressing"] == []


def test_low_confidence_empty():
    m = SKILL_MANIFEST["low_confidence"]
    assert m["executing"] == []
    assert m["expressing"] == []


# ── kind 分类全面检查 ──

def test_all_kinds_valid():
    """所有动作项的 kind 必须是 skill/orchestrator/append_marker 之一。"""
    valid_kinds = {"skill", "orchestrator", "append_marker"}
    for key, phases in SKILL_MANIFEST.items():
        for phase in ("executing", "expressing"):
            for item in phases.get(phase, []):
                assert item["kind"] in valid_kinds, \
                    f"{key}.{phase}: {item['name']} has invalid kind={item['kind']}"


def test_orchestrator_only_load_context():
    """orchestrator 类型只能是 wp-load-context。"""
    for key, phases in SKILL_MANIFEST.items():
        for phase in ("executing", "expressing"):
            for item in phases.get(phase, []):
                if item["kind"] == "orchestrator":
                    assert item["name"] == "wp-load-context", \
                        f"{key}.{phase}: orchestrator must be wp-load-context, got {item['name']}"
