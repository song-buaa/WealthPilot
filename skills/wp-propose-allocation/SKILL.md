---
name: wp-propose-allocation
description: 为新增资金生成资产配置方案。两种主线：A）capital_amount + 已有持仓 → 增量配置（compute_increment_plan）；B）capital_amount + 无持仓 → 初始配置（compute_initial_plan）。返回 plan_items（每类资产的建议金额和比例）+ 自动跑过的纪律校验结果。
version: 1.0.0
type: function_call
entry_point: backend.graph.tools:call_tool
tool_name: propose_increment_plan
inputs:
  type: object
  properties:
    portfolio_id:
      type: integer
      default: 1
    increment_amount:
      type: number
      description: 新增资金金额（人民币元）
    user_requested_deriv:
      type: boolean
      default: false
      description: 用户是否主动要求配置衍生品
  required: [increment_amount]
outputs:
  type: IncrementPlanOutput
  description: 含 total_amount / allocations / plan_items / discipline_check
tags: [allocation, planning, capital-deployment]
---

# wp-propose-allocation

生成资产配置方案。

## 两种主线

| 主线 | 触发条件 | 内部调用 |
|------|---------|---------|
| 增量配置 | capital_amount > 0 + 已有持仓 | compute_increment_plan |
| 初始配置 | capital_amount > 0 + 无持仓 | compute_initial_plan |
| 比例方向 | capital_amount = None | 不调计算引擎，由 LLM 直接给比例建议 |

## 方案输出

`AllocationResult` 含：
- `total_amount`：总配置金额
- `allocations`：dict[资产类别 → 金额]
- `plan_items`：list[AllocationPlanItem]
  - 每项含：label / current_ratio / target_mid / deviation / suggested_amount / suggested_ratio
- `discipline_check`：自动跑过的纪律校验结果

## 自动纪律对齐

`compute_increment_plan` 内部会自动跑纪律校验，如果初始方案违反纪律（如杠杆 ETF 超限），
会自动调整方案直到通过校验。

## 调用方式

```python
from backend.graph.tools import call_tool

result = call_tool(
    "propose_increment_plan",
    portfolio_id=1,
    increment_amount=1000000,  # 100 万
)
# result.total_amount: float
# result.plan_items: list[PlanItemOut]
# result.summary: str
```

## 上下游关系

- 上游：wp-fetch-holdings + wp-calc-allocation-deviation（隐式）
- 下游：wp-reasoning（AssetAllocation 意图）
