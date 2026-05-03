# WealthPilot 2.6 — Multi-Agent 决策系统升级 PRD

> 版本：v1.5（M1-Step1 验证后产品哲学校准）  
> 日期：2026-04-29  
> 文档定位：业务 + 架构 PRD（含 Agent 拆分、Tool 清单、评测体系），双读者（开发指导 + 面试展示）  
> 关联文档：`WealthPilot_2_0_产品优化与功能升级_v1_2.md`（已显式弃用 P0 部分）、`CHANGELOG.md`（v2.5.1 当前实现状态）

---

## 〇、版本变更说明

### v1.5 校准（2026-04-29，M1-Step1 验证后）

M1 启动前先做了"产品哲学假设验证"（脚本 `scripts/m1_path_verification.py` 实测），结果发现 v1.4 §2.2 关于"模糊输入主动推断"的描述**过于绝对**——当前代码的真实行为更细腻：

- **已实现"按盈亏筛选 Top-N 候选"**（PD_001/002 验证）
- **未实现"按仓位筛选"**（PD_003 验证：意图清晰但 asset 空，退化到通用回答）
- **未实现"自动选 Top-1"**（候选筛出后直接返回让用户选）

基于这个真实状态，v1.5 修订两处：

| 改动位置 | 修订内容 |
|---------|---------|
| §2.2 产品哲学 | 把"模糊输入主动推断"细化为**两级路径**：明确时直选 Top-1、模糊时返回候选选项。两种都是"主动"（系统都做了筛选） |
| §4.2 ResearchAgent | `infer_target_from_holdings` Tool 设计具体化：补 weight 维度 + Top-1 显著性阈值判断 + 候选返回结构。新增"PD_003 退化 bug"作为 M1 验收点 |
| §6.1 工作量 | M1 的推断 Tool 工作量从估算的"+0.5 天"具体化为补 weight 筛选 + 加 Top-1 逻辑两个明确动作（仍在 0.5 天预算内） |

**v1.5 没有改动的部分**：核心架构（7 Agent + LLM Planner + LangGraph）、Eval 体系、面试叙事、风险与缓解。仅是"产品哲学描述"和"具体 Tool 设计"的精细化校准。

### v1.4 校准（2026-04-29，M0 后）

**M0 阶段（用例 yaml 化）完成后基于代码勘探暴露的 7 项 gap，对 PRD 做现实校准**。本版本**没有引入新功能**，只是让 PRD 与代码事实对齐，避免 M1 实施时出现"PRD 和代码不一致"的方向性错误。

| 改动位置 | 修订内容 |
|---------|---------|
| §4.1 系统总览图 | Multi-Agent 从 5 个扩展到 **7 个**（新增 PreCheckAgent + SignalAgent），对齐代码 FlowStage |
| §4.2 Agent 拆分 | 新增 **PreCheckAgent**（数据完整性前置门禁）和 **SignalAgent**（4 维信号引擎）两个 Agent 的完整定义 |
| §4.2 PositionDecisionAgent | 决策档位从"7 档"改为"6 档"（BUY / HOLD / TAKE_PROFIT / REDUCE / SELL / STOP_LOSS），与代码 LLMResult 对齐 |
| §4.2 输出 schema 现状声明 | 新增"v2.5.1 现状 vs v2.6 目标"对比表，明确哪些字段已实现、哪些是 v2.6 引入。区分 PositionDecision (LLMResult) vs Generic (GenericLLMResult) 输出 schema 不对称 |
| §2.2 产品哲学 | 新增 **"模糊输入主动推断"原则**作为产品哲学显式声明（M0 决策点 1） |
| §2.3 Demo 场景 | 新增 **Fixture 设计原则**（5 持仓共享 fixture 覆盖核心场景） |
| §6.1 工作量 | M1 从 3 天扩到 **3.5 天**（多 2 个 Agent：PreCheck + Signal）；总工作量 9.5 → 10 天 |

**v1.4 没有改动的部分**：核心叙事（LLM Planner + Multi-Agent + Tool Calling + Eval Harness + Validator）、面试 Q&A、风险与缓解。所有锋利化叙事都保留。

### v1.3 补充（2026-04-29）

参考腾讯团队《Harness Engineering 工程化落地》一文反馈，补 v1.2 的两处真漏洞——**有 Eval 体系（离线评测）但缺运行时门禁**、**有跨版本对比但缺单 session 内多轮一致性评测**。两处改动总工作量约 0.5-1 天，纳入 M1 + M3：

1. **§4.2 新增 DecisionValidator 节点**（非 Agent，是纯函数运行时门禁）：放在 PositionDecisionAgent 输出后，对 DecisionResult 做确定性 schema + 业务约束检查（防幻觉、防 schema 违规、防纪律严重违规但仍给激进决策）。失败则强制重试或降级。这是参考"Eval 是事后分析、Validator 是运行时门禁，不能互相替代"的工程实践
2. **§5.2 L2 评测新增"多轮一致性"指标**：同一 session 内连续多轮决策，评测决策类型合理收敛性、ViewpointCard 引用前后一致性、补充信息后置信度单调性。这一指标针对"多轮对话场景下决策质量是否退化"的盲区
3. **§4.1 系统总览图更新**：DecisionValidator 显式画进 LangGraph 流程

### v1.2 定稿（2026-04-29）

v1.1 评审通过（评分 9.2/10），定稿前 3 处微调：

1. **明确开发顺序**：在第六章开头加注 **"M0 → M1/M2 → M3 → M4/M5 → M6"**，强调评测先行（M0 用例 yaml 化先做）、不一上来重构 LangGraph
2. **工作量表述统一**：全文统一为 **8.5 天**，避免 v1.1 末尾总结与正文 PRD 数字不一致
3. **Tool 数量精确化**：明确为 **7 个核心 Tool（4 个系统调度调用 + 3 个 Agent 自主调用）**，原 v1.1 部分位置写"6 个"不准确

### v1.1 修订（2026-04-29）

基于对 v1.0 的评审反馈，本版本聚焦"让 Multi-Agent 真正像 AI 系统、而不是 pipeline 重命名"，修订 4 处：

1. **OrchestratorAgent 升级为 LLM Planner（约束式）**：从"LLM 做意图分类 → 走预定义路径"升级为"LLM 生成结构化 routing_plan"，但保留约束确保工程可控（§4.2、§4.3）
2. **Tool 层引入"动态调用"标注**：明确哪些 Tool 由 Agent 在运行时通过 Function Calling 自主选择调用（§4.4）
3. **面试问答 Q1 升级**：从"为什么不用 LangChain"改为更锋利的"为什么不用普通代码写 graph"（§7.3）
4. **Demo 开场钩子升级**：以"如何定义一个好的金融决策"作为讲解起点（§7.2）

### v1.0 → 弃用 2.0 P0 的核心理由

弃用 2.0 P0（用户画像 / 首页重构 / 投资记录 / 收益分析），原因：补齐"产品闭环"价值高，但**不构成可对外讲解的 AI 系统设计案例**，无法体现 2026 年 AI Engineer / AI PM 岗位关注的核心能力（多 Agent 编排、LLM Planning、Tool/Function Calling、Eval Harness、可解释性）。

**2.6 的核心目标**：把已有的"投资决策 + 组合评估 + 资产配置"三大模块，从 **"Prompt + 服务编排"** 架构升级为 **"LLM-Planned Multi-Agent + Dynamic Tool Calling + Layered Eval Harness"** 架构，让 WealthPilot 可作为一个完整的 AI Agent 系统设计案例对外讲解。

**功能不再做齐全，技术做深**——这是本期的核心约束。

---

## 一、背景与问题陈述

### 1.1 现状（截至 v2.5.1）

WealthPilot 已具备以下能力：

- **完整的决策 Pipeline**：5 类意图识别、6 档决策输出（BUY / HOLD / TAKE_PROFIT / REDUCE / SELL / STOP_LOSS）、reasoning + risk + strategy 结构化字段
- **三层 ViewpointCard 投研架构**：FactsLayer / NarrativeLayer / JudgmentLayer，含保鲜机制（7/14/90 天分级过期）
- **跨市场标的标准化**：`<ticker>:<market>` 格式，EntityRegistry 支持公司级跨市场视图（如理想汽车 = LI:US + 2015:HK）
- **多源数据接入**：AlphaVantageAdapter（美股）、AKShareAdapter（港股 + A 股）、UserUploadAdapter，统一 InfoRouter 路由
- **多轮对话与澄清系统**：ConversationMessage 持久化、智能标的澄清流程
- **资产配置模块**：偏离度计算、增量分配算法、纪律校验（11 条投资纪律规则）
- **回归用例集**：18 个端到端决策用例，每次发版前全量跑通

### 1.2 当前架构的局限

虽然功能完整，但**架构本质仍是"Intent 分发 + Prompt 模板"**：

| 维度 | 当前状态 | 局限 |
|------|---------|------|
| Agent 抽象 | 隐式（散落在 decision_service.py 的方法中） | 无法体现"多 Agent 协作"系统设计 |
| 编排控制 | 硬编码顺序流（if-else 分发到不同处理函数） | LLM 不参与决策路径规划，编排是死的 |
| 能力调用 | 硬编码（service 层手动串联 loader / engine） | 缺少 Tool Calling 抽象，Agent 不能动态选择调用哪些能力 |
| 评测体系 | 18 个回归用例（端到端 pass/fail） | 缺少分层指标、缺少 LLM-as-judge、无法量化版本演进 |
| 能力复用 | WealthPilot 内部独占 | 投研能力无法对外暴露给其他 Agent 复用 |

### 1.3 本期解决的核心问题

> **从"一个能用的投顾 Demo"升级为"一个 LLM 主导规划、可量化、可解释、可扩展的 AI Agent 系统"。**

具体目标：

