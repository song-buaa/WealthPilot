
---

# 一、版本目标（不变）

升级为：

> ✅ 多轮对话投资决策助手  
> ✅ Chat + Explain Panel  
> ✅ 单一规则数据源（投资纪律）

---

# 二、核心原则（新增，必须写清）

---

## 原则1：一轮输入 = 一次完整决策

每次用户输入（无论是否追问）  
→ 必须走完整流程：  
intent → data → rule → signal → LLM  
→ 生成新的 decision_id

---

## 原则2：Explain Panel 永远绑定 decision

每条 AI 回复 → 对应一个 decision_id  
点击该回复 → 右侧展示该 decision

---

## 原则3：上下文用于“理解”，不改变流程

上下文仅用于：  
- 补全 asset  
- 补全 action  
  
不影响是否执行完整决策流程

---

# 三、多轮对话设计（核心补强）

---

## 3.1 意图解析（升级版）⭐重点

---

### 输出结构（新增字段）

JSON{  
  "asset": "理想汽车",  
  "action_type": "加仓判断",  
  "time_horizon": "短期",  
  "intent_type": "investment_decision",  
  "is_context_inherited": true,  
  "confidence_score": 0.85  
}

---

## 3.2 意图继承逻辑（必须实现）

---

### Case 1：完整输入

“理想汽车要不要加仓？”  
→ 正常解析

---

### Case 2：追问（缺信息）

“那蔚来呢？”

系统行为：

Python运行if missing(action_type):  
    action_type = last_intent.action_type  
  
if missing(time_horizon):  
    time_horizon = last_intent.time_horizon

---

## 3.3 意图类型分类（新增）

---

intent_type:  
- investment_decision  
- general_chat

---

### 行为

Python运行if intent_type == "general_chat":  
    → 只调用 LLM  
    → 不生成 decision_id

---

# 四、决策流程（明确不变）

---

用户输入  
→ intent_parser（含上下文）  
→ data_loader  
→ rule_engine  
→ signal_engine  
→ llm_engine  
→ decision_output

---

# 五、Chat vs LLM 输出关系（关键补充）

---

## 5.1 LLM底层输出（不变）

JSON{  
  "decision": "HOLD",  
  "reasoning": [],  
  "risk": [],  
  "strategy": []  
}

---

## 5.2 Chat展示（新增说明）

【结论】观望  
【原因】...  
【建议】...

---

## ❗规则

Chat内容 = LLM JSON 的渲染结果  
不是新的生成

---

# 六、Explain Panel 逻辑（补清）

---

## 6.1 绑定机制

Python运行decision_map = {  
  "decision_001": {...},  
  "decision_002": {...}  
}  
  
message → decision_id

---

## 6.2 点击行为

Python运行on_click(message):  
    current_decision_id = message.decision_id

---

## 6.3 非决策情况（新增）

---

若 intent_type == general_chat：  
  
Explain Panel 显示：  
“当前对话非投资决策，无决策链路”

---

# 七、状态管理（补全结构）

---

## 7.1 conversation_history

Python运行[  
  {  
    "message_id": "msg_001",  
    "role": "user",  
    "content": "...",  
    "intent": {...}  
  },  
  {  
    "message_id": "msg_002",  
    "role": "assistant",  
    "content": "...",  
    "decision_id": "decision_001"  
  }  
]

---

## 7.2 decision_map

Python运行{  
  "decision_001": {  
    "intent": {...},  
    "data": {...},  
    "rule_check": {...},  
    "signals": {...},  
    "llm_output": {...}  
  }  
}

---

## 7.3 current_decision_id

Python运行"decision_002"

---

# 八、上下文策略（明确）

---

## 传给LLM的context

Python运行context = [  
  last_user_input,  
  last_ai_response  
]

---

## ❗限制

最多保留最近1轮

---

# 九、假设性问题处理（MVP决策）⭐重要

---

## ❌ 不支持假设推演

---

### 示例

“如果我减仓一半再加仓呢？”

---

### 系统行为

提示：  
请先修改真实持仓后再进行决策

---

## 原因

避免引入虚拟持仓复杂度

---

# 十、UI交互补充（重要）

---

## Chat消息新增按钮

[查看决策逻辑 📊]

---

👉 点击后：

* 右侧高亮
* 自动切换对应decision

---

# 十一、数据源统一（保留）

---

## ❌ 删除

策略设定 Tab

---

## ✅ 唯一来源

投资纪律

---

# 十二、异常处理补充

---

1. intent解析失败 → 澄清问题  
2. LLM失败 → “系统繁忙”  
3. 无规则数据 → 不进入决策

---

# 十三、验收标准（升级）

---

## 功能

* 支持 ≥3轮对话
* 每轮独立decision
* Explain正确切换

---

## 体验

* 追问不丢上下文
* 逻辑一致
* 无数据冲突

---

# 🔥 最后一段（非常重要）

你现在这版 V3.1 已经具备：

> ✅ 产品形态（你设计的）  
> ✅ 工程可执行性（Claude要求的）  
> ✅ 系统一致性（Manus要求的）

## 🔧 补充1：last_intent来源（必须写死）

👉 在【意图识别】章节中新增：

Markdown### last_intent 获取规则  
  
last_intent 必须从 conversation_history 中获取：  
  
- 取最近一条 role = user 的历史消息  
- 使用该消息在解析时生成的 intent 字段  
- 若不存在历史，则 last_intent = None  
  
⚠️ 注意：  
- 不从 context 中读取  
- context 仅用于展示与轻量记忆，不参与意图继承

---

## 🔧 补充2：general_chat Prompt隔离

👉 在【流程路由】或【LLM调用策略】中新增：

Markdown### general_chat 模式下的LLM策略  
  
当 intent_type = general_chat 时：  
  
- 不进入决策流程  
- 不使用投资决策 Prompt  
- 使用普通对话 Prompt（无结构化输出要求）  
  
示例：  
  
System Prompt：  
你是一个友好的聊天助手，可以回答用户的日常问题，但不需要输出结构化投资结论。  
  
⚠️ 禁止输出：  
- 【结论】  
- 【原因】  
- 【建议】

---

## 🔧 补充3：3轮测试脚本

👉 在【验收标准】中新增：

Markdown### 标准测试脚本（必须通过）  
  
#### Case 1：意图继承测试  
  
第1轮：  
用户：理想汽车要不要加仓？  
→ intent_type = decision  
→ action = buy  
→ target = 理想汽车  
  
第2轮：  
用户：那蔚来呢？  
→ 自动继承 action = buy  
→ target = 蔚来  
  
---  
  
#### Case 2：假设性问题拦截  
  
第3轮：  
用户：如果我减仓一半呢？  
  
→ intent_type = hypothetical  
→ 系统响应：  
  
当前系统暂不支持假设性推演（如“如果...会怎样”）。  
请提供明确的操作意图，例如：  
- 我要不要减仓理想汽车？  
- 现在适合加仓吗？
