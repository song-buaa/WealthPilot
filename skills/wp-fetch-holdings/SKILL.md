---
name: wp-fetch-holdings
description: 查询用户的全部持仓数据，返回聚合后的持仓列表（按品种聚合，包含市值、成本、浮盈亏、占比、平台等完整信息）。适用于需要持仓基础数据的所有决策场景。
version: 1.0.0
type: function_call
entry_point: backend.graph.tools:call_tool
tool_name: fetch_holdings
inputs:
  type: object
  properties:
    portfolio_id:
      type: integer
      description: 用户组合 ID
      default: 1
  required: []
outputs:
  type: FetchHoldingsOutput
  description: 含 holdings 列表（AggregatedPosition）+ total_assets 总资产
tags: [data-fetch, portfolio, holdings]
---

# wp-fetch-holdings

查询用户的全部持仓数据。

## 用途

为决策流程提供基础持仓快照。所有依赖持仓数据的下游 Skill（如 wp-check-discipline /
wp-generate-signals / wp-calc-allocation-deviation）都会消费本 Skill 的输出。

## 实现

底层调用 `app.utils.position_aggregator.aggregate_investment_positions(pid)`，
通过 M2 Tool Layer 的 `fetch_holdings` 包装暴露为 Skill。

聚合规则：
- 同一标的（ticker 相同）跨平台合并为单条记录
- platforms 字段保留所有持仓平台
- weight 按总资产计算（0~1）

## 调用方式

```python
from backend.graph.tools import call_tool

result = call_tool("fetch_holdings", portfolio_id=1)
# result.holdings: list[AggregatedPosition]
# result.total_assets: float
```

## 上下游关系

- 上游：无（数据源 Skill）
- 下游：wp-check-discipline / wp-generate-signals / wp-calc-allocation-deviation
