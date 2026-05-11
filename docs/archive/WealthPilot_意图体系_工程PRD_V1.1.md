# WealthPilot 意图体系 工程PRD（V1.0）

> 本文档基于业务设计文档 V1.3 改写，面向工程开发视角。  
> 业务逻辑以 V1.3 为准，本文档补充技术决策、接口定义、异常处理与执行策略。

---

## 一、系统概述

### 1.1 系统目标

将用户自然语言输入转化为可执行的投资决策流程，输出结构化的分析结果或操作建议。

### 1.2 整体数据流

```
用户输入（自然语言）
    ↓
[IntentRecognizer] — LLM调用 #1
    ↓ 输出标准化 IntentPayload（JSON）
[ContextManager] — 合并历史上下文
    ↓ 输出完整 ExecutionContext
[Orchestrator] — 生成执行计划
    ↓ 输出 ExecutionPlan（有序 Subtask 列表）
[SubtaskRunner] — 按计划执行子任务（含 LLM 调用）
    ↓ 输出各 Subtask 结果
[OutputRenderer] — 聚合输出
    ↓
最终响应（流式 or 结构化 JSON）
```

### 1.3 模块一览

| 模块 | 职责 | LLM调用 |
|------|------|---------|
| IntentRecognizer | 语义解析，输出 IntentPayload | 是（单次） |
| ContextManager | 多轮上下文继承与重置 | 否 |
| Orchestrator | 生成执行计划，决定 Subtask 顺序与深度 | 否 |
| SubtaskRunner | 执行单个 Subtask，含数据拉取与 LLM 分析 | 是（每 Subtask 一次） |
| OutputRenderer | 聚合 Subtask 结果，按模板生成最终输出 | 是（单次，最终整合） |

---

## 二、数据结构定义

### 2.1 IntentPayload（意图识别输出）

```typescript
interface IntentPayload {
  primary_intent: IntentType;           // 主意图（唯一）
  secondary_intents: IntentType[];      // 次意图（最多2个）
  subtasks: SubtaskType[];              // 激活的子任务列表
  actions: ActionType[];                // 行为信号（可多个）
  entities: {
    asset?: string;                     // 标的名称（原始文本）
    asset_normalized?: string;          // 标准化后的标的代码/ID
    capital?: string;                   // 资金规模（原始文本，如"20万"）
    capital_amount?: number;            // 标准化后的数值（单位：元）
    portfolio_id?: string;              // 组合ID（已登录用户）
    time_horizon?: string;              // 投资期限
  };
  confidence: number;                   // 0~1，识别置信度
}
```

**IntentType 枚举：**

```typescript
type IntentType =
  | "PortfolioReview"
  | "AssetAllocation"
  | "PositionDecision"
  | "PerformanceAnalysis"
  | "Education";
```

**ActionType 枚举：**

```typescript
// 交易类
type TradeAction = "BUY" | "SELL" | "ADD" | "REDUCE" | "REBALANCE" | "TAKE_PROFIT" | "STOP_LOSS";
// 信息类
type InfoAction = "ANALYZE" | "VIEW_PERFORMANCE" | "GET_REPORT" | "SET_ALERT";
type ActionType = TradeAction | InfoAction;
```

**SubtaskType 枚举（按 Intent 分组）：**

```typescript
// PortfolioReview
type PortfolioSubtask = "review" | "risk_check" | "concentration_check" | "rebalance_check";
// AssetAllocation
type AllocationSubtask = "new_cash_allocation" | "rebalance_allocation" | "goal_based_allocation";
// PositionDecision
type PositionSubtask = "thesis_review" | "position_fit_check" | "action_evaluation";
// PerformanceAnalysis
type PerformanceSubtask = "pnl_breakdown" | "loss_reason" | "attribution";
// Education
type EducationSubtask = "concept_explain" | "rule_explain";

type SubtaskType =
  | PortfolioSubtask
  | AllocationSubtask
  | PositionSubtask
  | PerformanceSubtask
  | EducationSubtask;
```

---

### 2.2 ExecutionContext（执行上下文）

