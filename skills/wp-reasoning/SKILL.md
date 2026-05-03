---
name: wp-reasoning
description: 通用 LLM 推理 Skill，根据 prompt_template_id 参数加载对应 prompt 模板，调用 GPT-4.1 生成结构化决策。支持 5 种 prompt 模板（position_decision / portfolio_review / asset_allocation / performance_analysis / general_chat）对应 5 种意图。这是 v3.0 设计的核心创新——把"每意图一个推理 Skill"统一为"通用推理 Skill + 参数化 prompt 模板"。
version: 1.0.0
type: llm_dispatch
entry_point: decision_engine.llm_engine
prompt_templates_dir: prompts/
inputs:
  type: object
  properties:
    prompt_template_id:
      type: string
      enum: [position_decision, portfolio_review, asset_allocation, performance_analysis, general_chat]
    user_query:
      type: string
    context:
      type: object
      description: ExecutionContext 对象，含 loaded_data / rule_result / signal_result 等
  required: [prompt_template_id, user_query, context]
outputs:
  type: LLMResult or GenericLLMResult
  description: 含 chat_answer / structured_payload / decision / reasoning / risk
tags: [llm, reasoning, prompt-template]
---

# wp-reasoning

通用 LLM 推理能力。

## 设计哲学

v3.0 之前的设计是"每个意图一个推理 Skill"（5 个 Skill），但 5 个 Skill 之间的差异
仅在 prompt 内容，而不是"推理逻辑"——它们都是"调 LLM 拿结构化输出"。

v3.0 重新设计为：
- **1 个通用推理 Skill**（wp-reasoning）
- **5 个独立 prompt 模板**（prompts/*.md）

通过 `prompt_template_id` 参数选择模板。这种设计：
1. Skill 数量正确反映"原子能力数量"（推理是 1 个能力）
2. prompt 作为独立数据资产管理，可以版本化和 A/B 测试
3. 新增意图不需要新增 Skill，只需要加 prompt 模板

## prompt 模板映射

| prompt_template_id | LLM engine 函数 | 模型 |
|-------------------|----------------|------|
| position_decision | reason() | gpt-4.1, 4096 tokens |
| portfolio_review | review_portfolio() | gpt-4.1, 2048 tokens |
| asset_allocation | analyze_allocation() | gpt-4.1, 2048 tokens |
| performance_analysis | analyze_performance() | gpt-4.1, 2048 tokens |
| general_chat | chat() | gpt-4.1-mini, 512 tokens |

## prompt 资产保护

5 个 prompt 模板是 case 驱动迭代的产品资产（在 `decision_engine/llm_engine.py` 里
作为常量字符串存储）。v3.0 阶段保持这种存储方式不变，避免改动 prompt 文本。

v3.1 演进：把 prompt 抽到 `prompts/*.md` 独立文件，wp-reasoning 通过 prompt_template_id 加载。

## 调用方式

```python
# v3.0 当前阶段：通过 ExpressingAgent 调用，不直接 invoke
# v3.1 演进后：
from backend.skills import invoke_skill

result = invoke_skill(
    "wp-reasoning",
    prompt_template_id="position_decision",
    user_query="茅台还能拿吗",
    context=execution_output,
)
```

## 上下游关系

- 上游：所有数据获取/计算分析类 Skills 的输出（聚合到 ExecutionContext）
- 下游：wp-citation-rules（注入引用规则）/ wp-output-validator（校验输出）