1. **LLM-Planned Multi-Agent**：将隐式编排显式化为 5 个职责明确的 Agent，**由 LLM Planner 在运行时生成调用 DAG**，而非预定义路由
2. **Dynamic Tool Calling**：把核心能力封装为标准化 Tool（含 JSON Schema 描述），区分"系统调度调用"和"Agent 自主调用"两类 Tool
3. **三层 Eval Harness**：建立 L1（意图）/ L2（Agent 调用）/ L3（决策质量，LLM-as-judge + Rubric）的分层评测体系
4. **MCP 服务化**：把投研能力暴露为 MCP Server，可被 Claude Desktop / 其他 Agent 复用
5. **双层 Memory**：Session Memory（LangGraph checkpointer）+ Long-term User Memory（结构化历史决策检索）

---

## 二、用户与场景（业务侧）

### 2.1 目标用户画像

**核心用户**：成熟个人投资者（A/H/美股多市场配置、资产规模 50-500 万、有自主决策能力但研判精力有限）。

**典型痛点**：
- 持仓分散在多家券商和市场，看不清全貌
- 研报和资讯量大，自己无法及时消化和关联到持仓
- 投资纪律明确但情绪化决策时难以执行
- 决策事后无法系统复盘，难以判断"建议是否被采纳、采纳后效果如何"

### 2.2 产品哲学（v1.4 显式声明、v1.5 精细化）

WealthPilot 在交互层面有两条**产品原则**，本期升级时所有 Agent 设计必须遵守：

#### 原则 1：主动筛选 + 分级响应（v1.5 修订）

当用户输入未指明具体标的（如"我有一只涨了不少的股票"），系统应**基于用户描述特征 + 持仓数据主动筛选候选集**，再根据候选集的"清晰度"决定如何响应——而非要么 100% 替用户决定、要么完全推回让用户重述。

筛选维度由 user_query 关键词触发：
- "涨了不少 / 盈利 / 赚" → 按 `profit_loss_rate` 降序筛 Top-N
- "持续亏损 / 套牢 / 跌" → 按 `profit_loss_rate` 升序筛 Top-N
- "已经不轻了 / 重仓 / 占比大" → 按 `weight` 降序筛 Top-N

筛选后**根据 Top-1 vs Top-2 的显著性差距**走两级响应：

| 候选清晰度 | 显著性判定 | 响应路径 | 例 |
|----------|----------|---------|----|
| **明确** | Top-1 vs Top-2 在主筛选维度上差距显著（如盈利率差 ≥ 15pp，或仓位差 ≥ 5pp） | **直选 Top-1**，进入完整决策链路 | 茅台 +25% vs 五粮液 +6.7%（差 18pp）→ 锁定茅台 |
| **模糊** | Top-1 vs Top-2 在主筛选维度上接近 | **返回 Top-3 候选清单 + 一个澄清问题**让用户挑 | 浮盈中三个 +25%/+22%/+20%（差 < 15pp）→ 列出 3 个让用户选 |

> **两种路径都是"主动"** —— 系统都做了筛选工作。区别只是"是否替用户做最终选择"。"模糊时返回候选"是有意识的设计，不是降级或放弃。

**为什么这样设计**：
- 成熟用户的耐心阈值很低，但**完全替用户决定**也会引起信任损失（万一系统选错了，用户没有快速纠正的途径）
- 返回 Top-3 候选清单是"AI 把工作做了一半，剩下一半的最终决定权留给用户"——既体现智能，也尊重用户判断
- M1-Step1 验证已确认当前代码部分实现了"返回候选"路径，本期的工作是**补全 weight 维度 + 加显著性判断**，让"明确时直选"也能跑

**这条原则约束 ResearchAgent 的设计**：当 IntentResult.asset 为空时，必须调用 `infer_target_from_holdings` Tool 走筛选 → 显著性判断 → 分级响应流程，不能直接降级到 ClarifyAgent，也不能在意图明确（如 PD_003 中 confidence = 0.85）的情况下退化到 Education 通用回答。详细 Tool 设计见 §4.2 ResearchAgent。

#### 原则 2：风控不交给 LLM（确定性规则优先）

11 条投资纪律必须通过 DisciplineAgent 的纯函数规则引擎执行，**不允许 LLM 解释或绕过**。即使 LLM 在 PositionDecisionAgent 给出"激进加仓"建议，只要触发任何 high-severity violation，DecisionValidator 强制降级为 wait + need_info。

这条原则在 §4.2 DisciplineAgent 设计中显式落地（纯函数 Agent，无 LLM 调用）。

### 2.3 核心场景（本期重点收敛到三个）

> **本期产品功能不做新增**，所有工作围绕这三个已有场景的"决策质量提升"和"系统可讲解性"展开。

#### 场景 A：单标的决策（PositionDecision）
**用户输入**："茅台还能拿吗？" / "理想汽车要不要止盈？"  
**期望输出**：明确的操作建议（buy_init / buy_more / hold / trim / exit / wait / need_info）+ 置信度 + 关键依据 + 风险点  
**典型 Agent 协作链路（LLM Planner 实际产出可能不同）**：Orchestrator(Planner) → Research → Discipline → PositionDecision

#### 场景 B：组合评估（PortfolioReview）
**用户输入**："我现在的组合健康吗？" / "整体看有什么问题？"  
**期望输出**：组合健康度评分 + 主要风险点 + 偏离纪律的具体条目 + 优先处理建议  
**典型链路**：Orchestrator(Planner) → Allocation → Discipline → Research（持仓相关）→ PositionDecision（综合）

#### 场景 C：资产配置（AssetAllocation）
**用户输入**："年终奖 20 万怎么加进现有组合？" / "我应该怎么调仓？"  
**期望输出**：增量资金分配方案 / 调仓方案，符合目标区间和纪律约束  
**典型链路**：Orchestrator(Planner) → Allocation → Discipline → PositionDecision

> **注**：以上链路只是"典型形态"。实际路径由 LLM Planner 在运行时根据用户输入动态生成——例如用户输入"茅台和宁德哪个该减"会生成并行 Research 节点 + 对比模式 PositionDecision，与单标的场景的链路并不相同。

### 2.4 面试 Demo 的"那一个场景"

按"30 分钟只够讲一个场景"的约束，**Demo 主线选场景 A：单标的决策**，因为：
- 链路最长（涵盖 4 个 Agent），最能展示 Multi-Agent 协作
- 涉及多源数据融合（实时数据 + ViewpointCard + 纪律规则），最能展示 Tool 抽象
- 用户输入的歧义最多（模糊标的、模糊意图），最能展示澄清和 LLM Planner 编排能力

### 2.5 Fixture 设计原则（v1.4 新增，源自 M0 实践）

为保证 18 个评测用例的可复现性和场景覆盖度，建立**共享 fixture 范式**：所有用例共用一套持仓快照（5 只持仓 + 1 个现金），用最少的数据覆盖最多的评测场景。

| 评测场景 | Fixture 中对应持仓 |
|---------|------------------|
| 浮盈最大（涨幅大类用例） | 贵州茅台 +25% |
| 浮亏最大（持续亏损类用例） | 中概互联 ETF -30% |
| 重仓占比偏高（行业集中类） | 贵州茅台 30% + 五粮液 8%（白酒 38%） |
| 中等持仓 | 招商银行 15%、宁德时代 18% |
| 跨行业（防止单一行业误导） | 银行 / 新能源 / 互联网 / 白酒 |
| 全新资金语境（AssetAllocation） | 用 must_not_contain 黑名单防 fixture 干扰 |

**Fixture 详细规格见 `m0/schema/fixtures_v0.1.md`**。M3 评测脚本加载 fixture 后构造测试 context，与生产数据库完全解耦。

---

## 三、目标与非目标

### 3.1 本期目标（Goals）

| 编号 | 目标 | 衡量标准 |
|------|------|---------|
| G1 | 7 个 Agent 显式拆分（含 PreCheck + Signal），由 LLM Planner 编排（约束式） | 三个核心场景全部跑通；LLM Planner 失败时降级到 fallback 路由表；回归用例 18/18 不退化 |
| G2 | 7 个核心能力 Tool 化（4 静态 + 3 动态），区分系统调度调用 vs Agent 自主调用 | 每个 Tool 有 JSON Schema 描述；至少 2 个 Tool 由 Agent 通过 Function Calling 动态决定调用 |
| G3 | L1/L2/L3 三层评测体系建立 | 每个 PR 自动跑评测，输出 HTML 报告，分数可对比 |
| G4 | 投研能力以 MCP Server 形式对外暴露 | Claude Desktop 可调用，含 ≥3 个工具方法 |
| G5 | 双层 Memory 架构落地 | Session 级支持中断恢复，User 级支持历史决策检索 |
| G6 | 面试材料可独立交付 | 1 张架构图 + 1 份 Eval 报告 + 1 段 5 分钟 Demo + 1 份 1 页项目描述 |

### 3.2 非目标（Non-Goals）

明确**本期不做**的事，避免范围蔓延：

- ❌ 任何新业务功能（投资记录、收益分析、首页重构、用户画像动态化、截图 OCR、Telegram Bot 全部**推迟到 3.0**）
- ❌ 不引入 LangChain（理由：当前自有抽象更轻量，引入是负 ROI）
- ❌ 不重写已有的 ViewpointCard / EntityRegistry / Adapter 架构（这些是优势资产，仅在外层包装）
- ❌ 不做 RAG 向量检索升级（当前关键词 + 结构化查询足以支撑面试 Demo，引入向量化是过度工程）
- ❌ 不做量化回测、不做策略生成（业务定位不同，远期再议）
- ❌ 不做完全自由的 LLM Planning（理由：稳定性差、token 成本不可控；本期只做"约束式 Planner"）

---

## 四、架构设计（核心章节）

### 4.1 系统总览