```typescript
interface ExecutionContext {
  session_id: string;
  turn_index: number;                   // 当前是第几轮对话
  intent_payload: IntentPayload;        // 本轮识别结果
  inherited_fields: {                   // 从上下文继承的字段
    asset?: string;
    asset_normalized?: string;
    capital?: number;
    portfolio_id?: string;
    risk_level?: "低" | "中" | "高";
    goal?: string;
    time_horizon?: string;
  };
  user_profile: {                       // 长期用户画像（从用户系统读取）
    risk_level: "低" | "中" | "高";
    goal: string;
    verified: boolean;                  // KYC 是否通过
  };
  conversation_history: Turn[];         // 最近 N 轮对话摘要
}

interface Turn {
  turn_index: number;
  intent: IntentType;
  entities_snapshot: Record<string, string>;
  summary: string;                      // 本轮对话摘要（用于注入后续 prompt）
}
```

---

### 2.3 ExecutionPlan（执行计划）

```typescript
interface ExecutionPlan {
  primary_flow: SubtaskExecution[];     // 主意图的 Subtask 执行列表
  secondary_flow: SubtaskExecution[];   // 次意图的 Subtask 执行列表（摘要级）
  execution_mode: "sequential" | "parallel";
}

interface SubtaskExecution {
  subtask: SubtaskType;
  intent_source: "primary" | "secondary";
  execution_depth: "full" | "summary";  // full=完整执行，summary=摘要级
  depends_on: SubtaskType[];            // 依赖的前置 Subtask
  data_requirements: DataRequirement[]; // 需要拉取的数据
}

interface DataRequirement {
  type: "market_data" | "portfolio_data" | "user_profile" | "news";
  params: Record<string, string>;
}
```

---

### 2.4 SubtaskResult（子任务结果）

```typescript
interface SubtaskResult {
  subtask: SubtaskType;
  status: "success" | "failed" | "skipped";
  content: string;                      // LLM 输出的分析文本
  structured_data?: Record<string, unknown>; // 可选的结构化数据
  error?: string;
}
```

---

## 三、模块详细设计

### 3.1 IntentRecognizer

#### 职责
接收用户自然语言输入，调用 LLM 输出标准化 IntentPayload。

#### LLM 调用规格

- **调用次数**：1次
- **模型**：`claude-sonnet-4-20250514`（或同级别模型）
- **输出格式**：强制 JSON，不含 markdown 包裹
- **超时**：10s
- **重试**：最多2次（JSON 解析失败或格式校验失败时重试）

#### System Prompt 骨架

```
你是一个投资意图识别系统。你的唯一任务是将用户输入解析为标准 JSON 结构。

# 意图定义
[此处注入 Intent 定义，见业务文档第三章]

# 输出格式要求
- 必须输出合法 JSON，不含任何解释文字
- primary_intent 必须且只能有1个
- secondary_intents 最多2个，可为空数组
- confidence 范围 0~1
- 所有字段必须存在

# 输出示例
{ "primary_intent": "PositionDecision", ... }
```

#### 输出校验规则

1. JSON 格式合法
2. `primary_intent` 是合法的 IntentType 枚举值
3. `confidence` 在 0~1 之间
4. `subtasks` 中的每个值都属于 `primary_intent` 对应的合法 SubtaskType

校验失败 → 重试（最多2次）→ 仍失败 → 触发兜底策略（见第五章）

#### 实体标准化

识别完成后，对以下字段做标准化处理。**职责分工：LLM 只负责从自然语言中提取原始文本，标准化由代码层完成，不依赖 LLM 推断。**

| 原始字段 | 标准化方式 | 执行层 | 标准化字段 |
|----------|------------|--------|------------|
| `asset`（如"理想汽车"） | 调用 `SymbolSearchAPI`，模糊匹配返回股票代码 | 代码层（LLM 识别后） | `asset_normalized`（如"002594.SZ"） |
| `capital`（如"20万"） | 中文数字解析库（本地，无需 API） | 代码层（LLM 识别后） | `capital_amount`（200000） |

**`SymbolSearchAPI` 调用规则：**
- 输入：`asset` 原始文本
- 输出：匹配度最高的1条结果（股票代码 + 股票全名 + 交易所）
- 匹配度 < 阈值（建议0.8）时视为识别失败
- 标准化失败不阻断流程，`asset_normalized` 留空，后续 Subtask 按降级策略处理

