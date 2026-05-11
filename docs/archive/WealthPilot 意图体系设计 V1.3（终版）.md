# 📄 WealthPilot 意图体系设计 V1.3（终版）

---

## 一、设计目标

我们的目标是为 **智能投顾系统** 设计一个稳健的 **意图体系**，能够通过 **自然语言** 实现用户的投资决策，并通过 **Orchestrator** 进行高效的决策编排与执行。系统支持 **多轮对话**、**多意图识别** 和 **灵活的输出结构**，适应复杂的投资场景。

---

## 二、架构升级

### 1. **四层结构**（Intent + Subtask + Action + Context）

```
用户输入 → 意图识别（LLM） → Orchestrator（编排） → 工具调用 → 输出生成
```

- **Intent（主意图）**: 决定用户请求的核心目标（分析或行动）
    
- **Subtask（子任务/模式）**: 子任务拆解，支持复合意图处理
    
- **Action（行为信号）**: 具体的操作意图（如买入、卖出、调仓）
    
- **Context（上下文状态）**: 支持多轮对话的上下文管理，确保决策一致性
    

---

## 三、Intent 定义（5 大类）

### 1️⃣ **Portfolio Review（组合评估）**

**定义**: 对当前整体组合进行分析、评估或是否需要调整。

**触发条件**:

- 涉及多个标的
    
- 涉及整体仓位、风险、集中度
    

**输出**:

- 组合结构
    
- 风险分析
    
- 偏离情况
    
- 是否需要调整
    

---

### 2️⃣ **Asset Allocation（资产配置）**

**定义**: 针对新增资金或长期规划进行配置设计。

**触发条件**:

- 明确资金规模（如“20万怎么投？”）
    
- 配置、分配、比例等关键词
    

**输出**:

- 资产配置方案
    
- 配置逻辑
    
- 风险说明
    

---

### 3️⃣ **Position Decision（单标的决策）**

**定义**: 对某一个标的是否操作或持有做判断。

**触发条件**:

- 单一标的
    
- 出现“买入/卖出/加仓/减仓”等表达
    

**输出**:

- 标的判断
    
- 风险评估
    
- 操作建议（加仓/减仓等）
    

---

### 4️⃣ **Performance Analysis（收益分析）**

**定义**: 对收益来源、亏损原因进行分析。

**触发条件**:

- 盈亏、回撤、收益等关键词
    
- 无明确交易动作
    

**输出**:

- 收益拆解
    
- 亏损原因分析
    
- 归因分析
    

---

### 5️⃣ **Education / General（投教/通用）**

**定义**: 非个性化决策类问题。

**触发条件**:

- 投资知识性问题
    
- 非投资决策类
    

---

## 四、Subtask / Mode（子任务/模式）

每个 Intent 可以拆解为多个 **Subtask**（子任务），用于处理复合意图的多维度需求。

### Portfolio Review

- **Subtasks**:
    
    - review（整体评估）
        
    - risk_check（风险检查）
        
    - concentration_check（集中度检查）
        
    - rebalance_check（是否需要再平衡）
        

### Asset Allocation

- **Subtasks**:
    
    - new_cash_allocation（新增资金配置）
        
    - rebalance_allocation（再平衡配置）
        
    - goal_based_allocation（目标导向配置）
        

### Position Decision

- **Subtasks**:
    
    - thesis_review（逻辑判断）
        
    - position_fit_check（组合适配）
        
    - action_evaluation（操作评估）
        

### Performance Analysis

- **Subtasks**:
    
    - pnl_breakdown（收益拆解）
        
    - loss_reason（亏损原因分析）
        
    - attribution（收益归因分析）
        

### Education

- **Subtasks**:
    
    - concept_explain（概念解释）
        
    - rule_explain（规则解释）
        

---

## 五、Action 定义（行为信号）

**行为信号**用于标记用户的操作意图，影响系统的决策路径。

### 交易类动作

- **BUY**: 买入
    
- **SELL**: 卖出
    
- **ADD**: 加仓
    
- **REDUCE**: 减仓
    
- **REBALANCE**: 调仓
    
- **TAKE_PROFIT**: 止盈
    
- **STOP_LOSS**: 止损
    

### 信息类动作（非交易性）

- **ANALYZE**: 仅分析，无操作
    
- **VIEW_PERFORMANCE**: 查看表现
    
- **GET_REPORT**: 获取报告
    
- **SET_ALERT**: 设置提醒
    

**Action 的作用**：

1. **分析路径**：确定分析的维度（如风险、收益、集中度等）
    
2. **决策路径**：影响后续的操作决定（如是否买入/卖出）
    
3. **输出结构**：根据 Action 调整输出的内容和结构
    

---

## 六、LLM 意图识别（语义理解）

