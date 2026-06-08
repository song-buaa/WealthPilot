---
name: wp-generate-execution-plan
description: 从投资决策建议生成确定性分批执行计划草案。规则引擎产出所有数字(触发价/数量/批次),LLM 只写解释文案。type=function_call(确定性 orchestrator,非 llm_dispatch)。
version: 1.0.0
type: function_call
tool_name: generate_execution_plan
trigger: manual_button_only
inputs:
  type: object
  properties:
    symbol:
      type: string
      description: "标的代码 TICKER:MARKET (如 LI:US / 00700:HK)"
    market:
      type: string
      description: "US / HK"
    side:
      type: string
      description: "BUY / ADD / REDUCE / SELL"
    target_position_pct:
      type: number
      description: "目标仓位占比 (0~1)"
    current_position_pct:
      type: number
    current_price:
      type: number
    total_assets:
      type: number
    user_anchor_prices:
      type: array
      description: "用户锚点价(可空)"
    quick_mode:
      type: boolean
    source_decision_ref:
      type: string
  required: [symbol, market, side, target_position_pct]
outputs:
  type: ExecutionPlanDraft
  description: 含 plan_summary_block(权威数字) + rationale/risk_notes(AI 文案) + factor_snapshot + constraints_applied
tags: [execution-plan, manual-trigger, deterministic, v3.11]
---

# wp-generate-execution-plan

从投资决策建议生成确定性分批执行计划草案。

## 内核原则

1. **数字由规则引擎算死,AI 只解释。** 价格/数量/批次全部确定性产出。
2. **约束派生自 13 条纪律手册,不新建。** 直接读 `app/discipline/config.py`。
3. **born-activated。** 第一个 commit 起接入真实调用链。

## 调用方式

```python
from backend.skills import invoke_skill
result = invoke_skill("wp-generate-execution-plan",
    symbol="LI:US", market="US", side="BUY",
    target_position_pct=0.08,
    current_price=14.2, total_assets=1000000,
)
```

## 内部执行顺序(固定,不可调换)

1. `factors.py` → FactorSnapshot(含 data_source_meta)
2. `rule_engine.py` → 权威 plan dict(所有数字在此步定死)
3. LLM → 接收已定死 plan dict,只写 rationale / risk_notes
4. validator → 校验 plan_summary_block 数值一致(M4)

## 与 wp-action-planner 的区别

| | wp-action-planner | wp-generate-execution-plan |
|---|---|---|
| 数字来源 | LLM 产出(不可信) | 规则引擎(确定性) |
| type | function_call | function_call |
| AI 角色 | 翻译对话为行动 | 只写解释文案 |