---

### 3.2 ContextManager

#### 职责
合并本轮 IntentPayload 与历史上下文，生成完整 ExecutionContext。

#### 字段生命周期规则

| 字段 | 继承条件 | 重置条件 | 存活范围 |
|------|----------|----------|----------|
| `asset` | primary_intent 为 PositionDecision | Intent 切换至 PortfolioReview 或 AssetAllocation | 中期（多轮） |
| `capital` | 同一会话主题内未出现新金额 | 用户输入新资金规模时 | 中期（多轮） |
| `portfolio_id` | 当前组合未被用户修改 | 用户明确修改组合结构时 | 中期（多轮） |
| `risk_level` | 用户风险偏好未变更 | 用户明确调整风险承受度时 | 长期（全局） |
| `goal` | 目标未发生变化 | 用户修改投资目标或时间跨度时 | 长期（全局） |
| `time_horizon` | 同 `goal` | 同 `goal` | 长期（全局） |

**特别说明：** `portfolio_id` 的"查看"操作（PortfolioReview）**不触发重置**，仅"修改组合结构"触发重置。

#### Intent 切换时的继承逻辑

```
if 本轮 primary_intent == 上一轮 primary_intent:
    全部字段尝试继承

elif Intent 发生切换:
    按上表逐字段判断是否继承
    本轮 IntentPayload 中已有的字段优先，不被继承值覆盖
```

#### 会话历史维护

- 保留最近 **5轮** 对话摘要注入 prompt
- 每轮结束后，将本轮 intent + entities + 输出摘要写入 `conversation_history`

---

### 3.3 Orchestrator

#### 职责
根据 ExecutionContext 生成 ExecutionPlan，决定 Subtask 的执行顺序、深度与并行策略。

#### Secondary Intent 执行深度判断规则

执行深度由 **Secondary Intent 对应的 Action 类型** 决定：

| Secondary Intent 的 Action 类型 | 执行深度 | 含义 |
|----------------------------------|----------|------|
| 信息类（ANALYZE、VIEW_PERFORMANCE 等）| `summary` | 只输出摘要，不执行完整 Subtask 链 |
| 交易类（BUY、SELL、ADD 等）| `full` | 执行完整 Subtask 链 |
| 无 Action（空数组）| `summary` | 默认摘要级 |

Secondary Intent 的执行结果注入 Primary Intent 的**第一个 Subtask 之前**，作为背景信息传入。

#### Subtask 执行顺序与依赖关系

**PositionDecision（含 Secondary PerformanceAnalysis）示例：**

```
[secondary] loss_reason（summary）
    ↓ 输出注入背景
[primary] thesis_review
    ↓
[primary] position_fit_check
    ↓
[primary] action_evaluation
    ↓
OutputRenderer
```

**PortfolioReview 完整执行顺序：**

```
review（并行）+ risk_check（并行）+ concentration_check（并行）
    ↓ Promise.all 等待全部完成
rebalance_check（依赖以上三个，全部成功后启动）
    ↓
OutputRenderer
```

**并行执行同步规则：**
- 并行 Subtask 使用 `Promise.all` 等待全部完成后，再触发依赖任务
- 若并行组中**任意一个** Subtask 失败（status = `failed`），依赖它的下游 Subtask 标记为 `skipped`，不中断整体流程
- 若并行组**全部**失败，OutputRenderer 跳过对应章节，注明"该部分分析暂时不可用"

**AssetAllocation：**

```
new_cash_allocation / rebalance_allocation / goal_based_allocation（三选一，互斥）
    ↓
OutputRenderer
```

#### Secondary Intent 数量限制

- 最多处理 **2个** Secondary Intent
- 超过2个时取 confidence 最高的2个
- 两个 Secondary Intent 均为摘要级时，并行执行

---

### 3.4 SubtaskRunner

#### 职责
按 ExecutionPlan 执行每个 Subtask，包括数据拉取和 LLM 分析调用。

#### 每个 Subtask 的执行步骤

```
1. 拉取所需数据（DataRequirement 列表）
2. 构建 Subtask Prompt（注入数据 + 上下文）
3. 调用 LLM（单次）
4. 返回 SubtaskResult
```

#### LLM 调用规格

