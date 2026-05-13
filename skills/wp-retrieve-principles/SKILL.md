---
name: wp-retrieve-principles
description: >
  从 WealthPilot 知识库中语义检索用户原则类知识。覆盖三类内容：
  投资纪律（investment_principles）、投资风格（investment_style）、
  资产配置原则（allocation_principles）。
  适用于需要"用户原则视角"的决策场景。
  注意：本 Skill 不负责标的相关投研观点（那是 wp-fetch-research 的职责）。
version: 1.0.0
type: function_call
entry_point: backend.graph.tools:call_tool
tool_name: retrieve_principles
inputs:
  type: object
  properties:
    query:
      type: string
      description: 检索 query，通常是用户的原始问题
    source_types:
      type: array
      description: 检索哪些知识类型，默认全部三类
      default: [investment_principles, investment_style, allocation_principles]
    top_k:
      type: integer
      description: 返回结果数量
      default: 5
  required: [query]
outputs:
  type: object
  description: >
    chunks: 检索到的知识片段列表，每条含 content / source_type /
    source_channel / semantic_score / parent_doc_path / date / metadata。
    total_retrieved: 检索结果总数。
tags: [knowledge-retrieval, rag, principles, chroma]
---

# wp-retrieve-principles

从知识库中语义检索用户原则类知识。

## 能力说明

"principles"是广义概念，覆盖以下三类知识：

| source_type | 含义 | 典型内容 |
|-------------|------|---------|
| `investment_principles` | 投资纪律 | 硬性约束 + 定性偏好（如"港股回调时分批建仓"） |
| `investment_style` | 投资风格 | 价值主张与红线（如"长期主义、不加杠杆"） |
| `allocation_principles` | 资产配置原则 | 系统预置的配置方法论 |

## 使用场景

- 单标决策：纪律约束 + 风格偏好 + 配置原则
- 组合评审：配置原则 + 纪律约束
- 教育/原则咨询：仅配置原则

## 不使用场景

- 标的相关投研观点（归 `wp-fetch-research`）
- 联网搜索 / 盈米 MCP 数据（归 `wp-fetch-research`）

## 上下游关系

- **上游**：wp-load-context（内部调用）、ExecutingAgent._execute_general()
- **下游**：ExpressingAgent（注入 LLM prompt）、wp-citation-rules（引用标注）
