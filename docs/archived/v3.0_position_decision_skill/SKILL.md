---
name: wealthpilot-position-decision
description: 为单一持仓标的（股票/基金）生成投资决策建议。适用场景包括用户询问"X 还能拿吗"、"X 该不该卖"、"X 涨了好多要不要止盈"等明确针对单标的的决策问题。该 Skill 编排 5 个阶段的工作流：数据加载 → 前置校验 → 规则校验 → 信号生成 → LLM 推理，最后输出 6 档结构化决策（BUY/HOLD/TAKE_PROFIT/REDUCE/SELL/STOP_LOSS）。
version: 1.0.0
tags: [position-decision, single-asset, investment-decision]
intent_binding: PositionDecision
---

# WealthPilot 单标的决策 SOP

本 Skill 描述 WealthPilot 处理单标的投资决策的标准操作流程。
当用户的请求被识别为针对单一具体标的的决策问题时，
按本 SOP 编排 `decision_engine/decision_flow.py:_run_pipeline` 的 5 个阶段。

## 工作流程

### Step 1: 数据加载 (Data Loading)

**入口**：`data_loader.load(asset_name, portfolio_id, user_query)`

加载决策所需的全部上下文数据：
- 用户投资画像（风险偏好、投资目标）
- 全部持仓快照（聚合后的 AggregatedPosition 列表）
- 目标标的信息（target_position）
- 投资纪律配置（InvestmentRules，从 handbook_official.md 加载）
- 投研观点（local 投研卡 + 实时联网搜索 + 盈米 MCP 基金诊断）
- 组合总市值

**中断条件**：
- 数据加载异常 → ABORTED
- 标的歧义匹配 → 返回候选清单让用户澄清（不进 Step 3）
- 数据质量错误 → ABORTED

### Step 2: 前置校验 (PreCheck)

**入口**：`pre_check.check(loaded_data)`

对加载到的数据做完整性校验，确保后续流程有足够的输入。

**中断条件**：not passed → ABORTED

### Step 3: 规则校验 (Rule Check)

**入口**：`rule_engine.check(loaded_data, intent)`

执行决策管道层级的规则检查。注意这里是**简化版**，仅检查单标仓位上限，
完整的纪律体系（11 条纪律 / 3 引擎）见 `references/discipline_overview.md`。

简化版的设计动机：决策管道追求实时响应，完整纪律体系在投资纪律页面、组合健康度评估等异步场景使用。

**中断条件**：
- 未持仓 + 非买入意图 → ABORTED（LLM 引导回复）

### Step 4: 信号生成 (Signal)

**入口**：`signal_engine.generate(loaded_data, intent, rule_result)`

生成 4 维度市场信号（仓位 / 基本面 / 事件 / 情绪），作为 LLM 推理的环境因子。

**中断条件**：仓位口径不一致 → ABORTED

### Step 5: LLM 推理 (Reasoning)

**入口**：`llm_engine.reason(user_query, data, intent, rule_result, signals, history)`

调用 GPT-4.1，基于前 4 步的全部输出生成结构化决策。
LLM 输出 LLMResult，包含：

- **decision**：6 档枚举之一（见下文"决策档位"）
- **reasoning**：list[str]，推理依据（每条独立逻辑链）
- **risk**：list[str]，风险提示
- **strategy**：list[str]，操作策略
- **chat_answer**：str，面向用户的中文自然语言回答
- **structured_result**：完整结构化 JSON（含 confidence、infoNeeded、evidenceSources 等）

**无中断**：始终返回 LLMResult，错误时 is_fallback=True

### Step 6: 运行时校验 (Validator) [Skill 边界外]

注意：Validator 在 `_run_pipeline` 之外、由 SSE 服务层在 yield done 事件前调用。
参见 `backend/graph/decision_validator.py`。

校验项：
- decision 必须在 6 档枚举内
- reasoning / risk 列表非空
- 纪律严重违规时不能给激进决策（BUY/TAKE_PROFIT）
- chat_answer 长度 >= 20 字
- 通用层 + PositionDecision 专项层

校验失败 action="retry"，验证仍失败 action="fallback"。

## 决策档位（6 档）

代码位置：`decision_engine/llm_engine.py` 的 `_VALID_DECISIONS`

| 档位 | 中文 | 含义 |
|------|------|------|
| BUY | 加仓 | 基本面 + 情绪 + 纪律均支持加仓 |
| HOLD | 观望 | 当前状态无显著调整必要 |
| TAKE_PROFIT | 部分止盈 | 累计盈利达阈值，建议落袋部分仓位 |
| REDUCE | 逐步减仓 | 基本面或纪律出现弱化信号 |
| SELL | 减仓/清仓 | 基本面恶化或重大利空 |
| STOP_LOSS | 止损离场 | 触发被动止损纪律 |

## 输出格式约束

最终给用户的 chat_answer 必须包含 5 个部分：

1. **结论**（一句话核心建议）
2. **核心依据**（3-5 条具体数据支撑）
3. **主要风险**（2-3 条）
4. **操作建议**（2-4 条具体可执行的步骤）
5. **免责声明**（仅供参考，不构成投资建议）

## 数据来源优先级

1. 用户持仓数据（必须）
2. 投资纪律配置 `data/handbook_official.md`（必须）
3. 盈米 MCP 基金诊断（基金类标的优先使用，见 backend/mcp_client/yingmi_client.py）
4. 实时联网搜索（股票类标的的投研补充）
5. LLM 自身知识（仅作为兜底，不能作为唯一依据）

## 引用规范

引用事实时明确标注来源：
- `（据盈米基金数据）`
- `（据公开数据）` （联网搜索）
- `（据您当前组合数据）`
- 自身推理：不加来源标注

## 完整纪律体系参考

本 Skill 在 Step 3 仅做仓位上限快速校验。完整纪律体系（11 条纪律）由 `app/discipline/` 的 3 引擎实现（risk_engine / decision_engine / psychology_engine），详见 `references/discipline_overview.md` 和 `references/handbook_official.md`。
