---
name: wp-generate-signals
description: 为标的生成 4 维度市场信号（仓位 / 基本面 / 事件 / 情绪），作为 LLM 决策的环境因子。每个维度输出枚举值（如基本面：正面/中性/负面/N/A），不做精确数值预测。
version: 1.0.0
type: function_call
entry_point: backend.graph.tools:call_tool
tool_name: generate_signals
inputs:
  type: object
  properties:
    asset_name:
      type: string
    portfolio_id:
      type: integer
      default: 1
    action_type:
      type: string
      default: 持有评估
  required: [asset_name]
outputs:
  type: GenerateSignalsOutput
  description: 含 position_signal / fundamental_signal / sentiment_signal / event_*
tags: [signal, market-context, decision-support]
---

# wp-generate-signals

生成 4 维度市场信号。

## 4 维度定义

| 维度 | 取值 | 含义 |
|------|------|------|
| 仓位信号（position）| 偏高 / 合理 / 偏低 | 当前标的占组合比例的健康度 |
| 基本面信号（fundamental）| 正面 / 中性 / 负面 / N/A | 公司/产品的最新业绩与估值 |
| 事件信号（event）| uncertainty + direction | 是否存在重大突发事件 |
| 情绪信号（sentiment）| 中性（MVP 固定）| 市场对该标的的整体情绪 |

注：情绪信号当前 MVP 阶段固定为"中性"，未来通过聚合社交情绪/新闻情绪做精细化。

## 内部依赖

本 Skill 内部串行调用：
1. `data_loader.load(asset_name, pid)` 加载持仓 + 投研
2. 构造 IntentResult（基于 action_type 参数）
3. `rule_engine.check(loaded, intent)` 计算仓位信号
4. `signal_engine.generate(loaded, intent, rule_result)` 综合 4 维度

调用方只需传简单参数（asset_name + portfolio_id + action_type），
不需要自己组装 LoadedData / IntentResult / RuleResult 等复杂对象。

## 调用方式

```python
from backend.graph.tools import call_tool

result = call_tool(
    "generate_signals",
    asset_name="茅台",
    portfolio_id=1,
    action_type="持有评估",
)
# result.position_signal: str
# result.fundamental_signal: str
# result.event_uncertainty: str
# result.event_direction: str
# result.sentiment_signal: str
```

## 上下游关系

- 上游：wp-fetch-holdings + wp-fetch-research + wp-check-discipline（隐式）
- 下游：wp-reasoning（信号作为 LLM 推理的环境因子）
