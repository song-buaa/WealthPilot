# WealthPilot Skills 边界评估报告

> 评估目标：在新增 ActionPlanner Skill 前，确认与现有 11 个 Skill 的职责边界
> 日期：2026-05-10
> 基线：WealthPilot v3.1 + v3.2 M1/M2 已完成

---

## 第一部分：现有 11 个 Skill 全景梳理

### Skill 清单

| # | Skill 名称 | SKILL.md 路径 | 核心职责 | 类型 | 输入 | 输出 | 触发方式 |
|---|---|---|---|---|---|---|---|
| 1 | wp-load-context | `skills/wp-load-context/` | **组合 Skill**：加载决策完整上下文（画像+持仓+纪律+投研+target_position） | function_call (composite) | asset_name, portfolio_id, user_query | LoadDecisionContextOutput | 静态 bundle hard-code（所有路由第一步） |
| 2 | wp-fetch-holdings | `skills/wp-fetch-holdings/` | 查询用户全部持仓（聚合后） | function_call | portfolio_id | FetchHoldingsOutput (holdings + total_assets) | 静态 bundle hard-code |
| 3 | wp-fetch-research | `skills/wp-fetch-research/` | 拉取标的投研观点（本地卡+联网+盈米MCP） | function_call | asset_name, portfolio_id | list[str] (带来源标注) | 静态 bundle + LLM Selector 可增补 |
| 4 | wp-check-discipline | `skills/wp-check-discipline/` | 投资纪律校验（单标仓位上限/完整11条） | function_call | asset_name, portfolio_id, action_type | DisciplineCheckOutput | 静态 bundle hard-code |
| 5 | wp-generate-signals | `skills/wp-generate-signals/` | 生成 4 维度市场信号（仓位/基本面/事件/情绪） | function_call | asset_name, portfolio_id, action_type | GenerateSignalsOutput | 静态 bundle hard-code |
| 6 | wp-calc-allocation-deviation | `skills/wp-calc-allocation-deviation/` | 计算资产配置偏离度（5 类资产 vs 目标区间） | function_call | portfolio_id | CalcDeviationOutput | 静态 bundle + LLM Selector 可增补 |
| 7 | wp-propose-allocation | `skills/wp-propose-allocation/` | 生成资产配置方案（增量/初始配置） | function_call | portfolio_id, increment_amount | IncrementPlanOutput | 静态 bundle + LLM Selector 可增补 |
| 8 | wp-reasoning | `skills/wp-reasoning/` | **通用 LLM 推理**：5 种 prompt 模板 × 参数化调用 | llm_dispatch | prompt_template_id, user_query, context | LLMResult / GenericLLMResult | 静态 bundle hard-code（所有路由最后一步） |
| 9 | wp-citation-rules | `skills/wp-citation-rules/` | 输出引用规则（9条数据引用+4档时效性） | prompt_inject | 无（注入到 wp-reasoning） | 规则文本 | 静态 bundle hard-code |
| 10 | wp-output-validator | `skills/wp-output-validator/` | 输出硬校验（决策档位/列表非空/chat_answer长度） | validation | LLMResult, intent_type | ValidationResult | 静态 bundle hard-code |
| 11 | wealthpilot-position-decision | `skills/wealthpilot-position-decision/` | **总入口 Skill**：描述单标决策完整 SOP（5 阶段编排） | SOP 文档 | 无（描述性） | 无（描述性） | 不被代码调用，仅作文档 |

### 问题标注

| 问题类型 | Skill | 说明 |
|---|---|---|
| 颗粒度不统一 | wp-load-context vs wp-fetch-holdings | wp-load-context 是 composite（内部包含 wp-fetch-holdings 的逻辑），但 wp-fetch-holdings 同时存在于静态 bundle 中。实际运行时 ExecutingAgent 调用的是 wp-load-context（invoke_skill），wp-fetch-holdings 在 bundle 中但不被独立调用。**不影响功能，但 bundle 列表有冗余声明。** |
| 边界模糊 | wealthpilot-position-decision | 总入口 Skill 是纯文档，不被任何代码调用，与其他 10 个可执行 Skill 性质不同。**建议标记为 `type: documentation`** |
| 输入输出未完全结构化 | wp-fetch-research | 输出是 `list[str]`（文本列表），不是结构化数据类。v3.1 新增的 research 包含来源标注（`[用户资料]`/`[联网参考]`），但解析逻辑散布在消费端。**不阻塞 ActionPlanner，但未来应结构化。** |

---

## 第二部分：ActionPlanner 设计提案

### 2.1 职责定义

**把投资决策对话的上下文翻译为结构化的行动清单草稿（ActionListDraft），供用户审阅、编辑、确认后进入执行流程。**

### 2.2 输入契约

