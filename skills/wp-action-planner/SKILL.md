---
name: wp-action-planner
description: 把投资决策对话上下文翻译为结构化行动清单草稿（ActionListDraft）。仅在用户点击"生成行动清单"按钮时调用，不在每轮对话中运行。trigger: manual_button_only。不被 LLM Skill Selector 选择。
version: 1.0.0
type: function_call
entry_point: backend.services.action.action_planner:plan_actions
trigger: manual_button_only
inputs:
  type: ActionPlannerInput
  properties:
    conversation_id:
      type: string
      description: 当前对话 session_id
    conversation_context:
      type: array
      description: 最近 N 轮对话历史 [{role, content}]
    expressing_output:
      type: object
      description: Expressing Agent 最近一次输出（含 decisionType / confidence / recommendedAction）
  required: [conversation_id, conversation_context]
outputs:
  type: ActionListDraft
  description: 结构化行动清单草稿，含 decision_summary / symbol_strategies / allocation_intents / risk_notes / missing_fields
tags: [action-planning, manual-trigger, v3.2]
---

# wp-action-planner

把投资决策对话翻译为结构化行动清单。

## 触发方式

**仅通过用户主动点击"生成行动清单"按钮触发**，不通过 LLM Skill Selector 自动选择。

原因：
- ActionPlanner 把对话翻译为下单指令，误触可能产生安全风险
- 需要读取完整对话上下文（5000+ token），每轮对话都触发浪费 token
- 用户点击按钮是明确的意图信号，不需要 LLM 推测

## 与现有 Skill 的关系

- **不调用** wp-load-context（不重新加载持仓，只消费对话上下文）
- **不复用** wp-reasoning 的 prompt 模板（独立 prompt，输出 JSON 而非 chat_answer）
- **不进入** _SKILL_BUNDLES_BY_ROUTE（PEER 链路外的旁路调用）
- **不进入** _LLM_SELECTABLE_EXTRA_SKILLS（LLM Selector 不可选）

## 输出结构

ActionListDraft 直接对齐 OrderManager.create_draft 的 payload 格式，
无需额外转换即可持久化到 action_drafts 表。
