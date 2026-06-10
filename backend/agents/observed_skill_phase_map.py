# backend/agents/observed_skill_phase_map.py
#
# Diagnostic-only mapping for v3.8.1 reconciliation.
# 记录每个 wp-* Skill 当前【被实际 invoke 的 PEER 阶段】(依据 v3.8.1 勘探 C5)。
# 注意:这是"观测到的实际归属",不是 SKILL.md 声明 type,
# 也 NOT the final manifest schema —— v3.8.2 设计 manifest 时不得直接沿用本表为权威。

SKILL_PHASE = {
    "wp-load-context":              "executing",
    "wp-fetch-holdings":            "executing",
    "wp-fetch-research":            "executing",
    "wp-retrieve-principles":       "executing",
    "wp-check-discipline":          "executing",
    "wp-generate-signals":          "executing",
    "wp-reasoning":                 "expressing",
    "wp-output-validator":          "reviewing",
    "wp-citation-rules":            "ghost",     # 无生产 invoke 点
    "wp-calc-allocation-deviation": "ghost",     # 无 invoke 点
    "wp-propose-allocation":        "ghost",     # 无 invoke 点
    "wp-action-planner":            "frontend",  # 前端按钮直触,不经 PEER
}

# invoked_skills 中的伪 Skill 名(出现在 invoked_skills 但不对应真 wp- SKILL.md)
# 来源:Step 0 运行核实
#   - 4 个市场数据服务伪名(executing_agent.py L274-277)
#   - 2 个新建仓分支标记名(L421 / L425)
PSEUDO_SKILLS = {
    "wp-fetch-realtime-quote",
    "wp-fetch-fundamentals",
    "wp-fetch-capital-flow",
    "wp-fetch-kline",
    "m8-new-entry-analysis",
    "wp-check-discipline-partial",
}
