---
name: wp-load-context
description: 加载决策所需的完整上下文，是 ExecutingAgent 启动时调用的组合 Skill（Composite Skill）。内部封装：用户画像 + 持仓快照 + 纪律配置 + target_position 查找（含 LLM 语义兜底）+ 投研观点（含 M7 三层基金判别）+ 数据告警累积。这是 v3.0 设计中明确的"组合能力"——服务于业务自然边界，不强行拆为多个原子 Skill。
version: 1.0.0
type: function_call
composite: true
entry_point: backend.graph.tools:call_tool
tool_name: load_decision_context
inputs:
  type: object
  properties:
    asset_name:
      type: string
      description: 标的名称（可选，组合级评估时为 null）
    portfolio_id:
      type: integer
      default: 1
    user_query:
      type: string
      description: 用户原始问句（M7 三层判别需要）
      default: ""
  required: []
outputs:
  type: LoadDecisionContextOutput
  description: 含 loaded_data（LoadedData 完整对象）+ 4 个状态标志位
tags: [composite-skill, context-loading, data-fetch, m7]
---

# wp-load-context

加载决策上下文（组合 Skill）。

## 为什么是组合 Skill 而非原子 Skill

WealthPilot 的 9 个原子 Skill 各司单一职责（如 wp-fetch-holdings 只查持仓）。
但决策流程启动时需要一组紧密耦合的数据：

- 持仓数据（决定纪律校验和信号生成的基础）
- 用户画像（决定风险偏好和投资目标）
- 纪律配置（决定决策的硬性约束）
- target_position（用户咨询的具体标的，可能涉及 3 级查找）
- 投研观点（M7 三层判别：基金独占盈米 vs 股票通用搜索）
- 数据告警（装配过程中累积的边界警告）

这些数据**装配逻辑紧密耦合**——比如 target_position 的查找依赖 positions 列表，
M7 判别依赖 target_position 是否在持仓中。如果强行拆成 6 个独立 Skill 调用，
ExecutingAgent 会需要重新实现装配逻辑（即把 data_loader.load() 的 168 行代码
"搬"到 ExecutingAgent 里），破坏 v2.6 已稳定的实现。

因此 wp-load-context 设计为**组合 Skill**（composite=true）——内部封装完整装配，
对外暴露统一接口。

## 内部装配的关键逻辑

### 1. target_position 3 级查找
- Level 1：持仓内精确/模糊匹配（find_target）
- Level 2：LLM 语义解析（_resolve_asset_by_llm，gpt-4.1-mini）
- Level 3：M7 虚拟构造（持仓外基金 + 用户明确询问基金）

### 2. M7 三层判别（research 字段分流）
- Layer 1：持仓内基金 → 用 ticker 调盈米 MCP
- Layer 2：持仓外基金 + 用户明确说"基金" → 用 asset_name 调盈米
- Layer 3：歧义场景 → 走通用联网搜索

### 3. 投研字段独占模式
基金场景（is_likely_fund=True）下，research 字段**独占盈米数据**——不混入通用
联网搜索的噪声（避免"000001 → 平安银行"等误匹配）。

### 4. 数据告警累积
装配过程中如果发生：
- Portfolio 不存在 → warning
- 持仓为空 → error
- total_assets <= 0 → error
- M7 虚拟 target → warning（提示用户该标的不在持仓）

会被累积到 LoadedData.data_warnings。

## 调用方式

```python
from backend.skills import invoke_skill

# 单标决策场景
ctx = invoke_skill(
    "wp-load-context",
    asset_name="茅台",
    portfolio_id=1,
    user_query="茅台还能拿吗",
)
loaded = ctx.loaded_data  # LoadedData 完整对象

# 组合级评估场景（asset_name=None）
ctx = invoke_skill(
    "wp-load-context",
    portfolio_id=1,
    user_query="我组合现在健康吗？",
)
```

## 上下游关系

- 上游：PlanningAgent（提供 asset_name 和 user_query）
- 下游：wp-check-discipline / wp-generate-signals / wp-reasoning（消费 LoadedData）
