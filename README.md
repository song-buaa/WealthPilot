# WealthPilot

基于 LLM 的个人投资决策系统

**当前版本：v2.5.1**

## 项目简介

个人投资者在真实投资过程中，普遍面临一个核心问题：**信息、认知与执行是割裂的。** 持仓分散在多个平台看不清全貌，投研信息很多却难以直接作用于具体持仓，明知投资纪律却在关键时刻难以执行。

WealthPilot 围绕这一问题，构建了一个完整的投资决策闭环：以统一持仓视图作为决策上下文，通过 AI 对话将用户问题转化为结构化的决策过程，并以规则引擎持续约束投资行为。与一般 AI 投顾不同，它不只是聊天工具，而是把投资变成一个有上下文、有约束、有完整推理链路的决策过程。

## 设计理念

WealthPilot 不是一个"直接给结论"的 AI 投顾工具，而是强调三个核心原则：

- **基于真实持仓**：所有决策建立在用户当前资产状态之上，而不是脱离持仓的泛泛建议
- **过程可解释**：完整展示从意图识别到最终结论的决策链路，而不是黑盒输出
- **受纪律约束**：通过规则引擎在决策链路中前置校验投资纪律

本质上，它是一个 **LLM 负责推理、持仓状态提供上下文、投资规则提供约束** 的决策系统。

## 核心功能

WealthPilot 围绕投资全流程构建了六大核心能力，以投资决策为中心，投研、配置、持仓、纪律、画像协同支撑：

| 模块        | 功能描述                                                            | 状态     |
| --------- | --------------------------------------------------------------- | ------ |
| 投资决策      | SSE 流式 AI 对话、七模块 ExplainPanel、多轮会话、智能标的澄清、7 档结论                 | ✅ v2.4 |
| 投研观点      | 三层架构观点卡 · AV/AKShare 多数据源（美股·港股·A股）· 自动拉取 · 批量审核 · 跨市场合并 · 保鲜机制 | ✅ v2.5 |
| 资产配置      | 五大类配置管理、AI 对话式方案生成、纪律校验                                         | ✅ v2.3 |
| 投资账户总览    | 多账户持仓聚合、资产分布图表、净值/盈亏展示、AI 综合分析报告                                | ✅ v2.0 |
| 投资纪律      | 规则引擎 + 心理偏差检测、实时行为评估、手册管理                                       | ✅ v2.0 |
| 用户画像与投资目标 | 风险偏好 · 投资目标 · 持仓截图解析 · 多维度画像                                    | ✅ v2.1 |
| 养老规划      | 退休现金流测算与缺口分析                                                    | 🚧 规划中 |
| 资产负债总览    | 个人 / 家族全景                                                       | 🚧 规划中 |

## 技术栈

### 前端

- **React 19** + **Vite 8** + **TypeScript**
- **Tailwind CSS v4**
- **React Router v7**（客户端路由）
- **Zustand v5**（状态管理）
- **Recharts**（资产分布图表）
- **ReactMarkdown** + remark-gfm（AI 对话渲染）
- **Lucide React**（图标）

### 后端

- **Python 3.11**
- **FastAPI** + **uvicorn**（RESTful API + SSE 流式接口）
- **SQLAlchemy ≥ 2.0**（ORM，SQLite）
- **OpenAI ≥ 1.20**（LLM 调用）
- **AKShare**（港股 / A 股行情数据）
- **Perplexity API**（联网投研搜索，OpenAI 兼容接口）

## 架构说明

### 1. 整体处理流程

用户输入首先经过意图识别进行分流，在统一框架下按意图类型装载上下文、执行校验与推理策略，最终生成结构化结果并返回前端。

```
用户输入
  → 意图识别（分类 · 标的提取 · 置信度）
  → 上下文装载（持仓 / 用户画像 / 投研 / 市场数据，按意图按需加载）
  → 投资纪律校验（规则引擎，按意图触发）
  → 模型推理（不同意图走不同 Prompt 策略与 Pipeline）
  → 结构化结果生成（自然语言 + 结构化字段）
  → 前端展示（SSE 流式推送 · ExplainPanel 渲染）
```

这一架构使得系统在不同意图下既能保持统一框架，又能支持差异化处理策略。

### 2. 核心组件

系统通过以下核心组件，将用户问题转化为可解释、可约束的投资决策过程：

