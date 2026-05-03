---
name: wp-check-discipline
description: 投资纪律校验。决策管道用简化版（仅检查单标仓位上限，毫秒级响应）；用户主动审计场景用完整版（11 条纪律 / 3 引擎，毫秒-百毫秒级）。完整纪律体系来自 data/handbook_official.md（v1.4），由 risk_engine + decision_engine + psychology_engine 三引擎实现。
version: 1.0.0
type: function_call
entry_point: backend.graph.tools:call_tool
tool_name: check_discipline_rules
inputs:
  type: object
  properties:
    asset_name:
      type: string
      description: 标的名称
    portfolio_id:
      type: integer
      default: 1
    action_type:
      type: string
      enum: [BUY, HOLD, SELL, REDUCE, TAKE_PROFIT, STOP_LOSS]
      default: HOLD
  required: [asset_name]
outputs:
  type: DisciplineCheckOutput
  description: 含 violation / warning / current_weight / max_position / rule_details
tags: [discipline, validation, position-limit]
---

# wp-check-discipline

投资纪律校验。

## 简化版 vs 完整版

WealthPilot 的纪律校验有两层：

| 维度 | 简化版（决策管道）| 完整版（独立审计）|
|------|------------------|------------------|
| 实现位置 | decision_engine/rule_engine.py | app/discipline/ 三引擎 |
| 检查范围 | 仅纪律 3（单标仓位上限）| 完整 11 条纪律 |
| 触发场景 | PositionDecision 决策流程 | 投资纪律页面 / 组合体检 |
| 延迟 | <10ms | 50-200ms |
| 设计动机 | 决策响应实时性 | 纪律完整性 |

本 Skill 默认调用简化版。完整版校验通过 `/api/discipline/evaluate` API endpoint 触发。

## 完整 11 条纪律

详见 `references/discipline_overview.md`（与 wealthpilot-position-decision 共享）。
配置源：`data/handbook_official.md` v1.4。

## 调用方式

```python
from backend.graph.tools import call_tool

result = call_tool(
    "check_discipline_rules",
    asset_name="茅台",
    portfolio_id=1,
    action_type="BUY",
)
# result.violation: bool
# result.warning: str
# result.current_weight: float
# result.max_position: float
# result.rule_details: list[str]
```

## 上下游关系

- 上游：wp-fetch-holdings（隐式，rule_engine 内部读持仓）
- 下游：wp-generate-signals（信号需要 rule_result）/ wp-reasoning（决策考虑纪律状态）