```python
@dataclass
class ActionPlannerInput:
    conversation_id: str                    # 当前对话 session_id
    conversation_context: list[dict]        # 最近 N 轮对话历史 [{role, content}]
    expressing_output: dict                 # Expressing Agent 最近一次输出
                                           # 含 chat_answer / structured_payload / actionable_hint
```

### 2.3 输出契约

```python
@dataclass
class ActionListDraft:
    conversation_id: str
    decision_summary: str                           # 200 字内的决策依据摘要
    allocation_intents: list[AllocationIntentDraft]  # 资产配置调整意图（可为空）
    symbol_strategies: list[SymbolStrategyDraft]     # 标的策略（可为空）
    risk_notes: list[str]                           # AI 识别的风险提示
    missing_fields: list[str]                       # 需要用户补充的字段

@dataclass
class SymbolStrategyDraft:
    symbol: str
    side: str                                       # BUY / SELL
    quantity: Optional[int]
    quantity_pct: Optional[Decimal]
    order_type: str                                 # LIMIT / CONDITIONAL_LIMIT
    trigger_price: Optional[Decimal]
    limit_price: Optional[Decimal]
    parent_intent_index: Optional[int]              # 关联 allocation_intents 索引

@dataclass
class AllocationIntentDraft:
    title: str
    target_allocation: dict[str, Decimal]           # {"equity": 0.40, ...}
```

### 2.4 触发方式

**用户点击"生成行动清单"按钮时 hard-code 调用，不走 LLM Skill Selector。**

### 2.5 不走 Selector 的理由

1. **避免误触**：ActionPlanner 把对话翻译为下单指令，如果被 LLM Selector 在常规对话中误选，会在用户未预期时生成行动清单，造成困惑甚至误操作风险
2. **降低 token 成本**：ActionPlanner 需要读取完整对话上下文（可能 5000+ token），每轮对话都触发浪费巨大
3. **用户意图明确**：点击按钮是明确的用户意图信号，不需要 LLM 推测"用户是否想生成行动清单"
4. **职责隔离**：Expressing Agent 只做轻量判断（actionable=true/false），重活在用户主动触发后才做

---

## 第三部分：边界冲突分析

### 3.1 逐个 Skill 冲突检查

| 现有 Skill | 与 ActionPlanner 是否有冲突 | 分析 |
|---|---|---|
| wp-load-context | ❌ 无冲突 | wp-load-context 加载持仓/纪律/投研数据；ActionPlanner 读取的是对话上下文，两者数据源完全不同 |
| wp-fetch-holdings | ❌ 无冲突 | 数据获取 vs 行动翻译，层次不同 |
| wp-fetch-research | ❌ 无冲突 | 投研数据获取 vs 行动翻译 |
| wp-check-discipline | ⚠️ 需关注 | ActionPlanner 输出的 risk_notes 可能包含纪律违规提示。**不冲突但需确认**：ActionPlanner 的 risk_notes 是从对话上下文中提取已有结论，不重新调用 wp-check-discipline。即：ActionPlanner 引用 Expressing 已输出的纪律校验结果，不自己算 |
| wp-generate-signals | ❌ 无冲突 | 信号生成 vs 行动翻译 |
| wp-calc-allocation-deviation | ❌ 无冲突 | 偏离度计算 vs 行动翻译 |
| wp-propose-allocation | ⚠️ 需关注 | wp-propose-allocation 生成配置方案（plan_items），ActionPlanner 的 allocation_intents 也描述目标配置。**区别**：wp-propose-allocation 是计算引擎（输入金额→输出方案），ActionPlanner 是翻译器（输入对话→输出草稿）。ActionPlanner 可以引用 wp-propose-allocation 的计算结果，但不重新计算 |
| wp-reasoning | ⚠️ 核心区别 | wp-reasoning 是通用 LLM 推理（生成 chat_answer），ActionPlanner 是专用 LLM 翻译（生成 ActionListDraft）。**两者都调用 LLM 但 prompt 模板不同、输出结构不同**。wp-reasoning 的 5 个 prompt 模板不包含 action_planning。ActionPlanner 应新增第 6 个模板或独立实现 |
| wp-citation-rules | ❌ 无冲突 | 引用规则是 prompt 注入，ActionPlanner 不需要引用规则（输出不是面向用户的 chat_answer） |
| wp-output-validator | ❌ 无冲突 | 输出校验 vs 行动翻译。但**未来可考虑**给 ActionListDraft 也加校验（如 quantity 为空时 missing_fields 必须包含提示） |
| wealthpilot-position-decision | ❌ 无冲突 | 纯文档 Skill |

### 3.2 LLM Skill Selector 误选风险

**当前 LLM Selector 可增补的 Skill 列表**（`_LLM_SELECTABLE_EXTRA_SKILLS`）：
- wp-fetch-research
- wp-propose-allocation
- wp-calc-allocation-deviation

