"""
WealthPilot Risk & Decision Engine
基于《投资纪律手册 v1.2》实现的三层纪律执行引擎

模块结构：
    config.py           — 规则参数（直接来自手册 JSON 块，禁止修改）
    models.py           — 数据结构定义
    risk_engine.py      — 硬性约束执行层（HARD_RULE）
    psychology_engine.py — 行为约束层（情绪/冷却）
    decision_engine.py  — 策略判断层（SOFT_RULE）
    engine_runner.py    — 统一入口 evaluate_action()
"""