```
┌──────────────────────────────────────────────────────────────────┐
│                         User / Frontend                           │
│                   (React + SSE 流式渲染)                            │
└─────────────────────────────┬────────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────────┐
│                   FastAPI Gateway (/api/decision/chat)            │
└─────────────────────────────┬────────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────────┐
│              LangGraph StateGraph (Decision Graph)                │
│                                                                   │
│  ┌──────────────────────────┐                                     │
│  │ Orchestrator (Planner)   │ ◄─── LLM 生成 routing_plan(DAG)    │
│  │  - intent recognition    │       + 失败降级到 fallback 路由表    │
│  │  - DAG planning (LLM)    │                                     │
│  └─────────────┬────────────┘                                     │
│                │                                                  │
│                │ 按 routing_plan 动态分发                            │
│                │                                                  │
│  ┌─────────────┼──────────────┬──────────────┐                    │
│  ▼             ▼              ▼              ▼                    │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐               │
│ │ Research │ │Allocation│ │Discipline│ │ Clarify  │               │
│ │  Agent   │ │  Agent   │ │  Agent   │ │  Agent   │               │
│ │ (LLM +   │ │(确定性+  │ │ (纯函数) │ │ (规则)   │               │
│ │  动态    │ │ LLM 解释)│ │          │ │          │               │
│ │  Tool)   │ │          │ │          │ │          │               │
│ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘               │
│      │            │            │            │                     │
│      ▼            │            │            │                     │
│  ┌──────────┐     │            │            │                     │
│  │ PreCheck │     │            │            │                     │
│  │  Agent   │     │            │            │                     │
│  │ (纯函数  │     │            │            │                     │
│  │ 数据完整 │     │            │            │                     │
│  │ 性门禁)  │     │            │            │                     │
│  └────┬─────┘     │            │            │                     │
│       │           │            │            │                     │
│       ▼           │            │            │                     │
│  ┌──────────┐     │            │            │                     │
│  │ Signal   │     │            │            │                     │
│  │  Agent   │     │            │            │                     │
│  │ (4 维信号│     │            │            │                     │
│  │ 仓位/事件│     │            │            │                     │
│  │ 基本面/  │     │            │            │                     │
│  │ 情绪)    │     │            │            │                     │
│  └────┬─────┘     │            │            │                     │
│       │           │            │            │                     │
│       └───────────┴──────┬─────┘            │                     │
│                          ▼                  │                     │
│                  ┌───────────────┐          │                     │
│                  │ PositionDeci- │          │                     │
│                  │ sion Agent    │ ◄────────┘                     │
│                  │ (LLM 综合,    │                                 │
│                  │  6 档决策)    │                                 │
│                  └──────┬────────┘                                 │
│                         │                                          │
│                         ▼                                          │
│                  ┌───────────────┐                                 │
│                  │ Decision      │ ◄── 纯函数运行时门禁              │
│                  │ Validator     │     (schema/防幻觉/纪律一致性)    │
│                  │ (脚本检查)    │                                  │
│                  └──────┬────────┘                                 │
│                  pass / │ \\ fail (重试或降级)                       │
│                         ▼                                          │
│                       END                                          │
└─────────────────────────┬────────────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────────┐
│                       Tool Layer                                  │
│   ─── 系统调度调用 ───      │     ─── Agent 自主调用 ───              │
│   fetch_holdings           │     query_viewpoint_cards             │
│   calc_allocation_deviation│     fetch_realtime_research           │
│   check_discipline_rules   │     web_search                        │
│   propose_increment_plan   │                                       │
└─────────────────────────┬────────────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────────┐
│            Memory Layer            │     Persistence Layer         │
│ ┌─────────────────┐ ┌────────────┐ │ ┌──────────────────────────┐ │
│ │ Session Memory  │ │ Long-term  │ │ │  SQLite (SQLAlchemy)     │ │
│ │ (LangGraph      │ │ User       │ │ │  - holdings              │ │
│ │  Checkpointer)  │ │ Memory     │ │ │  - viewpoint_cards       │ │
│ └─────────────────┘ └────────────┘ │ │  - decision_history      │ │
│                                    │ │  - conversation_messages │ │
│                                    │ │  - user_memory (新)      │ │
│                                    │ └──────────────────────────┘ │
└────────────────────────────────────┴───────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────────┐
│                External Capability Exposure                       │
│           ┌─────────────────────────────────────────┐             │
│           │  wealthpilot-research-mcp (MCP Server)  │             │
│           │  fetch_news / fetch_fundamental /       │             │
│           │  query_viewpoint_cards                  │             │
│           └─────────────────────────────────────────┘             │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 Agent 拆分

7 个 Agent 的职责边界、输入输出、调用 Tool 清单（v1.4：原 5 个 Agent + PreCheck + Signal）：

#### Agent 1：OrchestratorAgent（LLM Planner，本期最大升级点）

| 项 | 内容 |
|----|------|
| **核心职责** | 意图理解 + 动态生成 routing_plan（Agent 调用 DAG）+ 失败降级 |
| **何时是 LLM 推理** | 是。LLM call 输出结构化 routing_plan |
| **输入** | user_query、conversation_history、position_names、available_agents、available_tools |
| **输出** | `RoutingPlan` 对象（见下方 Schema），含 nodes（Agent 调用列表）+ edges（执行顺序）+ rationale（决策理由） |
| **使用 Tool** | 无（纯路由判断） |
| **关键设计** | **约束式 Planner**：LLM 只能从 5 个已知 Agent 中选；必须返回结构化 JSON（Pydantic 校验）；Pydantic 校验失败时降级到 fallback 路由表（按 v1.0 的 5 类 intent 路由） |

**RoutingPlan Schema**：

```python
class AgentNode(BaseModel):
    agent: Literal["Research", "PreCheck", "Signal", "Discipline", "Allocation", "PositionDecision", "Clarify"]
    inputs: dict           # 传给该 Agent 的关键参数（如 target_symbols）
    parallel_group: Optional[int] = None  # 同 group 的节点可并行执行

class RoutingPlan(BaseModel):
    intent: Literal["PositionDecision", "PortfolioReview", "AssetAllocation", "PerformanceAnalysis", "Education"]
    nodes: list[AgentNode]                 # 1-7 个 Agent 节点
    needs_clarification: bool
    clarification_reason: Optional[str]
    rationale: str                          # LLM 生成此 plan 的理由（用于可解释性 + Eval）
```

**为什么这样设计**：
- LLM 拥有"路径决策权"，体现 Multi-Agent 系统的"自主性"
- 但选择空间被严格限制（只能从 7 个 Agent 选），保证工程可控
- rationale 字段强制 LLM 给出选择理由，既提升决策稳定性（Chain-of-Thought），也方便 L2 评测时人工 review
- 失败降级保证系统可用性 ≥ v1.0 基线（不会因为 Planner 故障导致全系统不可用）

**Planner 实际产出示例**：

```json
// 输入: "茅台还能拿吗？"
{
  "intent": "PositionDecision",
  "nodes": [
    {"agent": "Research", "inputs": {"target_symbols": ["600519:SH"]}},
    {"agent": "Discipline", "inputs": {"focus_symbols": ["600519:SH"]}},
    {"agent": "PositionDecision", "inputs": {"mode": "single_holding"}}
  ],
  "needs_clarification": false,
  "rationale": "用户对单一持仓发起持有判断，需要先获取该标的研究依据并校验纪律约束，再综合输出决策。"
}

// 输入: "茅台和宁德哪个该减？"
{
  "intent": "PositionDecision",
  "nodes": [
    {"agent": "Research", "inputs": {"target_symbols": ["600519:SH"]}, "parallel_group": 1},
    {"agent": "Research", "inputs": {"target_symbols": ["300750:SZ"]}, "parallel_group": 1},
    {"agent": "Discipline", "inputs": {"focus_symbols": ["600519:SH", "300750:SZ"]}},
    {"agent": "PositionDecision", "inputs": {"mode": "comparison"}}
  ],
  "needs_clarification": false,
  "rationale": "用户提出双标的对比决策，并行获取两个标的研究信息，然后做对比模式综合。"
}
```

#### Agent 2：ResearchAgent（投研分析师 + 动态 Tool 选择）

| 项 | 内容 |
|----|------|
| **核心职责** | 拉取并加工目标标的的投研信息，输出结构化研究依据 |
| **何时是 LLM 推理** | 是。把多源 RawFact 加工成 ViewpointCard（已有逻辑）+ **动态决定调用哪些 Tool**（新增） |
| **输入** | target_symbols、entity_id、user_query |
| **输出** | research_cards（已确认 + 实时数据互补）、tool_calls_log、**candidate_holdings（v1.5：模糊路径下返回的候选清单）** |
| **使用 Tool** | `query_viewpoint_cards`（必调用）、`fetch_realtime_research`（**Agent 自主决定**）、`web_search`（**Agent 自主决定**）、`infer_target_from_holdings`（**asset 为空时必调用**，v1.5 设计具体化） |
| **Tool 调用决策逻辑** | 通过 LLM Function Calling 在运行时决定：(1) 本地 ViewpointCard 数据是否充分？充分则只调用 query；(2) 卡片中关键事件距今较远或缺失？追加 fetch_realtime_research；(3) 用户问题涉及最新动态？追加 web_search；(4) **若 asset 为空且用户描述含"涨/跌/重仓"等持仓特征关键词，调用 infer_target_from_holdings 走分级响应**（落地 §2.2 原则 1） |
| **关键设计** | 复用现有 ViewpointRepository / Renderer / 三层 Card 架构，仅在外层加 Function Calling 决策层 |

##### `infer_target_from_holdings` Tool 详细设计（v1.5 具体化）

**为什么是 v1.5 才落地具体设计**：M1-Step1 验证发现当前代码已实现"按盈亏筛选"但缺三件事——(a) weight 维度筛选缺失（PD_003 退化）、(b) Top-1 自动选择缺失（PD_001/002 都是返回 Top-3）、(c) 显著性判断缺失。这三个 gap 由本 Tool 集中解决。

**Tool 签名**：

```python
def infer_target_from_holdings(
    user_query: str,
    positions: list[Position],
) -> InferenceResult:
    """
    基于 user_query 关键词 + 持仓特征推断目标标的，返回分级响应。
    """

