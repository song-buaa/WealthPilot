# 📄 WealthPilot PRD V2（工程可执行版）

## 模块：投资决策引擎（Investment Decision Engine）

---

# 一、模块目标

构建一个：

> ✅ **可解释 + 可约束 + 可复用的投资决策引擎**

通过结构化流程，将用户输入转化为投资建议，避免黑盒决策。

---

# 二、系统流程（固定执行链路）

用户输入  
→ 意图解析（Intent Parser）  
→ 信息检索（Data Loader）  
→ 前置校验（Pre-check）  
→ 规则校验（Rule Engine）  
→ 信号生成（Signal Engine）  
→ LLM推理（LLM Engine）  
→ 结果展示（UI）

---

# 三、模块设计（可直接开发）

---

# 3.1 用户输入

### 输入格式

自然语言输入（string）

---

# 3.2 意图解析模块（intent_parser.py）

---

## 输出结构（必须返回）

JSON{  
  "asset": "理想汽车",  
  "action_type": "加仓判断",  
  "time_horizon": "短期",  
  "trigger": "发布会",  
  "confidence_score": 0.85  
}

---

## 规则

- confidence_score < 0.6：  
    → 不进入后续流程  
    → 返回澄清问题  
  
示例：  
“你是想了解理想汽车是否值得加仓，还是查看它的行情？”

---

# 3.3 数据加载模块（data_loader.py）

---

## 数据来源（MVP阶段）

- 用户画像：mock JSON  
- 持仓数据：mock JSON / SQLite  
- 投资纪律：本地配置  
- 投研观点：本地列表

---

## 数据结构（统一返回）

JSON{  
  "profile": {  
    "risk_level": "中高",  
    "goal": "长期增值"  
  },  
  "position": {  
    "li_auto": {  
      "weight": 0.20  
    }  
  },  
  "rules": {  
    "max_single_position": 0.25  
  },  
  "research": [  
    "看好产品周期",  
    "短期销量承压"  
  ]  
}

---

# 3.4 前置校验模块（pre_check）

---

## 规则

若以下任一缺失：  
  
- 用户画像  
- 投资纪律  
- 持仓数据  
  
→ 不进入决策流程  
→ 返回提示：  
  
“请先完善您的投资画像/持仓信息后再进行决策”

---

# 3.5 规则校验模块（rule_engine.py）

---

## 功能

执行**硬规则校验**

---

## 示例规则

Python运行position_ratio = current_position / max_position  
  
if position_ratio >= 1:  
    violation = True  
elif position_ratio >= 0.8:  
    warning = "接近上限"

---

## 输出

JSON{  
  "position_ratio": 0.8,  
  "warning": "接近上限",  
  "violation": false  
}

---

# 3.6 信号层模块（signal_engine.py）⭐核心

---

## 信号维度（固定）

---

### 1️⃣ 仓位信号（position_signal）

position_ratio = 当前仓位 / 上限  
  
≥ 0.8 → 偏高  
0.4 ~ 0.8 → 合理  
≤ 0.4 → 偏低

---

### 2️⃣ 事件信号（event_signal）

字段：  
  
- uncertainty：高 / 中 / 低  
- direction：利好 / 中性 / 利空  
  
规则（MVP简化）：  
  
若存在 trigger：  
    uncertainty = 高  
    direction = 中性（默认）

---

### 3️⃣ 基本面信号（fundamental_signal）

基于投研观点关键词：  
  
包含“看好 / 增长” → 正面  
包含“风险 / 下滑” → 负面  
否则 → 中性

---

### 4️⃣ 情绪信号（sentiment_signal）

MVP阶段：  
  
默认 = 中性

---

## 输出结构

JSON{  
  "position_signal": "偏高",  
  "event_signal": {  
    "uncertainty": "高",  
    "direction": "中性"  
  },  
  "fundamental_signal": "正面",  
  "sentiment_signal": "中性"  
}

---

# 3.7 LLM模块（llm_engine.py）

---

## 使用模型

Anthropic Claude  
模型：claude-sonnet-4-20250514

---

## System Prompt（必须固定）

你是一个专业的投资决策助手。  
  
你需要基于：  
- 用户持仓情况  
- 投资纪律  
- 投研观点  
- 信号层分析结果  
  
提供理性、克制、可解释的投资建议。  
  
要求：  
  
1. 不得使用绝对性表达（如“必须买入”）  
2. 必须给出理由  
3. 必须提示风险  
4. 输出语言为中文  
5. 风格类似投顾报告，简洁理性

---

## 输入（拼接）

JSON{  
  "user_query": "...",  
  "signals": {...},  
  "rules": {...},  
  "research": [...]  
}

---

## 输出格式（强约束）

JSON{  
  "decision": "BUY / HOLD / SELL",  
  "reasoning": [],  
  "risk": [],  
  "strategy": []  
}

---

## UI映射

BUY → 加仓  
HOLD → 观望  
SELL → 减仓

---

# 3.8 异常处理（必须实现）

---

## 规则

1. 标的不识别：  
→ 提示用户确认  
  
2. 无投研观点：  
→ fundamental_signal = "N/A"  
  
3. 数据缺失：  
→ 默认建议 = HOLD（观望）

---

# 3.9 UI展示（Streamlit）

---

## 页面结构

---

### ① 输入区

* 输入框
* 历史记录

---

### ② 意图解析

标的：理想汽车  
操作：加仓判断  
置信度：0.85

---

### ③ 数据展示

* 用户画像
* 持仓
* 投研观点

---

### ④ 规则校验

⚠ 当前仓位接近上限

---

### ⑤ 信号层（重点）

仓位：偏高 ⚠  
事件：不确定性高（中性）  
基本面：正面  
情绪：中性

---

### ⑥ AI推理（可折叠）

---

### ⑦ 最终结论

结论：观望  
  
策略：  
- 回调后加仓  
- 不建议当前追高

---

### ⑧ 合规提示（必须）

本系统输出仅供参考，不构成投资建议，投资有风险，入市需谨慎。

---

# 四、第一版范围（严格控制）

---

## ✅ 必做

* 完整流程跑通
* Signal Layer规则实现
* Claude API调用
* UI展示

---

## ❌ 不做

* 联网搜索
* 用户反馈写库
* 多轮对话
* 自动交易

---

# 五、代码结构（建议）

/decision_engine  
    ├── intent_parser.py  
    ├── data_loader.py  
    ├── pre_check.py  
    ├── rule_engine.py  
    ├── signal_engine.py  
    ├── llm_engine.py  
    └── decision_flow.py

---

# 六、验收标准

---

## 功能

* 能输出 BUY / HOLD / SELL
* 能正确判断仓位信号
* 能生成结构化解释

---

## 体验

* 非黑盒（可解释）
* 输出稳定（不漂移）

---

## 安全

* 不违反投资纪律
* 不输出绝对建议

---

# 七、一句话总结（面试版）

> 我设计的是一个分层的投资决策引擎，通过规则约束和信号层将复杂信息结构化，再由大模型进行推理，从而实现可解释、可控的投资建议输出，而不是简单的黑盒问答。


### 补充说明（工程实现）

1. intent_parser 实现方式：
   - 使用 Claude LLM 解析用户输入
   - 输出结构化 JSON

2. LLM输出处理：
   - 必须进行 JSON 提取（strip 非JSON文本）
   - 避免解析失败

3. LLM异常处理：
   - 若API调用失败或超时
   - 返回：“系统繁忙，请稍后再试”