---
name: wp-output-validator
description: 输出硬校验 Skill。对 LLM 输出做确定性检查（不调 LLM）：决策档位枚举、列表非空、激进决策与纪律违规冲突、chat_answer 长度等。校验失败返回 ValidationResult 含失败规则列表，调用方决定 retry 或 fallback。
version: 1.0.0
type: validation
entry_point: backend.graph.decision_validator:validate_decision_output
inputs:
  type: object
  properties:
    result:
      type: LLMResult or GenericLLMResult
    intent_type:
      type: string
    discipline_violations:
      type: list
  required: [result, intent_type]
outputs:
  type: ValidationResult
  description: 含 passed / action / failures（list[ValidationFailure]）
tags: [validation, output-quality, deterministic-check]
---

# wp-output-validator

输出硬校验。

## 检查项

| 检查 | 失败规则名 | 严重度 |
|------|----------|--------|
| chat_answer 非空 | chat_answer_empty | hard |
| chat_answer >= 20 字 | chat_answer_too_short | hard |
| is_fallback = False | is_fallback | hard |
| decision 在 6 档枚举内 | decision_invalid | hard |
| reasoning 列表非空 | reasoning_empty | hard |
| risk 列表非空 | risk_empty | hard |
| 纪律严重违规 + 激进决策冲突 | discipline_conflict | hard |
| confidence < 0.5 时 infoNeeded 非空 | low_confidence_no_info_needed | soft |

## 校验流程

1. 通用层（所有意图）：检查 is_fallback / chat_answer 非空 / 长度
2. PositionDecision 专项层：检查 decision 枚举 / reasoning / risk / 纪律一致性

任一 hard 规则失败 → `passed=False`，返回失败列表。

## action 判定

- 无 hard failure → `action="pass"`
- is_fallback → `action="fallback"`（LLM 本身出错，重试没意义）
- 其他 hard failure → `action="retry"`

## 与 ReviewingAgent 的关系

ReviewingAgent 内部第一层调用本 Skill 做硬校验：
- 硬校验通过 → action=pass（毫秒级，覆盖 99% 场景）
- 硬校验失败 + 重试次数未达上限 → 调 LLM 评分（Layer 2）
- 重试次数达上限 → action=fallback（降级 HOLD）

## 调用方式

```python
from backend.graph.decision_validator import validate_decision_output

result = validate_decision_output(
    result=llm_result,
    intent_type="PositionDecision",
)
# result.passed: bool
# result.action: str (pass/retry/fallback)
# result.failures: list[ValidationFailure]
```

## 上下游关系

- 上游：wp-reasoning（消费它的 LLMResult 输出）
- 下游：ReviewingAgent（基于 ValidationResult 决定 action）