class InferenceResult(BaseModel):
    mode: Literal["single", "multi", "none"]      # 单选 / 多候选 / 无匹配
    primary_dimension: Literal["profit_loss", "weight", "category"]  # 主筛选维度
    selected: Optional[Position] = None             # mode=single 时填
    candidates: list[Position] = []                 # mode=multi 时填，Top-3
    significance_gap: Optional[float] = None        # Top-1 vs Top-2 在主维度的差距
    rationale: str                                   # 推断理由（"基于您说'涨了不少'..."）
```

**筛选维度（按 user_query 关键词路由）**：

| 关键词 | 主维度 | 排序 | 类别过滤（可选叠加） |
|-------|------|------|------------------|
| 涨 / 盈利 / 赚 / 浮盈 / 落袋为安 | profit_loss_rate | 降序 | - |
| 跌 / 亏 / 套牢 / 浮亏 / 止损 | profit_loss_rate | 升序 | - |
| 重仓 / 不轻 / 占比大 / 仓位偏重 | weight | 降序 | - |
| 基金（叠加在盈亏维度上） | - | - | asset_class == 基金/ETF |
| 股票（叠加） | - | - | asset_class == 股票 |

**显著性判断（Top-1 vs Top-2 差距阈值）**：

| 主维度 | 显著阈值 | 解释 |
|-------|--------|------|
| profit_loss_rate | ≥ 15 个百分点 | 例：+25% vs +6% (差 19pp) → 直选；+22% vs +20% (差 2pp) → 多候选 |
| weight | ≥ 5 个百分点 | 例：30% vs 18% (差 12pp) → 直选；30% vs 28% (差 2pp) → 多候选 |

**返回结构**：

```python
# 例 1：明确路径（PD_001 fixture）
InferenceResult(
    mode="single",
    primary_dimension="profit_loss",
    selected=Position(symbol="600519:SH", name="贵州茅台", profit_loss_rate=0.25),
    significance_gap=0.18,  # 茅台 +25% vs 五粮液 +6.7%
    rationale="您说'涨了不少'，您持仓中贵州茅台浮盈最多（+25%），且显著高于第二名（+6.7%）"
)

# 例 2：模糊路径（多候选）
InferenceResult(
    mode="multi",
    primary_dimension="profit_loss",
    candidates=[Position(...), Position(...), Position(...)],  # Top-3
    significance_gap=0.02,  # Top-1 和 Top-2 都是 +25% 左右
    rationale="您说'涨了不少'，您持仓中浮盈较多的有 3 只，请问您指的是哪一只？"
)

# 例 3：无匹配（关键词不在表中）
InferenceResult(mode="none", rationale="未识别到持仓特征关键词")
```

**调用方处理**：

- ResearchAgent 收到 `mode=single` → 把 `selected.symbol` 填入 state.target_symbols，继续走完整决策链路（PreCheck → Signal → Discipline → PositionDecision）
- ResearchAgent 收到 `mode=multi` → 把候选清单 + 澄清问题作为 chat_answer 返回，FlowStage 停在 intent，不消耗后续 LLM 调用
- ResearchAgent 收到 `mode=none` → 走传统的 ClarifyAgent 路径

**M1 实现工作量**：0.5 天（细分：补 weight 筛选 0.2 天 + 加 Top-1 显著性逻辑 0.3 天）

#### Agent 3：PreCheckAgent（数据完整性前置门禁，v1.4 新增）

| 项 | 内容 |
|----|------|
| **核心职责** | 在 Research 完成后、Signal 计算前，校验决策所需的数据是否完整可用，缺失则提前 abort 而非让下游 Agent 跑出错误结果 |
| **何时是 LLM 推理** | **否，纯函数脚本** |
| **输入** | research_cards、target_position、loaded_data（来自 ResearchAgent） |
| **输出** | `pre_check_result: {passed: bool, message: str, missing_fields: list}` |
| **使用 Tool** | 无 |
| **代码对应** | `app/decision_engine/data_loader.py` 现有的 `PreCheckResult`，对应 FlowStage.PRE_CHECK |
| **校验项（V1）** | (1) target_position 非空；(2) market_value_cny / cost_value_cny 等关键字段非空；(3) 若需要研报数据，research_cards 非空且至少 1 张未过期；(4) 跨市场标的的 EntityRegistry 解析成功 |
| **失败处理** | 直接路由到 ClarifyAgent 输出"数据不足"提示给用户，而非走 SignalAgent → PositionDecisionAgent 浪费 LLM call |
| **关键设计** | **这是一个被低估的工程价值节点**——在 LLM 推理前过滤掉数据问题，能省 60%+ 的无效 LLM 调用，也避免 "LLM 编出不存在的研报来圆数据缺失"这种幻觉 |

#### Agent 4：SignalAgent（4 维信号引擎，v1.4 新增）

| 项 | 内容 |
|----|------|
| **核心职责** | 基于 PreCheck 通过的数据，生成 4 维结构化信号，作为 PositionDecisionAgent 的核心输入 |
| **何时是 LLM 推理** | **否，纯函数 + 确定性规则**（v2.5.1 已实现） |
| **输入** | research_cards、target_position、rule_result、loaded_data |
| **输出** | `SignalResult: {position_signal, event_signal, fundamental_signal, sentiment_signal}` |
| **使用 Tool** | 无（内部纯函数实现） |
| **代码对应** | `app/decision_engine/signal_engine.py`，对应 FlowStage.SIGNAL |
| **4 维信号定义** | (1) **仓位信号**：偏高 / 合理 / 偏低（基于 weight vs target_range）；(2) **事件信号**：`{uncertainty: 高/中/低, direction: 利好/中性/利空}`（基于 ViewpointCard 的最近事件加工）；(3) **基本面信号**：正面 / 中性 / 负面 / N/A；(4) **情绪信号**：中性（MVP 固定，预留扩展） |
| **关键设计** | **结构化中间产物的价值**：把"持仓 + 研报 + 规则"这种异构数据压缩成 4 个简洁信号送入 LLM，比直接喂原始数据准确率高很多。这是当前架构的隐藏王牌——面试时讲 Multi-Agent 最有差异化的环节 |
| **设计哲学** | LLM 不擅长"加工原始数据"，但很擅长"基于结构化信号合成判断"。SignalAgent 把脏活累活留给确定性规则，让 LLM 只负责它擅长的"语义合成"——这是清晰的能力边界划分 |

#### Agent 5：DisciplineAgent（纪律校验官，纯函数）

| 项 | 内容 |
|----|------|
| **核心职责** | 基于 11 条投资纪律规则，校验当前决策上下文中的违规项 |
| **何时是 LLM 推理** | 否。纯规则引擎（确定性逻辑） |
| **输入** | positions、proposed_action（可选，预估某个动作的违规情况）、focus_symbols |
| **输出** | violations（list）、relevant_rules（与本次决策相关的纪律条目摘要） |
| **使用 Tool** | `check_discipline_rules`（系统调度调用，固定） |
| **代码对应** | `app/decision_engine/rule_engine.py`，对应 FlowStage.RULE_CHECK |
| **关键设计** | 作为 LangGraph 中的"纯函数节点"，无 LLM 调用，确保确定性和可复现 |
| **设计哲学** | **风控不交给 LLM**（落地 §2.2 原则 2）——这是金融场景关键差异点，硬规则不能被概率模型概率化 |

#### Agent 6：AllocationAgent（资产配置师，半确定性）

| 项 | 内容 |
|----|------|
| **核心职责** | 计算当前组合相对目标区间的偏离 + 生成增量/调仓方案 |
| **何时是 LLM 推理** | 部分是。计算层确定性，自然语言输出层用 LLM |
| **输入** | positions、target_ranges、incremental_amount（可选） |
| **输出** | deviation_snapshot、allocation_plan、explanation |
| **使用 Tool** | `calc_allocation_deviation`（系统调度调用）、`propose_increment_plan`（系统调度调用） |
| **关键设计** | 复用现有 `app/allocation/calculator.py` 纯函数引擎；LLM 仅负责把计算结果翻译成自然语言解释 |

#### Agent 7：PositionDecisionAgent（决策综合官）

| 项 | 内容 |
|----|------|
| **核心职责** | 综合上游 Agent（Research / PreCheck / Signal / Discipline / Allocation）的输出，生成最终的 6 档决策 + 关键依据 + 风险点 |
| **何时是 LLM 推理** | 是。核心决策合成节点 |
| **输入** | research_cards、signals（4 维信号）、discipline_violations、allocation_deviation、user_profile、conversation_history、mode（single_holding / comparison / portfolio_view） |
| **输出** | LLMResult（6 档决策 + reasoning + risk + strategy + chat_answer），未来扩展为 DecisionResult schema |
| **使用 Tool** | `web_search`（**Agent 自主决定**，仅在 LLM 输出 fallback 信号时考虑调用以补强证据） |
| **代码对应** | `app/decision_engine/llm_engine.py`，对应 FlowStage.LLM |
| **关键设计** | 严格使用结构化 prompt 模板，确保输出 schema 稳定；mode 字段决定 prompt 模板分支（单标 / 对比 / 组合视角） |

##### 6 档决策定义（与代码 LLMResult.decision 对齐）

| 档位 | 中文 | Emoji | 适用场景 |
|------|------|-------|---------|
| `BUY` | 加仓 | 📈 | 信号利好 + 仓位偏低 + 无纪律违规 |
| `HOLD` | 观望 | 🔍 | 信号中性 / 不确定性高 |
| `TAKE_PROFIT` | 部分止盈 | 💰 | 重仓 + 浮盈较大 + 信号偏中性 |
| `REDUCE` | 逐步减仓 | 📉 | 仓位偏高 + 基本面边际转弱 |
| `SELL` | 减仓 / 清仓 | 🚨 | 基本面恶化 + 信号利空 |
| `STOP_LOSS` | 止损离场 | 🛑 | 跌破投资逻辑 + 浮亏达纪律阈值 |

> **注**：v1.3 PRD 写"7 档决策"是设想（含 buy_init / buy_more 两档对应 BUY），但代码现状是 6 档。v1.4 与代码对齐，不强行扩 7 档（细分为 init/more 在 LLM 输出层意义不大，可由 Tool 调用层判断"持仓里是否有该 asset"得知是初次买入还是加仓）。

##### 输出 Schema 现状声明（v1.4 关键澄清）

代码当前的 LLMResult / GenericLLMResult 与 PRD 设想的"统一 DecisionResult schema"存在 gap，本期重构目标是逐步统一，但**不会在 v2.6 一次性改完**。这是有意识的渐进策略——避免 M1 重构爆炸式扩散到所有依赖该 schema 的代码。

| 字段 | v2.5.1 现状 | v2.6 目标 | M1 改动 |
|-----|-----------|----------|---------|
| `decision` (6 档枚举) | ✅ 已实现（LLMResult.decision） | ✅ 保留 | 无 |
| `reasoning` (推理依据列表) | ✅ 已实现 | ✅ 保留 | 无 |
| `risk` (风险列表) | ✅ 已实现 | ✅ 保留 | 无 |
| `strategy` (操作策略列表) | ✅ 已实现 | ✅ 保留 | 无 |
| `chat_answer` (自然语言回答) | ✅ 已实现 | ✅ 保留 | 无 |
| `confidence` (置信度 0-1) | ❌ 未实现 | ⭐ v2.6 引入 | M1 加，DecisionValidator 用 |
| `confidence_reason` (置信度理由) | ❌ 未实现 | ⭐ v2.6 引入 | M1 加 |
| `evidence_sources` (引用清单) | 部分（reasoning 中文本提及，非结构化） | ⭐ v2.6 引入结构化 | M1 加 |
| `info_needed` (待补充信息) | ❌ 未实现 | ⭐ v2.6 引入 | M1 加 |
| `decision_corrected` (修正标记) | ✅ 已实现（BUG-04 修复产物） | ✅ 保留 | 无 |
| `structured_result` (Phase 1 结构化) | 🟡 字段已加，填充逻辑不完整 | ⭐ v2.6 完善 | M1 完善 |

**Generic 类（PortfolioReview / AssetAllocation / PerformanceAnalysis）的 GenericLLMResult**：
- 当前结构：`{intent_type, chat_answer, raw_payload}`
- v2.6 目标：保留 chat_answer，规范化 raw_payload schema（按 intent_type 分别定义）
- M3 阶段评测可基于 raw_payload 做更深的 L3 评测（如组合健康度评分对比）

**为什么这种渐进而非激进**：v2.6 核心目标是"架构升级"（Multi-Agent + Planner + Eval），不是"schema 大重构"。schema 演进留给 v3.0 业务功能补齐时一并处理，避免本期范围爆炸。

#### 关键节点：DecisionValidator（运行时门禁，v1.3 新增）

**这不是一个 Agent，而是一个纯函数节点**，作为 PositionDecisionAgent 输出后的最后一道运行时门禁。设计哲学来自工程实践共识：**Eval Harness 是事后离线分析，Validator 是运行时门禁，两者互补不可替代**。

| 项 | 内容 |
|----|------|
| **核心职责** | 对 PositionDecisionAgent 输出的 DecisionResult 做确定性检查，不通过则强制重试或降级 |
| **是否 LLM 推理** | **否，纯函数脚本** |
| **输入** | `decision_result`（待校验）、`research_cards`、`positions`、`discipline_violations` |
| **输出** | `validation_result: {pass: bool, failures: list[ValidationFailure], action: "pass" | "retry" | "fallback"}` |
| **使用 Tool** | 无 |

**校验规则（V1 版本，全部为确定性检查）**：

| 类型 | 校验项 | 失败处理 |
|------|-------|---------|
| **Schema 完整性** | DecisionResult 7 个必填字段（finalAction、confidence、rationale、riskPoints、evidenceSources、confidenceReason、infoNeeded）非空 | retry（最多 1 次） |
| **防幻觉：标的引用** | rationale / evidenceSources 中提到的 symbol 必须真实存在于 positions 或 research_cards | retry（带具体错误反馈） |
| **防幻觉：数据引用** | evidenceSources 中引用的 ViewpointCard ID / 数据来源必须真实存在 | retry |
| **置信度-信息完整性一致性** | 当 confidence < 0.5 时，infoNeeded 字段必须非空 | retry |
| **rationale 精炼性** | rationale 条目数 ≤ 3 | retry |
| **riskPoints 完备性** | finalAction ∈ {buy_init, buy_more, trim, exit} 时，riskPoints 必须非空 | retry |
| **纪律-决策一致性** | 当 discipline_violations 中含 severity="high" 项时，finalAction 不能为 buy_init / buy_more | **fallback**（强制降级到 wait + need_info，附原因说明） |
| **mode 一致性** | 单标 mode 下 decision 只针对一个 symbol；对比 mode 下必须涵盖 routing_plan 中所有 target_symbols | retry |

**重试与降级机制**：
- 校验失败 → 第 1 次重试时，把具体 ValidationFailure 反馈给 PositionDecisionAgent prompt，要求修正
- 第 1 次重试仍失败 → 降级输出 `finalAction = "wait"` + `infoNeeded` 列出未通过的校验项 + `confidence = 0.3`
- 所有 ValidationFailure 写入 `state.validation_log`，供 L2 评测和后续 prompt 优化使用

**为什么需要 Validator（设计哲学）**：

LLM 的输出在语义层面可能完全合理，但在 **schema 层面 / 引用真实性 / 业务约束自洽性** 上仍可能违规。Eval Harness 只能在测试时发现这些问题，**生产环境每一次决策都需要运行时门禁兜底**。

举例：PositionDecisionAgent 完全可能输出 "rationale 引用了茅台的某条研报但 research_cards 里根本没有这条" ——这种幻觉 Validator 能立刻拦截，而 Eval 只能在事后统计幻觉率。

**Validator 与 Eval Harness 的分工**：

| 维度 | Validator（运行时） | Eval Harness（离线） |
|------|------------------|------------------|
| 触发时机 | 每次决策都跑 | 每个 PR / 版本跑 |
| 评判方式 | 确定性脚本 | LLM-as-judge + Rubric |
| 失败动作 | 重试 / 降级（用户可见） | 报告生成（用户不可见） |
| 关注点 | 单次决策的"硬错误" | 整体系统的"质量趋势" |

#### 辅助节点：ClarifyAgent（澄清节点，非主路径）

当 Planner 判断意图模糊或标的不明时进入此节点，输出澄清问题让用户回答。复用现有 `_build_clarification_reply` 逻辑，包装为 LangGraph 节点。

### 4.3 LangGraph StateGraph 设计

#### State 定义

```python
from typing import TypedDict, Optional, Annotated
from langgraph.graph import add_messages

