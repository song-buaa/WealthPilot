---
name: wp-fetch-research
description: 拉取标的相关的投研观点，包含本地投研卡（用户审核的高优先级数据）+ 联网搜索（公开市场数据）+ 盈米基金诊断（基金类标的的权威数据）。M7 阶段实现了三层判别逻辑：持仓内基金 / 持仓外用户明确说基金 / 歧义场景。
version: 1.0.0
type: function_call
entry_point: backend.graph.tools:call_tool
tool_name: query_viewpoint_cards
related_tools: [fetch_realtime_research, fetch_fund_diagnosis, search_financial_news]
inputs:
  type: object
  properties:
    asset_name:
      type: string
      description: 标的名称或代码
    portfolio_id:
      type: integer
      description: 用户组合 ID
      default: 1
  required: [asset_name]
outputs:
  type: list[str]
  description: 投研观点文本列表，按 [用户资料]/[第三方数据]/[联网参考] 来源标注
tags: [data-fetch, research, fund, mcp]
---

# wp-fetch-research

拉取标的相关的投研观点。

## 用途

为 LLM 推理提供数据依据。下游 Skill（wp-reasoning）会基于这些观点生成投资建议。

## 数据源优先级

1. **本地投研卡**（query_viewpoint_cards）：用户审核过的观点，标注 `[用户资料]`
2. **联网搜索**（fetch_realtime_research）：公开新闻、研报，标注 `[第三方数据]` 或 `[联网参考]`
3. **盈米 MCP 基金诊断**（fetch_fund_diagnosis）：基金类标的的权威数据
4. **市场新闻**（search_financial_news）：补充宏观资讯

## M7 三层判别逻辑

调用方根据用户问句和持仓状况自动选择数据源：

| 场景 | 判定 | 数据源 |
|------|------|--------|
| Layer 1：标的在持仓中且是基金 | 优先用盈米 MCP | 盈米诊断 + 业绩指标 |
| Layer 2：持仓外标的 + 用户明确说"基金" | 触发盈米 MCP | 盈米诊断（独占模式） |
| Layer 3：歧义场景 | 用通用联网搜索 | fetch_realtime_research |

## 调用方式

```python
from backend.graph.tools import call_tool

# 本地投研卡
result = call_tool("query_viewpoint_cards", asset_name="茅台", portfolio_id=1)

# 联网搜索（用于股票或歧义场景）
result = call_tool("fetch_realtime_research", asset_name="茅台")

# 盈米基金诊断（M7 三层判别命中时）
result = call_tool("diagnose_fund", fund_name_or_code="000001")
```

## 上下游关系

- 上游：wp-fetch-holdings（判断 Layer 1/2 时需要持仓信息）
- 下游：wp-reasoning（投研观点作为 LLM 推理依据）