**意图识别**：对用户输入进行分类，识别出五类意图之一，同时提取目标标的、操作方向等实体信息，作为后续处理的路由依据。不同意图对应完全不同的处理路径。

**上下文加载**：根据意图类型，从持仓数据库、用户画像、投研知识库、市场行情等来源按需加载相关上下文，作为模型推理的输入。

**投资纪律校验**：基于规则引擎对当前操作意图进行前置校验，检查是否触发用户定义的投资纪律规则，校验结果作为推理上下文的一部分传入模型。

**模型推理**：根据意图类型选择对应的 Prompt 策略与推理 Pipeline，调用 LLM 生成决策结论或分析结果。不同意图的推理深度与输出格式存在显著差异。

**答案生成**：将模型输出解析为结构化结果，同时生成面向用户的自然语言答案，通过 SSE 流式推送至前端。

### 3. 五类意图处理路径

| 意图                  | 处理路径   | 说明                          |
| ------------------- | ------ | --------------------------- |
| PositionDecision    | 完整决策链路 | 最复杂，含纪律校验、信号生成、多步推理、7 档结论输出 |
| PortfolioReview     | 简化路径   | 基于持仓全貌的综合分析，偏总结性输出          |
| AssetAllocation     | 简化路径   | 基于五大资产类别的配置方案生成与纪律校验        |
| PerformanceAnalysis | 简化路径   | 持仓表现分析，偏数据解读与归因             |
| GeneralChat         | 轻量路径   | 通用投资问答，不加载持仓上下文             |

### 4. 示例：单标的决策链路（PositionDecision）

这是系统中最复杂、最完整的一类决策流程，用于展示系统在"有持仓、有规则约束"场景下的完整能力。

```
用户输入（浏览器）
  → POST /api/decision/chat（SSE 流式）
  → intent_engine（意图识别 · 多标的分发）
  → decision_engine/data_loader（持仓加载 · LLM 语义匹配 · 投研提炼）
  → decision_engine/rule_engine（纪律规则前置检查）
  → decision_engine/signal_engine（4 维度信号）
  → decision_engine/llm_engine（GPT-4.1 生成 7 档决策结论 + confidence + chat_answer）
  → SSE 推流 → Decision.tsx ExplainPanel 实时渲染
```

#### 单标的决策结论（7 档）

| 档位        | 含义   |
| --------- | ---- |
| buy_init  | 新建仓  |
| buy_more  | 加仓   |
| hold      | 观望   |
| trim      | 减仓   |
| exit      | 清仓   |
| wait      | 等待信号 |
| need_info | 信息不足 |

#### ExplainPanel（单标的场景）

投资决策右侧面板按以下顺序展示完整决策依据链路：

1. **识别意图** — 意图类型 / 目标资产 / 操作方向 / 置信度
2. **持仓数据** — 当前仓位权重 / 盈亏 / 持仓平台
3. **纪律校验** — 规则通过/违规、规则明细
4. **投研观点** — 用户录入观点 + 联网参考（可折叠）
5. **市场信号** — 仓位 / 事件不确定性 / 基本面 / 情绪
6. **AI 推理过程** — LLM reasoning 条目（默认折叠）
7. **最终结论** — 决策档位 + 结论摘要 + 策略/风险要点

### AI 模型接入

| 功能域  | 模型                                           | 用途                                            |
| ---- | -------------------------------------------- | --------------------------------------------- |
| 核心决策 | gpt-4.1                                      | 意图识别、六步管道终端推理、生成 7 档结论 + chat_answer          |
| 轻量对话 | gpt-4.1-mini                                 | PortfolioReview / GeneralChat / 投研观点加工 / 持仓报告 |
| 图像解析 | gpt-4o                                       | 银行 / 券商持仓截图 OCR → 结构化持仓；风险偏好问卷截图 → 画像字段       |
| 快速告警 | gpt-4.1-nano                                 | 单条偏离告警简要解读                                    |
| 联网搜索 | sonar-pro（Perplexity）/ gpt-4o-search-preview | 实时投研信息检索，Perplexity 优先，未配置时自动降级               |

### 外部数据源

| 数据源            | 覆盖市场   | 接入数据类型                 |
| -------------- | ------ | ---------------------- |
| Alpha Vantage  | 美股     | 新闻情绪、公司概况、财报（Earnings） |
| AKShare（东方财富）  | 港股、A 股 | 新闻、公司概况、历史行情           |
| Perplexity API | 全市场    | 联网实时投研搜索               |