class DecisionState(TypedDict):
    # 输入
    user_query: str
    session_id: str
    conversation_history: Annotated[list, add_messages]
    
    # Orchestrator (Planner) 填充
    routing_plan: RoutingPlan          # 核心：LLM 生成的 DAG
    plan_source: str                    # "llm_planner" | "fallback_router"（Eval 时区分）
    
    # ResearchAgent 填充
    research_cards: list[dict]
    research_meta: dict                 # 卡片数量 / 来源分布 / 时效性
    research_tool_calls: list[dict]     # Agent 实际调用的 Tool 序列（L2 评测用）
    
    # DisciplineAgent 填充
    discipline_violations: list[dict]
    relevant_rules: list[dict]
    
    # AllocationAgent 填充
    allocation_deviation: Optional[dict]
    allocation_plan: Optional[dict]
    
    # PositionDecisionAgent 填充（最终输出）
    decision_result: Optional[dict]     # DecisionResult schema
    decision_tool_calls: list[dict]
    
    # DecisionValidator 填充（v1.3 新增）
    validation_result: Optional[dict]   # {pass, failures, action}
    validation_log: list[dict]          # 完整校验记录（含重试历史）
    decision_retried: bool              # 是否经过重试
    decision_fallback: bool             # 是否走了降级路径
    
    # 调试与评测
    agents_invoked: list[str]           # 实际调用的 Agent 序列（L2 评测）
    all_tool_calls: list[dict]          # 全链路 Tool 调用序列（L2 评测）
