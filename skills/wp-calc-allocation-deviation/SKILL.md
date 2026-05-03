---
name: wp-calc-allocation-deviation
description: 计算用户当前组合各资产类别（权益/固收/另类/衍生品/货币）相对目标区间的偏离度。返回结构化 DeviationSnapshot，含每类资产的 current_pct / target_range / deviation 等。
version: 1.0.0
type: function_call
entry_point: backend.graph.tools:call_tool
tool_name: calc_allocation_deviation
inputs:
  type: object
  properties:
    portfolio_id:
      type: integer
      default: 1
  required: []
outputs:
  type: CalcDeviationOutput
  description: 含 by_class（5 类资产偏离度）/ cash / overall_status / priority_action
tags: [allocation, deviation, calculation]
---

# wp-calc-allocation-deviation

计算资产配置偏离度。

## 用途

服务于 PortfolioReview 和 AssetAllocation 两个意图：
- PortfolioReview：作为组合健康度评估的核心指标
- AssetAllocation：判断当前组合是否需要再平衡

## 数学定义

对每类资产：
deviation = current_pct - target_mid_pct
where target_mid_pct = (target_floor + target_ceiling) / 2

正值表示超配，负值表示欠配。

## 资产类别和目标区间

来自 `data/handbook_official.md` 的 `asset_allocation_ranges`：

| 类别 | 目标区间 |
|------|---------|
| 权益 | 40%~80% |
| 固收 | 20%~60% |
| 另类 | ≤ 10% |
| 衍生品 | ≤ 10% |
| 货币 | 10,000~100,000 元（绝对值）|

## 调用方式

```python
from backend.graph.tools import call_tool

result = call_tool("calc_allocation_deviation", portfolio_id=1)
# result.by_class: list[ClassDeviationItem]
# result.summary: str
```

## 上下游关系

- 上游：wp-fetch-holdings（隐式）
- 下游：wp-reasoning（PortfolioReview / AssetAllocation 意图）
