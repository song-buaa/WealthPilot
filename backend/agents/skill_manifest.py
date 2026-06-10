# backend/agents/skill_manifest.py
#
# v3.8.2: 忠实描述层。记录每种提问情况下 Executing + Expressing 两阶段的真实动作。
# 【不得用于生成 PlanningOutput.selected_skills】—— 那由 LEGACY 表负责。
#
# 每个动作项结构: {"name": str, "kind": str, "impl": str(可选)}
#   kind: "skill"          —— 真 wp-* Skill(走 invoke_skill 或单独 append)
#         "orchestrator"   —— wp-load-context(编排入口,内部吞多个 service 直连)
#         "append_marker"  —— invoked_skills 里的伪标记,非 Skill 协议对象
#   impl: Expressing 项标注背后实际 LLM 函数


def S(name, impl=None):
    """真 Skill 简写。"""
    return {"name": name, "kind": "skill", **({"impl": impl} if impl else {})}


def ORC():
    """编排入口（wp-load-context）。"""
    return {"name": "wp-load-context", "kind": "orchestrator"}


def MARK(name):
    """伪标记（invoked_skills 中的非 Skill 协议名）。"""
    return {"name": name, "kind": "append_marker"}


def REASON(impl):
    """Expressing 推理（wp-reasoning + 背后 LLM 函数）。"""
    return {"name": "wp-reasoning", "kind": "skill", "impl": impl}


SKILL_MANIFEST = {
    # ── position_single / position_multi ──
    "position_single::PositionDecision::main": {
        "executing":  [ORC(), S("wp-check-discipline"), S("wp-generate-signals")],
        "expressing": [REASON("llm_engine.reason")],
    },
    "position_single::PositionDecision::new_entry": {
        "executing":  [ORC(), MARK("m8-new-entry-analysis"), MARK("wp-check-discipline-partial")],
        "expressing": [REASON("llm_engine.reason_new_entry")],
    },
    # position_multi 同 position_single（decision_service_v3 拆解为多次 single 调用）

    # ── portfolio ──
    "portfolio::PortfolioReview": {
        "executing":  [ORC(), S("wp-fetch-research")],
        "expressing": [REASON("llm_engine.review_portfolio")],
    },
    "portfolio::AssetAllocation": {
        "executing":  [ORC()],
        "expressing": [REASON("llm_engine.analyze_allocation")],
    },
    "portfolio::PerformanceAnalysis": {
        "executing":  [ORC()],
        "expressing": [REASON("llm_engine.analyze_performance")],
    },

    # ── general（不经 load-context，Step 0 核实）──
    "general::keyword_hit": {
        "executing":  [S("wp-retrieve-principles")],
        "expressing": [REASON("llm_engine.chat")],
    },
    "general::keyword_miss": {
        "executing":  [],
        "expressing": [REASON("llm_engine.chat")],
    },

    # ── 不进 ExecutingAgent（decision_service 层短路）──
    "clarify":        {"executing": [], "expressing": []},
    "low_confidence": {"executing": [], "expressing": []},
}