```

#### 编排逻辑（双轨制）

**主轨：LLM Planner 驱动**

```
1. user_query → Orchestrator(Planner) 节点
2. Planner 输出 RoutingPlan（含 DAG）
3. LangGraph 执行器按 DAG 顺序调度 Agent，parallel_group 相同的并行执行
4. 所有 Agent 节点执行完毕后，结果汇入 PositionDecision（如 plan 中包含）
5. 输出最终 DecisionResult，END
```

**降级轨：Fallback 路由表**（Planner 失败时启用）

```
intent == "PositionDecision"     → Research → Discipline → PositionDecision → END
intent == "PortfolioReview"      → Allocation → Discipline → Research → PositionDecision → END
intent == "AssetAllocation"      → Allocation → Discipline → PositionDecision → END
intent == "PerformanceAnalysis"  → 复用 PortfolioReview 路径
intent == "Education"            → PositionDecision（直答）→ END
needs_clarification == True      → Clarify → END
```

降级触发条件：
- Pydantic 校验 RoutingPlan 失败
- LLM Planner 调用超时（>5 秒）
- LLM Planner 返回的 nodes 列表为空或不合法

降级后 plan_source 字段标记为 `fallback_router`，便于 Eval 时统计 LLM Planner 成功率。

### 4.4 Tool Layer 设计

7 个核心 Tool，按调用方式分两类（4 个系统调度调用 + 3 个 Agent 自主调用）：

#### 系统调度调用（System-Orchestrated Tools）

由 LangGraph 节点执行时确定性调用，参数由 state 传入。

| Tool 名称 | 职责 | 输入 Schema | 输出 Schema | 调用 Agent |
|----------|------|------------|------------|-----------|
| `fetch_holdings` | 拉取用户当前持仓快照 | `{user_id?, account_filter?}` | `{positions: Position[], total_value, total_cost}` | Allocation, PositionDecision |
| `check_discipline_rules` | 校验持仓 + 拟议动作对 11 条规则的违规情况 | `{positions, proposed_action?: ActionPlan, focus_symbols?: list}` | `{violations: Violation[], relevant_rules: Rule[]}` | Discipline |
| `calc_allocation_deviation` | 计算当前组合相对目标区间的偏离 | `{positions, target_ranges}` | `{deviation_snapshot}` | Allocation |
| `propose_increment_plan` | 基于偏离生成增量分配方案 | `{deviation, incremental_amount, constraints}` | `{allocation_plan}` | Allocation |

#### Agent 自主调用（Agent-Decided Tools，本期升级重点）

由 Agent 在运行时通过 LLM Function Calling **决定是否调用**及**调用参数**。

| Tool 名称 | 职责 | 调用决策依据 | 调用 Agent |
|----------|------|------------|-----------|
| `query_viewpoint_cards` | 查询已确认的投研卡片（含 entity 扩展） | 必调用（每次 Research 都要查本地卡） | Research |
| `fetch_realtime_research` | 异步触发实时数据拉取（AV + AKShare） | **Agent 决策**：本地卡数据是否充分？是否有过期卡需要补强？ | Research |
| `web_search` | 联网搜索补强证据 | **Agent 决策**：本地卡 + 实时数据是否覆盖用户问题？决策置信度是否足够？ | Research, PositionDecision |

**实现要求**：
- 所有 Tool 都提供 JSON Schema 描述（Pydantic model 自动导出）
- Agent 自主调用类 Tool 注册到 LLM 的 functions/tools 参数中，由 LLM 通过 Function Calling 协议触发
- 所有 Tool 调用都记录到 state.all_tool_calls，供 L2 评测使用

**为什么这样设计 Tool 调用模式**：

GPT 反馈中提到"让 Agent 决定用本地卡还是拉实时"，这其实**不是好的 Function Calling 场景**——这是业务规则（卡片在保鲜期就用本地，过期就拉实时），让 LLM 决定反而引入不确定性。真正适合 LLM Function Calling 的场景是**信息收集决策**：

- "本地数据是否充分支撑回答" → LLM 判断（涉及对用户问题的语义理解）
- "是否需要联网搜索补强" → LLM 判断（涉及对当前证据置信度的评估）
- "卡片在保鲜期是否还需用" → 业务规则（不需要 LLM 决定）

这条原则保证了"Tool Calling 用在该用的地方"，避免把确定性逻辑也甩给 LLM 而引入失控。

### 4.5 双层 Memory 设计

#### Session Memory（短期）

- **载体**：LangGraph 内置 SqliteSaver checkpointer
- **粒度**：按 session_id 持久化整个 DecisionState
- **能力**：
  - 中断恢复（用户刷新页面后继续上一轮对话）
  - 多轮上下文自动注入（无需手动维护 conversation_history 拼接）
  - human-in-the-loop（在某节点暂停等待用户确认，例如纪律严重违规时）

#### Long-term User Memory（长期）

- **载体**：复用现有 SQLite + 新增 `user_memory` 表
- **存储内容**：
  - 历史 DecisionResult（按 session 归档，含 timestamp、symbol、final_action、用户后续是否采纳）
  - 用户偏好沉淀（哪些 Agent 建议被频繁修改、哪些研报来源被偏好）
- **检索方式**：结构化查询（按 symbol、时间范围、intent），不引入向量化

### 4.6 MCP Server 暴露

新建独立项目 `wealthpilot-research-mcp`，暴露 3 个工具方法：

| MCP Tool | 用途 | 复用底层 |
|---------|------|---------|
| `fetch_news` | 按 symbol 拉取新闻 + 情绪 | AlphaVantageAdapter / AKShareAdapter |
| `fetch_fundamental` | 按 symbol 拉取基本面快照 | InfoRouter |
| `query_viewpoint_cards` | 查询已加工的 ViewpointCard | ViewpointRepository |

**部署形态**：本地 stdio MCP server，可在 Claude Desktop 配置中接入。面试 Demo 时现场展示一次调用。

---

## 五、Eval Harness 设计（核心差异化章节）

### 5.1 设计哲学

金融决策**没有唯一标准答案**——同一个问题"茅台还能拿吗"，"hold（继续持有）"和"trim（减一点）"都可能合理。因此：

- **L1 意图层**：可用 exact match，因为意图分类有标准答案
- **L2 Agent 调用层**：可用结构化对比，因为 Agent 调用序列有期望集合
- **L3 决策质量层**：必须升级到 LLM-as-judge + Rubric 评分，给出"接受范围"而非"唯一答案"

> **方法论传承**：这是从蚂蚁意图分类评测的 exact-match 范式，向金融决策评测的 rubric-based 范式的演进。这条演进路径本身就是面试时的差异化叙事。

### 5.2 三层评测指标

| 层级 | 评测对象 | 指标 | 评测方式 |
|------|---------|------|---------|
| **L1 意图识别** | RoutingPlan.intent 字段 | Top-1 准确率、混淆矩阵、各类意图 F1 | 自动（exact match） |
| **L2 Agent 调用** | agents_invoked 序列、all_tool_calls 序列、plan_source 分布、validation_log | Agent 选择 F1、调用顺序匹配率、LLM Planner 成功率、**Validator 通过率**、**重试率**、**降级率** | 自动（结构化对比） |
| **L3 决策质量** | 最终 DecisionResult | 决策类型符合率、引用准确率、幻觉率、依据充分性 | LLM-as-judge（GPT-4.1 + Rubric） |

#### L2 评测的多轮一致性指标（v1.3 新增）

单轮决策评测之外，**多轮场景下决策质量是否退化**是一个独立的评测维度。在多轮用例（multi-turn yaml cases）中评测：

| 指标 | 定义 | 期望 |
|------|-----|------|
| **决策类型收敛性** | 同一 symbol 在连续 N 轮内的 finalAction 序列是否合理收敛（不应该 buy_more → exit → buy_more 这样反复） | 反复跳变率 < 10% |
| **引用一致性** | 第 N 轮引用的 ViewpointCard 应是第 N-1 轮引用集合的扩展或子集，不应完全无关 | Jaccard 相似度 ≥ 0.5 |
| **置信度单调性** | 当用户在第 N 轮补充了 infoNeeded 中要求的信息后，第 N+1 轮的 confidence 应单调上升 | 上升率 ≥ 80% |
| **澄清后稳定性** | 通过 Clarify 路径补充信息后，后续决策不应再走 Clarify（除非用户提了新问题） | 二次澄清率 < 20% |

这一指标针对的盲区：单轮评测看不出"系统是否在多轮中保持一致的世界模型"。如果第 1 轮说"茅台基本面健康建议持有"，第 2 轮用户问"那要不要加仓"时却开始说"茅台基本面承压建议减仓"——这是单轮评测发现不了的严重退化。

#### Validator 相关评测（v1.3 新增）

DecisionValidator 本身的运行数据也是关键指标：

- **Validator 通过率**：首次通过 / 总决策数（目标 ≥ 90%）
- **重试成功率**：retry 后通过 / retry 总次数（目标 ≥ 80%）
- **降级率**：fallback 决策 / 总决策数（目标 ≤ 5%）
- **Validator 失败类型分布**：哪类校验项最常失败（指导 prompt 优化方向）

如果某个版本 Validator 通过率突然下降，说明 PositionDecisionAgent 的 prompt 或上游 Agent 输出质量有退化，是早期预警信号。

### 5.3 用例数据集结构

每个用例独立 yaml 文件，便于版本管理和扩展：

```yaml
# evals/cases/PD_001_茅台_持有判断.yaml
case_id: PD_001
category: PositionDecision
description: 用户对长期持有的核心股询问当前是否继续持有
intent: PositionDecision

input:
  user_query: "茅台还能拿吗？"
  context_setup:
    positions:
      - symbol: 600519:SH
        name: 贵州茅台
        weight: 0.15
        cost_price: 1500
        current_price: 1750
    cards_available:
      - symbol: 600519:SH
        event_type: fundamental_snapshot
        as_of: 2026-04-15

expected:
  L1_intent: PositionDecision
  L2_agents_invoked: [Orchestrator, Research, Discipline, PositionDecision]
  L2_tools_called_min: [query_viewpoint_cards, check_discipline_rules]
  L2_planner_acceptable_variants:        # 允许的 plan 变体
    - [Research, Discipline, PositionDecision]
    - [Research, Discipline, Allocation, PositionDecision]   # Planner 选择查偏离也合理
  L2_validator_expected: pass            # v1.3: 期望 Validator 一次通过
  L3_decision_type_in: [hold, trim]      # 接受范围
  L3_confidence_min: 0.6
  L3_must_cite_sources: [第三方数据]
  L3_must_not:
    - 编造未持仓信息
    - 推荐买入未在持仓中的标的
    - 给出超过 confidence 0.5 但不填 infoNeeded
```

**多轮用例样本（v1.3 新增）**：

```yaml
# evals/cases/MT_001_茅台_补充信息后置信度上升.yaml
case_id: MT_001
category: MultiTurn-PositionDecision
description: 第一轮置信度低且要求补充信息，第二轮用户提供后置信度应上升

turns:
  - turn: 1
    user_query: "茅台还能拿吗？"
    context_setup:
      positions: [...]
      cards_available: []   # 故意没有研究数据
    expected:
      L1_intent: PositionDecision
      L3_confidence_max: 0.5     # 期望低置信度
      L3_infoNeeded_required: true   # 必须填 infoNeeded
      L3_decision_type_in: [wait, need_info]
  
  - turn: 2
    user_query: "我刚看了券商研报，给的是买入评级，目标价 2000"
    context_inherit_from_turn: 1   # 继承第 1 轮 state
    expected:
      L3_confidence_min: 0.55      # 期望比第 1 轮上升
      L3_decision_type_in: [hold, buy_more, trim]   # 已经能给明确判断
      multi_turn_check:
        confidence_monotonic: true       # 置信度单调上升
        viewpoint_consistency_min: 0.5   # 引用 Jaccard 相似度
        decision_type_convergence: true  # 不能跳到完全相反方向