## 目录结构

```
WealthPilot/
├── frontend/                    # React SPA
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx    # 投资账户总览
│   │   │   ├── Discipline.tsx   # 投资纪律
│   │   │   ├── Research.tsx     # 投研观点
│   │   │   ├── Decision.tsx     # 投资决策（SSE + ExplainPanel）
│   │   │   ├── Allocation.tsx   # 资产配置看板
│   │   │   ├── AllocationChat.tsx # 资产配置 AI 对话
│   │   │   └── UserProfile.tsx  # 用户画像
│   │   ├── components/
│   │   │   └── layout/          # AppLayout, Sidebar
│   │   ├── lib/
│   │   │   ├── api.ts           # 所有 API 调用封装（含 SSE streamDecisionChat）
│   │   │   └── fmt.ts           # 数字/货币格式化工具
│   │   └── store/
│   │       └── decisionStore.ts # 决策页面状态管理
│   ├── package.json
│   └── vite.config.ts           # Vite proxy → :8000
│
├── backend/                     # FastAPI 服务层
│   ├── main.py                  # 应用入口，路由挂载
│   ├── api/
│   │   ├── portfolio.py         # 持仓/负债/告警/导入接口
│   │   ├── discipline.py        # 纪律规则/手册/评估接口
│   │   ├── research.py          # 观点/文档/卡片接口
│   │   └── decision.py          # SSE 对话/Explain/会话接口
│   └── services/                # 业务逻辑层，对接核心引擎
│
├── decision_engine/             # 投资决策引擎
│   ├── data_loader.py           # 持仓 + 投研数据加载（LLM 语义匹配）
│   ├── llm_engine.py            # LLM 决策生成（7 档结论 + confidence）
│   ├── rule_engine.py           # 规则前置检查
│   ├── signal_engine.py         # 4 维度信号生成
│   ├── pre_check.py             # 决策前置校验
│   ├── decision_flow.py         # 决策流程编排
│   └── types.py
│
├── intent_engine/               # 意图识别引擎
├── app/                         # 核心业务逻辑
│   ├── models.py                # SQLAlchemy ORM 模型
│   ├── database.py              # 数据库基础设施
│   ├── analyzer.py              # 持仓分析引擎
│   ├── discipline/              # 纪律子模块
│   └── ...
│
├── streamlit_app.py             # 旧版入口（v1.X，已不维护）
├── data/                        # 运行时数据（handbook 等）
├── docs/                        # 设计文档归档
├── tests/                       # 单元测试
├── requirements.txt
└── .env.example
```

## 快速开始

### 1. 安装依赖

```bash
# Python 依赖
pip install -r requirements.txt

# 前端依赖
cd frontend && npm install
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入：
#   OPENAI_API_KEY      — GPT-4.1（投资决策 LLM）
#   PERPLEXITY_API_KEY  — 联网投研搜索（可选）
source .env
```

### 3. 启动应用

```bash
# 终端 1：启动后端
uvicorn backend.main:app --reload --port 8000

# 终端 2：启动前端
cd frontend && npm run dev
```

浏览器访问 **http://localhost:5173**

### 4. 运行测试

```bash
pytest
```

## 版本历史

详见 [CHANGELOG.md](CHANGELOG.md)。

**近期主要版本：**

- **v2.5.1**（2026-04-28）：A 股支持 · 观点库分页 · 标的下拉筛选 · 投研保鲜机制 · 跨市场合并优化
- **v2.5.0**（2026-04-28）：投研观点模块 v2 — 三层架构 · Alpha Vantage/AKShare 多数据源 · 自动拉取 · 批量审核 · 跨市场合并
- **v2.4.0**（2026-04-10）：决策对话策略优化 Phase 2 — 多轮持久化 · 智能标的澄清 · 7 档结论 · 并行投研搜索
- **v2.3.0**（2026-04-06）：资产配置模块 V1 — 五大类配置管理 · AI 对话 · 纪律校验
- **v2.2.0**（2026-04-05）：决策 I/O Contract v1.0 — 结构化输入/输出改造
- **v2.1.0**（2026-04-04）：用户画像模块重构 — 单页双模态 · 图片解析 · 本地冲突校验
- **v2.0.0**（2026-04-04）：全栈重写，React+FastAPI，四核心模块完整落地

## 许可证

AGPL-3.0 License