ActionPlanner **不在** `_LLM_SELECTABLE_EXTRA_SKILLS` 中，也不在任何 `_SKILL_BUNDLES_BY_ROUTE` 的静态 bundle 中。

**误选风险：无。** 只要不把 ActionPlanner 加入 `_LLM_SELECTABLE_EXTRA_SKILLS`，LLM Selector 永远不会选到它。

**SKILL.md 描述的措辞建议**：在 description 中明确写 `trigger: manual_button_only`，并在 tags 中加 `manual-trigger`，让未来维护者清楚这不是 Selector 可选的 Skill。

### 3.3 ActionListDraft 与 OrderManager 输入契约对齐

| ActionListDraft 字段 | OrderManager.create_draft 的 payload 字段 | 对齐? |
|---|---|---|
| `conversation_id` | `create_draft(conversation_id=...)` | ✅ 直接映射 |
| `decision_summary` | `create_draft(decision_summary=...)` | ✅ 直接映射 |
| `symbol_strategies[]` | `payload.actions[{type:"symbol", symbol, side, ...}]` | ⚠️ **命名不同但可映射** |
| `allocation_intents[]` | `payload.actions[{type:"allocation", title, ...}]` | ⚠️ **命名不同但可映射** |
| `risk_notes[]` | 不在 payload 中 | ❌ **需新增** — risk_notes 应存入 payload 或 draft 的独立字段 |
| `missing_fields[]` | 不在 payload 中 | ❌ **需新增** — 同上 |

**对齐建议**：
- `payload.actions[]` 的结构改为直接包含 `symbol_strategies` 和 `allocation_intents` 两个数组（而非统一的 `actions` + `type` 判别），减少一层间接
- `risk_notes` 和 `missing_fields` 加到 `payload` 的顶层或 `ActionDraft` 模型的独立字段

---

## 第四部分：Skills 编排逻辑梳理

### 当前 10 个 Skill 的调用编排

```
用户输入
    │
    ▼
PlanningAgent
    │  ① 意图识别 + 路由决策
    │  ② 静态 bundle 选择 Skill 列表
    │  ③ 边界场景时 LLM Selector 增补
    │
    ▼
ExecutingAgent
    │  按 bundle 列表顺序调用：
    │  ┌─ wp-load-context [composite, hard-code]
    │  │    ├── (内含) wp-fetch-holdings 逻辑
    │  │    ├── (内含) wp-fetch-research 逻辑
    │  │    └── (内含) target_position 查找
    │  ├─ wp-check-discipline [hard-code]
    │  ├─ wp-generate-signals [hard-code]
    │  ├─ wp-calc-allocation-deviation [LLM Selector 可增补]
    │  └─ wp-propose-allocation [LLM Selector 可增补]
    │
    ▼
ExpressingAgent
    │  调用 wp-reasoning [hard-code]
    │  注入 wp-citation-rules [prompt_inject, hard-code]
    │
    ▼
ReviewingAgent
    │  调用 wp-output-validator [validation, hard-code]
    │
    ▼
SSE 流式输出 → 用户
```

**Skill 间调用链**：
- wp-load-context 内部包含 wp-fetch-holdings / wp-fetch-research 的逻辑（composite），但这两个原子 Skill 的 entry_point 也可以独立调用
- wp-citation-rules → wp-reasoning：prompt 注入关系（citation-rules 的文本被注入到 reasoning 的 system prompt）
- 其余 Skill 之间无直接调用链

### ActionPlanner 加入后的编排图

```
用户输入
    │
    ▼
PlanningAgent → ExecutingAgent → ExpressingAgent → ReviewingAgent
    │                                      │
    ▼                                      ▼
SSE 流式输出                    输出包含 actionable=true/false
    │                           + actionable_hint
    ▼
用户看到 AI 回复
    │
    │ (用户点击"生成行动清单"按钮)
    ▼
ActionPlanner Skill [manual trigger, 独立调用]
    │  输入：conversation_context + expressing_output
    │  调用 LLM（独立 prompt 模板，不走 wp-reasoning）
    │  输出：ActionListDraft
    │
    ▼
OrderManager.create_draft(ActionListDraft)
    │
    ▼
前端展示行动清单卡片（用户审阅/编辑/确认）
```

**关键变化**：
- ActionPlanner 是 PEER 流程之外的**旁路调用**，不在 Planning→Executing→Expressing→Reviewing 链路中
- 触发点是用户点击按钮，不是 PlanningAgent 路由
- ActionPlanner 不进入 `_SKILL_BUNDLES_BY_ROUTE`，不进入 `_LLM_SELECTABLE_EXTRA_SKILLS`

---

## 第五部分：原子化评估