```

### 5.4 LLM-as-judge Rubric 设计

L3 评测的关键。设计 5 维评分量规（每维 0-2 分，总分 10 分）：

| 维度 | 评分标准 |
|------|---------|
| **决策一致性**（2分） | 决策类型是否落在 L3_decision_type_in 集合内 |
| **引用真实性**（2分） | 引用的研报/纪律是否真实存在于 context，无幻觉 |
| **依据充分性**（2分） | rationale 是否覆盖关键论据，且不超过 3 条精炼条目 |
| **风险完备性**（2分） | riskPoints 是否覆盖关键风险，且与决策类型逻辑自洽 |
| **置信度合理性**（2分） | confidence 与 confidenceReason 是否匹配，低置信度时是否填了 infoNeeded |

**Judge Prompt 关键约束**：
- 给 Judge 完整的 context（用户输入 + Agent 各阶段输出）
- Judge 返回 JSON 格式：`{score_per_dim, total_score, reasoning, flags}`
- 多次运行取平均（至少 3 次），降低 Judge 自身波动

### 5.5 报告产出

每次 PR 自动跑全量评测，输出：

- `eval_report.html`：版本对比看板，含 L1/L2/L3 各层得分变化、回归用例 pass/fail 列表、关键退化用例的 diff
- `eval_report.md`：精简版，便于在 PR description 中粘贴
- 历史评测结果按 commit hash 归档，支持纵向对比

---

## 六、范围与里程碑

### 开发顺序总则（v1.2 新增）

```
M0 (评测基线先行) → M1 + M2 (架构主体重构) → M3 (评测体系建立) → M4 + M5 (能力暴露与 Memory) → M6 (面试材料)
```

**核心原则：评测先行，不一上来重构 LangGraph。** M0 把现有 18 个回归用例 yaml 化（半天工作量），既给 M1 重构提供"防退化基线"，也给 M3 评测体系的设计提供对齐起点。M1 和 M2 可适度并行（同一开发者也可串行），M3 必须等 M1/M2 完成才能跑出有意义的对比指标。

### 6.1 In Scope（本期做）

| 模块 | 工作 | 工作量 |
|-----|------|-------|
| **M0：用例 yaml 化** | 18 个回归用例迁移到 yaml 格式（M1 启动前先做） | 0.5 天 |
| **M1：LangGraph 重构 + LLM Planner + Validator** | 7 个 Agent 抽出（含 PreCheck + Signal）+ StateGraph 编排 + LLM Planner（含 fallback 路由）+ checkpointer 接入 + **DecisionValidator 节点** | 3.5 天 |
| **M2：Tool 抽象层（含 Agent 自主调用）** | 7 个核心 Tool 定义（4 静态 + 3 动态）+ JSON Schema + Function Calling 改造（≥2 个 Tool 由 Agent 自主调用） | 1 天 |
| **M3：Eval Harness** | L1/L2/L3 三层评测脚本 + Judge Rubric + HTML 报告 + 用例集扩到 30 个 + **多轮一致性指标 + Validator 通过率指标（v1.3 新增）** | 2.5 天 |
| **M4：MCP Server** | wealthpilot-research-mcp 独立项目 + 3 个工具方法 + Claude Desktop 接入验证 | 1 天 |
| **M5：双层 Memory** | LangGraph checkpointer 配置 + user_memory 表 + 历史决策检索 | 0.5 天 |
| **M6：面试材料** | 架构图（含 mermaid + 高保真版）+ Eval 报告样例 + 5 分钟 demo 录屏 + 1 页项目描述 | 1 天 |

**总工作量：10 天**（v1.3 是 9.5 天，v1.4 增加 0.5 天用于 M1 多两个 Agent 的拆分：PreCheckAgent + SignalAgent。M0 已完成不计入）

### 6.2 Out of Scope（本期不做）

- 用户画像动态化、首页重构、投资记录、收益分析、截图 OCR、Telegram Bot、资讯自动化（n8n）
- LangChain 引入、向量化 RAG 升级、量化策略、移动端
- 已有的 ViewpointCard / EntityRegistry / Adapter 架构改造
- 完全自由的 LLM Planner（不限制 Agent 选择空间）

### 6.3 阶段完成标准

**M1 完成标准**：
- [ ] 三个核心场景在 LangGraph 上跑通（场景 A/B/C 各至少 3 个用例）
- [ ] LLM Planner 在 M0 的 18 个用例上成功率 ≥ 80%（其余 ≤20% 走 fallback）
- [ ] 18 个回归用例 pass 率 ≥ 18/18（不退化）
- [ ] 现有 SSE 流式输出兼容，前端无破坏性改动
- [ ] **DecisionValidator 节点接入完成，所有 PositionDecision 输出都经过 Validator**
- [ ] **Validator 重试机制工作正常，至少在 3 个故意构造的"幻觉用例"上能拦截并触发重试**
- [ ] **Validator 降级机制工作正常，纪律严重违规但 LLM 仍给激进决策的场景能强制降级**
- [ ] **PreCheckAgent 接入完成，数据缺失场景能在 LLM call 前拦截（v1.4 新增）**
- [ ] **SignalAgent 接入完成，4 维信号（仓位/事件/基本面/情绪）正确输出到 state（v1.4 新增）**
- [ ] **`infer_target_from_holdings` Tool 实现完成，覆盖 profit_loss / weight / category 三类筛选维度（v1.5 具体化）**
- [ ] **Top-1 显著性判断工作正常：用 PD_001/PD_002 验证 mode=single 路径，用 fixture 构造的"近似浮盈"持仓验证 mode=multi 路径**
- [ ] **PD_003 退化 bug 修复：意图明确（confidence ≥ 0.8）但 asset 为空的场景，必须走 weight 筛选而非退化到 Education 通用回答**

**M2 完成标准**：
- [ ] 7 个 Tool 全部完成 JSON Schema 定义
- [ ] 至少 2 个 Tool（`fetch_realtime_research` 和 `web_search`）由 Agent 通过 Function Calling 自主调用
- [ ] Tool 调用全量记录到 state.all_tool_calls

**M3 完成标准**：
- [ ] 30 个 yaml 用例（18 个 M0 迁移 + 12 个新增，覆盖三个场景）
- [ ] **5 个多轮一致性用例（v1.3 新增）**
- [ ] L1/L2/L3 评测脚本可一键运行
- [ ] HTML 报告可在浏览器打开，含版本对比
- [ ] **报告包含 Validator 通过率、重试率、降级率三个指标（v1.3 新增）**
- [ ] **报告包含多轮一致性指标（决策类型收敛性、引用一致性、置信度单调性）（v1.3 新增）**
- [ ] Judge 评分 3 次平均的 std < 0.5（说明评分稳定性）
- [ ] 跑出 v2.5.1 基线分数 vs v2.6 升级后分数的对比

**M6 完成标准**：
- [ ] 架构图清晰展示 7 Agent + LLM Planner + LangGraph + Tool Layer + Memory + MCP
- [ ] Demo 录屏完整覆盖场景 A 的端到端流程
- [ ] 项目描述 1 页，含技术栈、核心架构、量化指标三部分

---

## 七、面试展示章节（独立可用）

> 本章可独立提取，作为面试时的项目讲解脚本。

### 7.1 一句话定位（v1.1 升级）

WealthPilot 是一个**面向 A/H/美股个人投资者的 LLM-Planned Multi-Agent 财富决策系统**。它的核心不是"AI 能不能给出投资建议"，而是回答了一个更难的问题——**怎么定义一个 AI 给出的金融决策是好决策**。围绕这个问题，我设计了三层评测体系（L1 意图 / L2 Agent 调用 / L3 LLM-as-judge Rubric），并基于 LangGraph 把决策路径从硬编码的 if-else 升级为 LLM 动态规划的 DAG。

### 7.2 5 分钟 Demo 脚本（场景 A，v1.1 升级）

| 时间 | 内容 |
|-----|------|
| **0:00-0:45** | **开场钩子**："我先问大家一个问题——同样是 AI 给的金融建议，你怎么判断哪个是好建议？这不像客服意图分类有标准答案，'茅台该不该卖'根本没有唯一对错。WealthPilot 这个项目的起点就是回答这个问题。" |
| **0:45-1:30** | 用户输入"茅台还能拿吗？"，前端展示 SSE 流式输出过程 |
| **1:30-2:30** | 切到架构图，讲清 LangGraph 路径：**展示 LLM Planner 输出的 RoutingPlan JSON**——强调"路径不是写死的，是 LLM 根据用户输入实时规划的 DAG"；接着展示 Research Agent 内部的 Function Calling 决策（"本地卡够不够？要不要拉实时？") |
| **2:30-3:00** | **DecisionValidator 演示**：故意构造一个让 LLM 输出"引用了不存在的研报"的边界用例，演示 Validator 拦截 → 重试 → 修正的全过程。强调"Eval 是事后分析，Validator 是运行时门禁，两者不能互相替代" |
| **3:00-4:00** | 切到 Eval 报告，讲清 L1/L2/L3 三层指标，**重点讲 L3 LLM-as-judge 的 5 维 Rubric 设计**——这是从蚂蚁意图分类评测的 exact-match 方法论，向金融决策评测的 rubric-based 方法论的演进 |
| **4:00-4:30** | 切到 MCP Desktop，现场触发一次 `query_viewpoint_cards` 调用，展示能力可被外部 Agent 复用 |
| **4:30-5:00** | 总结技术差异化：**LLM Planner 而非硬编码路由 + Agent 自主 Tool Calling + 运行时 Validator + 离线 Eval Rubric + 风控不交给 LLM** |

### 7.3 高频面试问题预答（v1.1 升级）

**Q1（升级版）：你这个用 LangGraph，不就是把 pipeline 拆成 Agent 吗？用普通 Python 代码不行吗？**  
A：完全可以用普通代码写，问题是写出来就是一个 mini LangGraph，不如直接用行业标准词汇沟通。具体看 LangGraph 提供的三个能力，普通代码做都得自己实现：
- **State Persistence**：用户对话中断重启，要从断点恢复——普通代码要自己实现 session checkpoint
- **Node-level Trace**：每个 Agent 输入输出自动记录，Eval Harness 直接消费这份 trace——普通代码要自己埋点
- **Conditional Edges + Human-in-the-loop**：纪律严重违规时暂停等用户确认——普通代码做这个要重写整个流程控制

而且更关键的是，本期的 Orchestrator 是 **LLM Planner**——它输出的是动态的 DAG，不是固定路径。这种"运行时决定执行图结构"的场景，LangGraph 的图抽象天然适配；用普通代码反而要自己造一个 DAG 调度器。

**Q2：LLM Planner 听起来不稳定，怎么保证可靠？**  
A：约束式 Planner，三层保障：
1. **选择空间约束**：Planner 只能从 7 个已知 Agent 中选，不会调用不存在的能力
2. **结构化输出**：Pydantic 校验 RoutingPlan，校验失败立即降级
3. **失败降级**：Pydantic 校验失败 / 超时 / 返回空，降级到 fallback 路由表（按 5 类 intent 走预定义路径）

每次 Eval 时，state 里的 plan_source 字段会标记是 LLM Planner 还是 fallback。我们持续监控 LLM Planner 成功率，本期目标 ≥ 80%。

**Q3：Agent 拆分依据是什么？为什么是 7 个不是 3 个或 10 个？**  
A：拆分依据是"职责单一 + 输入输出可序列化 + 失败可独立 retry"。7 个 Agent 对应 6 类真实能力（编排 / 研究 / 数据完整性门禁 / 4 维信号生成 / 纪律校验 / 配置 / 综合决策）+ 1 个综合决策。

意图识别没拆出来是因为它就是 Orchestrator Planner 的核心输出；持仓解析没拆出来是因为它没有 LLM 推理，应该作为 Tool 而非 Agent。

**两个特殊设计值得展开**：

1. **DisciplineAgent 是纯函数 Agent**——这是金融场景的关键差异点：风控规则不能交给概率模型概率化处理（呼应产品哲学"风控不交给 LLM"）

2. **SignalAgent 是 Multi-Agent 中的隐藏王牌**——它把"持仓数据 + 研报 + 规则结果"这种异构信息加工成 4 维结构化信号（仓位/事件/基本面/情绪）才送给 LLM。LLM 不擅长加工原始数据，但擅长基于结构化信号合成判断。这种"脏活给规则，合成给 LLM"的能力边界划分，比"全部喂给 LLM"准确率高很多

**Q4：怎么知道你的 Agent 系统比上一版好？**  
A：三层 Eval Harness。L1 意图分类 exact match 看分类准确率，L2 看 Agent 调用序列 F1 + LLM Planner 成功率，L3 用 LLM-as-judge + 5 维 Rubric 评决策质量。每个 PR 自动跑全量评测，HTML 报告对比版本分数。这套方法是从蚂蚁意图分类评测的方法论延伸——蚂蚁是 exact match 就够了（意图分类有唯一答案），但金融决策没有唯一答案，所以升级到 rubric-based + LLM-as-judge。

**Q4.5：那运行时怎么保证每次决策不出问题？Eval 是事后跑的吧？**  
A：好问题。这正是 Eval 解决不了的——Eval 是离线分析整体趋势，但生产环境每一次决策都需要运行时门禁兜底。所以我在 PositionDecisionAgent 后加了一个 DecisionValidator 节点，纯函数脚本，对每个 DecisionResult 跑一组确定性检查：

- **Schema 完整性**：7 个必填字段非空
- **防幻觉**：rationale 引用的 symbol 和 ViewpointCard 必须真实存在于上下文
- **置信度-信息一致性**：confidence < 0.5 时必须填 infoNeeded
- **纪律-决策一致性**：discipline_violations 含 high severity 时不能输出 buy_init / buy_more

校验失败先 retry（带具体错误反馈给 prompt），还失败就强制 fallback 到 wait + need_info。所以 Validator 是运行时门禁，Eval 是离线分析，两者互补不可替代。这个设计哲学其实是从软件工程的"代码审查 vs 单元测试"类比过来的——审查是流程，测试是门禁。

**Q5：决策的可解释性怎么保证？**  
A：四个层面。
1. **DecisionResult schema 强制要求** rationale + riskPoints + confidence + evidenceSources + infoNeeded 五个字段
2. **ViewpointCard 三层架构**（Facts / Narrative / Judgment）保证每条引用都可追溯到原始数据 + as_of 时间
3. **LangGraph 的 state trace** 让整个决策链路的每一步输入输出都可回放
4. **LLM Planner 的 rationale 字段** 强制 LLM 解释为什么选择这个调用路径——这是用户能看到的"AI 怎么想的"

**Q6：和通用 RAG 投顾 Agent 的差异？**  
A：四个差异。
1. **结构化过程而非黑盒生成**——决策路径走哪几个 Agent、调用哪些 Tool 全部显式可控
2. **纪律引擎是确定性规则**——风控不交给 LLM，由 11 条 hard rule 强制约束
3. **评测体系定义了"什么叫好"**——不是"能输出就行"而是 rubric 量化
4. **LLM 在两个层级动态决策**——Orchestrator 决定调用哪些 Agent，Agent 内部决定调用哪些 Tool；不是死的 pipeline

**Q7（蚂蚁经验引导）：你之前在蚂蚁做过评测体系吗？怎么迁移过来的？**  
A：在蚂蚁做的是智能客服意图分类的评测体系——意图有标准答案，评测核心是 annotation 一致性 + 模型预测准确率，方法是 exact match。迁移到金融决策场景时遇到一个关键问题：**金融决策没有唯一答案**。所以保留了"分层评测"的方法论（L1/L2/L3），但 L3 必须从 exact match 升级到 rubric-based LLM-as-judge。这个演进过程本身就是一个有意思的方法论问题——**评测设计要服从被评测对象的特性，而不是套用同一种范式**。

---

## 八、风险与缓解

| 风险 | 影响 | 缓解措施 |
|-----|------|---------|
| LangGraph 重构破坏现有 18 个回归用例 | 高 | M0 先做 yaml 化，M1 期间每天跑回归；保留 v2.5.1 分支作为 fallback |
| LLM Planner 不稳定，Pydantic 校验失败率高 | 中 | 三层保障（约束 + 校验 + 降级）；持续监控 plan_source 分布，校验失败率 > 20% 触发 prompt 调优 |
| LLM-as-judge 评分波动大 | 中 | 多次运行取平均（≥3 次）；rubric 维度足够细（5 维度而非 1 个总分）；定期人工抽样校准 |
| Function Calling 在中文场景下意图识别准确率下降 | 中 | 保留现有的中文 intent_recognizer 作为前置参考信号传给 LLM Planner，不是替代 |
| MCP 协议本身不稳定 | 低 | MCP 已是相对稳定的标准；本期只暴露 3 个工具，复杂度低 |
| 面试官不熟悉 LangGraph 反而要求讲底层原理 | 低 | 准备 30 秒"LangGraph 本质就是一个 state machine + conditional edges + checkpointer"的极简解释 |

---

## 九、3.0 后续规划展望

本期是技术架构升级。**业务功能补齐**留给 3.0：

- 用户画像动态化（替换硬编码配置）
- 投资记录 + 收益分析（决策闭环）
- 首页重构（决策工作台视角）
- 截图 OCR 持仓导入
- Telegram Bot 推送层
- 资讯自动化（n8n + Feed MCP）

3.0 的工作可以在 2.6 的 LLM-Planned Multi-Agent 架构上自然扩展：每个新功能对应一个新 Agent 或新 Tool，不需要重做架构。

---

## 十、附录

### 10.1 术语表

| 术语 | 释义 |
|-----|------|
| Agent | 一个有明确职责、可独立调用、输入输出结构化的处理单元；可以是 LLM 推理也可以是纯函数 |
| LLM Planner | 由 LLM 在运行时生成调用图的编排器，区别于硬编码路由 |
| 约束式 Planner | LLM Planner 的工程化版本：选择空间限定 + 结构化输出 + 失败降级 |
| Tool | Agent 可调用的能力单元，有 JSON Schema 描述，通过 Function Calling 协议调用 |
| 系统调度调用 | Tool 由 LangGraph 节点确定性调用，参数由 state 传入 |
| Agent 自主调用 | Tool 由 Agent 在运行时通过 LLM Function Calling 决定是否调用及调用参数 |
| LangGraph | LangChain 生态中的 Agent 编排框架，核心是 StateGraph（状态图） |
| StateGraph | 由节点（Agent）和边（路由规则）组成的有状态图结构 |
| Checkpointer | LangGraph 提供的状态持久化机制，用于 session 级中断恢复 |
| MCP | Model Context Protocol，Anthropic 推出的 LLM 工具调用协议标准 |
| Eval Harness | 评测框架的总称，含数据集、运行器、指标计算、报告生成 |
| LLM-as-judge | 用 LLM 评判其他 LLM 输出质量的评测方法 |
| Rubric | 评分量规，把主观评判拆成多个客观维度 |

### 10.2 关联文档

- `WealthPilot_2_0_产品优化与功能升级_v1_2.md`：被本文档显式弃用 P0 部分
- `CHANGELOG.md`：v2.5.1 当前实现状态
- 后续待写：`Multi_Agent_Engineering_Spec.md`（M1 启动前）、`Eval_Harness_Spec.md`（M3 启动前）、`Demo_Script.md`（M6 产出）

---

*v1.5 — 2026-04-29，基于 M1-Step1 验证（脚本实测当前代码遇模糊输入的真实路径），对 v1.4 的产品哲学描述做精细化校准——把"主动推断"细化为"明确时直选 Top-1、模糊时返回候选清单"两级路径，infer_target_from_holdings Tool 设计具体化（含 weight 筛选维度 + 显著性阈值判断 + 三种 mode 返回结构），M1 完成标准加 PD_003 退化 bug 修复验收。M0 + Step1 已完成，进入 M1 主体重构。*
