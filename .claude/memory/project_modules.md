---
name: WealthPilot 模块清单与文件结构
description: 各功能模块的关键文件路径、实现状态和入口说明
type: project
---

## 已实现模块

| 模块 | 文件 | 状态 |
|------|------|------|
| 投资账户总览 | app_pages/overview.py | ✅ 完整（iframe 双层方案，最复杂 ~1400 行） |
| 投资纪律 | app_pages/discipline.py | ✅ 完整（~1600 行） |
| 投研观点 | app_pages/research.py | ✅ v1.9.1 稳定（四 Tab 页） |
| 投资决策 | app_pages/strategy.py | ✅ V3.2（多轮对话 + Explain Panel） |
| 养老规划 | app_pages/retirement_life.py | ✅ 简单实现 |

## 决策引擎包（decision_engine/）

intent_parser.py → data_loader.py → pre_check.py → rule_engine.py → signal_engine.py → llm_engine.py → decision_flow.py

FlowStage 状态机：INTENT→LOADED→PRE_CHECK→RULE_CHECK→SIGNAL→LLM→DONE/ABORTED

## 核心业务层（app/）

models.py（ORM：Portfolio/Position/Liability/ResearchDocument/ResearchCard/ResearchViewpoint）
analyzer.py（BalanceSheet 计算 + 偏离告警）
discipline/（投资纪律引擎，含 risk_engine.py / psychology_engine.py）
ai_advisor.py（LLM 集成：分析报告 + 研报解析）
research.py（多因子评分检索引擎）

## 占位页面（9 个，显示开发中）

用户画像和投资目标、新增资产配置、投资记录、收益分析、生活账户总览、购房规划、消费规划、个人资产负债总览、家族资产负债总览

**Why：** 这 9 个页面路由到 placeholder.py，是产品规划的未来功能。