- **调用次数**：每 Subtask 1次（full 深度）；摘要级 Subtask 合并为1次
- **模型**：同 IntentRecognizer
- **输出格式**：自然语言文本（非 JSON）
- **超时**：15s
- **重试**：最多1次

#### 各 Subtask 的数据依赖

| Subtask | 需要的数据 |
|---------|------------|
| thesis_review | 标的基本面数据、近期新闻 |
| position_fit_check | 用户持仓数据、标的数据 |
| action_evaluation | thesis_review + position_fit_check 的输出 |
| risk_check | 持仓数据、波动率数据 |
| concentration_check | 持仓数据、行业分布 |
| rebalance_check | risk_check + concentration_check 的输出 |
| pnl_breakdown | 持仓历史收益数据 |
| loss_reason | pnl_breakdown + 市场数据 |
| new_cash_allocation | 用户风险偏好、资金规模、市场宏观数据 |

#### 数据不可用时的降级策略

| 场景 | 处理方式 |
|------|----------|
| 标的信息查不到（识别失败） | 跳过数据拉取，LLM 基于通识分析，输出加免责声明 |
| 持仓数据不存在（未登录） | 跳过持仓相关 Subtask，输出提示用户登录 |
| 市场数据接口超时 | 使用缓存数据（容忍1小时内的陈旧数据），输出加时效说明 |

---

### 3.5 OutputRenderer

#### 职责
聚合所有 SubtaskResult，按输出模板生成最终响应。

#### LLM 调用规格

- **调用次数**：1次（整合 Subtask 结果为连贯的最终输出）
- **输出格式**：自然语言（面向用户）
- **流式输出**：支持 SSE 流式推送

#### 各 Intent 输出模板

**PositionDecision：**
```
1. 当前情况概述（含 Secondary Intent 的亏损分析，若有）
2. 核心逻辑判断（投资逻辑是否仍成立）
3. 风险评估
4. 操作建议（BUY / SELL / ADD / REDUCE / HOLD）
5. 风险提示（合规）
```

**PortfolioReview：**
```
1. 组合结构分析
2. 风险与集中度情况
3. 偏离目标情况
4. 是否需要调整
5. 调整方向建议（若需要）
```

**AssetAllocation：**
```
1. 目标与约束说明
2. 配置原则
3. 资产分配方案
4. 风险说明
```

**PerformanceAnalysis：**
```
1. 收益总览
2. 关键驱动因素
3. 亏损/波动来源
4. 改进建议
```

**Education：**
```
1. 概念/规则解释
2. 结合用户场景的示例（如有）
```

#### Action 对输出结构的影响

| Action | 输出调整 |
|--------|----------|
| SELL / STOP_LOSS | 操作建议章节强调风险，合规提示前置 |
| BUY / ADD | 输出结构中增加"买入理由"小节 |
| REBALANCE | 输出具体的调仓方向建议 |
| TAKE_PROFIT | 输出"止盈逻辑"与"持续持有"两种路径对比 |
| ANALYZE（信息类） | 不输出操作建议章节，仅输出分析 |

---

## 四、Intent 优先级与分流规则

### 4.1 优先级

```
PortfolioReview > AssetAllocation > PositionDecision > PerformanceAnalysis > Education
```

### 4.2 分流逻辑（IntentRecognizer 的分类依据）

```python
if 涉及多个标的 or 涉及组合整体:
    primary_intent = PortfolioReview
elif 涉及资金规模配置:
    primary_intent = AssetAllocation
elif 涉及单一标的:
    primary_intent = PositionDecision
elif 涉及盈亏/收益/回撤（无交易动作）:
    primary_intent = PerformanceAnalysis
else:
    primary_intent = Education
```

> 以上逻辑写入 IntentRecognizer 的 System Prompt，不在代码层做硬判断。LLM 负责语义理解，代码只做格式校验。

---

## 五、异常处理与兜底策略

### 5.1 置信度低于阈值

| confidence 范围 | 处理策略 |
|-----------------|----------|
| ≥ 0.75 | 正常执行 |
| 0.5 ~ 0.74 | 执行，但在输出前追加澄清问题 |
| < 0.5 | 不执行，直接向用户返回澄清问题 |