所有的用户输入将通过 **LLM** 进行意图识别，返回一个标准化的 **JSON 结构**，包括：

```json
{
  "primary_intent": "PositionDecision",
  "secondary_intents": ["PerformanceAnalysis"],
  "subtasks": ["loss_reason", "action_evaluation"],
  "actions": ["SELL"],
  "entities": {
    "asset": "理想汽车"
  },
  "confidence": 0.87
}
```

### LLM 的作用：

- **识别主意图**：判断用户请求的核心目标
    
- **识别子意图**：处理复合意图（如分析 + 决策）
    
- **抽取实体**：识别标的、资金等关键信息
    

---

## 七、Context（状态管理器）

Context 用于管理多轮对话中的上下文信息，确保系统的决策一致性和连贯性。

### Context 结构：

```json
{
  "last_intent": "AssetAllocation",
  "current_intent": "PortfolioReview",
  "entities": {
    "asset": "理想汽车",
    "capital": "20万"
  },
  "conversation_stage": "Rebalancing",
  "user_profile": {
    "risk_level": "中等",
    "goal": "长期增值"
  }
}
```

### Context 管理规则：

- **短期（1轮）**：当前意图、当前 Action
    
- **中期（多轮）**：标的、组合、资金等
    
- **长期（全局）**：风险偏好、投资目标、用户画像
    

---

## 八、Orchestrator（决策编排器）

Orchestrator 是决定 **如何执行用户请求** 的核心组件。

### 编排流程：

1. **LLM 识别**：识别 Intent、Action 和 Entities
    
2. **Context 补全**：根据上下文决定继承或重置
    
3. **Subtask 执行**：根据 Action 调用相应的分析子任务
    
4. **输出聚合**：根据 Action 和 Subtask 决定最终输出内容
    

### 多意图处理（Primary + Secondary）

```json
{
  "primary_intent": "PositionDecision",
  "secondary_intents": ["PerformanceAnalysis"],
  "subtasks": ["loss_reason", "action_evaluation"],
  "actions": ["SELL"]
}
```

### 输出结构：根据 **Primary Intent** 输出结果，Secondary Intent 用于补充信息。

---

## 九、补充优化建议

### 1. **实体标准化**

- 对 **实体**（如资产、资金、目标）进行标准化，方便后续调用金融数据库 API。
    

### 2. **Context 继承与重置规则**

- 定义哪些字段在多轮中继承，哪些需要重置。
    
- 确保多轮对话的一致性。
    

### 3. **Subtask 依赖关系**

- 明确哪些 Subtask 是顺序执行，哪些是并行执行。
    

### 4. **风险合规的硬隔离**

- 在决策类意图与投教类意图之间增加合规层，避免未经授权的决策建议。

# 十、补充信息

## 1️⃣ **Context 字段生命周期表**

每个字段的 **继承条件** 和 **重置条件**，可以直接作为工程的标准。

|字段|继承条件|重置条件|
|---|---|---|
|capital|同一对话主题内|用户明确新金额时|
|asset|Intent为PositionDecision时|Intent切换到Portfolio/Allocation时|
|portfolio|当前组合未变时|用户明确要求查看或修改组合时|
|risk_level|用户的风险偏好不变|用户明确调整风险承受度时|
|goal|目标不发生变化|用户修改投资目标或时间跨度时|

**说明：**

- **短期**：如同一对话内的操作，`capital` 和 `asset` 会继承
    
- **中期**：如 `portfolio` 和 `risk_level` 会跨轮继承，直到发生关键变化
    
- **长期**：如 `goal` 一旦设置，只有用户明确修改时才会重置
    

---

## 2️⃣ **Secondary Intent 执行规则**

明确 **Secondary Intent** 的执行深度，以及如何影响主意图的输出。

### 执行深度

1. **分析级输出（摘要级）**
    
    - **定义**：只提取次要信息，做摘要或重要性提示。
        
    - **适用场景**：涉及 **解释性问题**，如收益分析、风评分析等。
        
    - **输出规则**：只传递简洁的辅助信息，不再执行复杂的流程。
        
2. **完整执行**
    
    - **定义**：在主意图未能完全解答时，执行 Secondary Intent 的 **完整流程**。
        
    - **适用场景**：涉及 **决策性问题**，如是否调整组合、是否买卖标的等。
        
    - **输出规则**：执行完整的子任务流程，然后输出多维度的分析与建议。
        

---

### 融合规则（如何整合Secondary到Primary的输出）

- **Primary Intent的输出**为最终决策依据，**Secondary Intent的输出**只是补充，提供背景信息。
    
- **顺序执行**：Secondary Intent 完成后，将其输出的数据注入 Primary Intent 的相应位置，确保主输出逻辑不变。
    
- **并行输出**：如果多个Secondary Intent并行，必须明确它们的优先级，并进行合并输出。
    