### ActionPlanner 输出的 5 个部分

1. `decision_summary` — 决策依据摘要
2. `allocation_intents[]` — 资产配置调整意图
3. `symbol_strategies[]` — 标的策略
4. `risk_notes[]` — 风险提示
5. `missing_fields[]` — 缺失字段

### 拆分方案 vs 不拆分方案

| 维度 | 拆成 5 个原子 Skill | 保持合并设计 |
|---|---|---|
| **用户体验** | ❌ 差。用户点一个按钮，后台要串行调 5 个 LLM，延迟 × 5。用户等 10+ 秒不可接受 | ✅ 好。一次 LLM 调用 1-3 秒完成 |
| **Token 成本** | ❌ 高。每个原子 Skill 都需要完整的对话上下文作为输入（重复传入 5 次） | ✅ 低。一次调用一份上下文 |
| **输出一致性** | ❌ 风险。5 个独立 LLM 调用可能产生相互矛盾的输出（如 strategy 说买入，risk_note 说不应买入） | ✅ 好。单次调用内部逻辑一致 |
| **未来扩展性** | ⚠️ 中。新增输出字段需要新建 Skill | ⚠️ 中。新增字段直接扩展 ActionListDraft |
| **调试难度** | ❌ 高。5 个 Skill 各自的 prompt 需要独立维护和调优 | ✅ 低。一个 prompt 模板 |

### 建议：**不拆分，保持合并设计**

理由：
1. ActionPlanner 本质是**一次性翻译任务**（对话 → 结构化草稿），5 个输出字段之间存在语义耦合（决策摘要解释了策略选择的原因，风险提示基于策略内容），拆分会破坏一致性
2. 用户体验要求一次点击快速响应（< 3 秒），串行 5 次 LLM 调用不可接受
3. 现有 wp-reasoning 也是合并设计（一个 Skill 输出 decision + reasoning + risk + strategy + chat_answer 多个字段），ActionPlanner 的设计与之一致

---

## 第六部分：最终建议

### 需要 Songbin 确认的关键决策点

| # | 决策点 | 建议 | 需确认 |
|---|---|---|---|
| 1 | **ActionPlanner 是否走 wp-reasoning 的第 6 个 prompt 模板？** | **建议独立实现**，不复用 wp-reasoning。理由：wp-reasoning 输出 chat_answer（面向用户的 Markdown），ActionPlanner 输出 ActionListDraft（面向 OrderManager 的 JSON）。输出格式完全不同，共用一个 Skill 会让 wp-reasoning 变得臃肿。独立实现也便于后续对 ActionPlanner 的 prompt 做专门调优。 | 是否同意独立实现？ |
| 2 | **ActionListDraft 与 M1 payload 字段命名对齐** | 建议 M3 实施时修改 `confirm_draft` 的 payload 解析逻辑，让它直接消费 ActionListDraft 的结构（`symbol_strategies[]` / `allocation_intents[]`），而非当前的 `actions[{type:...}]` 通用结构。同时在 `action_drafts` 表或 `payload` 中新增 `risk_notes` 和 `missing_fields` 字段。 | 是否同意改造 confirm_draft 的 payload 结构？ |
| 3 | **ActionPlanner 调 LLM 时是否需要加载持仓数据？** | **建议不重新加载**。ActionPlanner 的输入是 `conversation_context`（已包含 Expressing Agent 输出的完整分析），不需要重新调 wp-load-context。如果对话中提到了持仓数据，Expressing 已经引用过了，ActionPlanner 从上下文中提取即可。这样保持 ActionPlanner 的轻量和快速。 | 是否同意不加载持仓数据？ |
| 4 | **Expressing Agent 的 actionable 判断是硬规则还是 LLM 判断？** | PRD 说"由 prompt 实现"（LLM 判断）。建议：v3.2 先用硬规则（对话包含 decisionType 为 buy_init/buy_more/trim/exit 时 actionable=true），v3.3 再升级为 LLM 判断。硬规则更可靠、零成本、无延迟。 | 是否同意 v3.2 用硬规则？ |
| 5 | **ActionPlanner 的 SKILL.md 放在哪？** | PRD 说 `skills/action_planner/SKILL.md`。但实际代码（entry_point）建议放在 `backend/services/action/action_planner.py`（与 OrderManager 同模块）。SKILL.md 放在 `skills/wp-action-planner/SKILL.md`（统一 wp- 前缀）。 | 是否同意 wp-action-planner 命名？ |
| 6 | **ActionPlanner 输出的 missing_fields 如何处理？** | 两种方案：A) 前端弹窗提示用户补充缺失字段后再确认；B) 直接生成草稿，缺失字段标注为 null，用户在编辑页补充。建议方案 B（更简单，v3.2 MVP）。 | A 还是 B？ |