**澄清问题生成规则：**
- 根据当前识别到的 Intent 候选，生成1个封闭式问题
- 示例："你是想了解理想汽车这个股票本身，还是想看它在你组合里的情况？"

### 5.2 IntentRecognizer 连续失败

```
重试2次后仍失败 → 默认路由至 Education Intent
→ 输出："我没能完全理解你的问题，你可以换一种方式描述，或者告诉我你想分析哪个标的/组合？"
```

### 5.3 Subtask 执行失败

```
单个 Subtask 失败 → 标记为 skipped，继续执行后续 Subtask
OutputRenderer 阶段感知到 skipped Subtask → 在对应输出章节注明"该部分分析暂时不可用"
```

### 5.4 合规拦截

以下情况触发合规拦截，不进入 Orchestrator 执行：

- 用户未完成 KYC（`user_profile.verified = false`）且 Intent 为交易类（含 Action: BUY/SELL/ADD/REDUCE）
- 输出建议涉及场外交易、杠杆产品等需要额外资质的场景

拦截后响应：
```
"根据合规要求，该功能需要完成风险评估后才能使用。[跳转风险评估]"
```

> 投教类 Intent（Education）不受合规拦截影响，始终可用。

---

## 六、接口定义

### 6.1 主入口

```
POST /api/v1/chat
```

**Request：**
```json
{
  "session_id": "string",
  "user_id": "string",
  "message": "string",
  "turn_index": 0
}
```

**Response（SSE 流式）：**
```
event: intent
data: { "primary_intent": "PositionDecision", "confidence": 0.87 }

event: subtask_start
data: { "subtask": "thesis_review" }

event: subtask_result
data: { "subtask": "thesis_review", "status": "success" }

event: output_start
data: {}

event: output_section_start
data: { "section_index": 1, "section_title": "当前情况概述" }

event: output_chunk
data: { "text": "理想汽车..." }

event: output_section_start
data: { "section_index": 2, "section_title": "核心逻辑判断" }

event: output_chunk
data: { "text": "..." }

event: output_done
data: { "clarification_question": null }
```

**SSE 推送规则：**
- `output_chunk` 按**字符级**流式推送，前端实现打字机效果
- `output_section_start` 在每个新章节开始前推送，携带章节序号与标题，供前端渲染章节标题或进度指示
- `subtask_result` 只推送状态（success/skipped），不推送 Subtask 的完整内容（内容由 OutputRenderer 整合后统一推送）
- `clarification_question` 仅在 confidence 处于 0.5~0.74 区间时非空

### 6.2 上下文查询（调试用）

```
GET /api/v1/session/{session_id}/context
```

---

## 七、开发优先级建议

### Phase 1（MVP）

- [ ] IntentRecognizer（含 JSON 校验 + 重试）
- [ ] ContextManager（字段继承规则）
- [ ] Orchestrator（单意图，无 Secondary Intent）
- [ ] SubtaskRunner（PositionDecision 全链路）
- [ ] OutputRenderer（PositionDecision 输出模板）

### Phase 2

- [ ] 多意图支持（Secondary Intent + 执行深度判断）
- [ ] PortfolioReview 全链路
- [ ] AssetAllocation 全链路
- [ ] 置信度澄清机制

### Phase 3

- [ ] PerformanceAnalysis 全链路
- [ ] 合规拦截层
- [ ] 实体标准化（股票代码映射）
- [ ] Education 兜底完善

---

## 八、待解决项（V2）

| 编号 | 问题 | 优先级 |
|------|------|--------|
| V2-01 | 实体标准化：股票名称→代码的映射库建设 | 高 |
| V2-02 | 合规层细化：不同产品类型的合规规则拆分 | 高 |
| V2-03 | Subtask 并行执行的结果聚合策略细化 | 中 |
| V2-04 | 用户画像动态更新（从对话中学习风险偏好变化） | 中 |
| V2-05 | 意图识别的 few-shot 样本库建设 | 低 |

| V2-06 | Prompt 模板版本管理：存储方式（数据库/配置文件）与热更新机制 | 中 |

---

*文档版本：V1.1 | 基于业务文档 V1.3 | 本版更新：实体标准化流程、并行同步机制、SSE帧结构*
