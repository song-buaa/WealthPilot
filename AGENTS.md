# AGENTS.md

> WealthPilot 项目级 AI Agent 协作说明
> 当前版本：v3.0.0 | 最后更新：2026-05-03

本文件遵循 [AGENTS.md 开放标准](https://agents.md/)，为 AI 编程助手（Claude Code / Cursor / Cline 等）提供项目上下文与协作约定。

## 项目概述

WealthPilot 是 AI 驱动的个人投资决策工作台。本地运行，数据私有，帮助个人投资者从"凭直觉"过渡到"系统化决策"。

核心能力覆盖 5 种意图：
- PositionDecision：单标决策（买入/持有/减仓/止损/止盈）
- PortfolioReview：组合健康度全面评估
- AssetAllocation：资产配置方案生成
- PerformanceAnalysis：收益表现归因分析
- Education：投资知识科普 / 通用对话

## 当前架构（v3.0）

WealthPilot v3.0 是 Multi-Agent + Skills 协议架构，参考蚂蚁 agentUniverse PEER 模式与 Anthropic Skills 开放标准。

### 4 Agent（PEER）

| Agent | 职责 |
|-------|------|
| PlanningAgent | 意图识别 + Skill 选择 + 路由 |
| ExecutingAgent | 数据加载 + 信号生成 + 纪律校验 |
| ExpressingAgent | LLM 流式输出（唯一 AsyncGenerator）|
| ReviewingAgent | 硬校验 + LLM 评分（0-1 + retry/fallback）|

代码位置：`backend/agents/`

### 10 个 Skill

原子 Skill（9 个）：
- 数据获取：wp-fetch-holdings / wp-fetch-research / wp-check-discipline
- 计算分析：wp-calc-allocation-deviation / wp-generate-signals / wp-propose-allocation
- LLM 推理：wp-reasoning（参数化 prompt 模板）
- 输出规范：wp-citation-rules / wp-output-validator

组合 Skill（1 个）：
- wp-load-context（封装 data_loader.load 168 行装配逻辑）

代码位置：`skills/wp-*/SKILL.md`

### 双轨并行（feature flag）

通过环境变量切换 v2.6/v3.0：
```bash
USE_V3_AGENTS=0  # 默认，走 v2.6 旧路径
USE_V3_AGENTS=1  # 启用 v3.0 4 Agent + Skills
```

v2.6 与 v3.0 m5 评测均 18/18 PASS，行为完全等价。
v2.6 路径将在 v3.0 经过完整手动测试后清理（v3.0 → v3.1）。

## 技术栈

### 前端（不变）
- React 19 + Vite + TypeScript
- Tailwind CSS v4 + shadcn/ui
- lucide-react 图标
- Zustand 状态管理

### 后端（v3.0）
- FastAPI + SQLAlchemy + SQLite
- LangGraph（StateGraph + checkpointer）
- OpenAI SDK（GPT-4.1 主模型 + GPT-4.1-mini 评分模型）
- Anthropic Skills 协议（10 个 SKILL.md）
- MCP 协议（盈米基金诊断 MCP 接入）

### 评测（M3）
- 18 个 yaml 用例（5 意图覆盖）
- L1（intent）/ L2（决策质量）/ L3（端到端）三层评测
- HTML 报告生成

## 目录结构（关键路径）

```
backend/
  agents/              v3.0 PEER 4 Agent + 数据契约 + Adapter
    contracts.py       4 个 dataclass（A2A 字段对齐）
    planning_agent.py  意图识别 + Skill Selector
    executing_agent.py 数据加载 + 信号 + 纪律
    expressing_agent.py LLM 流式输出
    reviewing_agent.py 硬校验 + LLM 评分
    adapters.py        M2 Tool Output → 业务对象转换
  skills/              SkillsLoader + invoke
  graph/               M2 Tool Layer + LangGraph + DecisionValidator
    tools.py           15 个 Tool 注册（call_tool 统一入口）
  services/
    decision_service.py    主入口（含 USE_V3_AGENTS 切换）
    decision_service_v3.py v3.0 4 Agent 协作链路
  mcp_client/          盈米 MCP 客户端
skills/                10 个 SKILL.md
decision_engine/       v2.6 核心引擎（data_loader / rule_engine / signal_engine / llm_engine）
app/                   ORM 模型 + 业务服务
m0/cases/              18 个 yaml 评测用例
docs/                  PRD + 设计文档 + 评测报告
data/                  SQLite + 纪律手册
```

## 重要约定

### 跑评测
```bash
# v2.6（默认）m5 端到端评测
AV_DEV_MOCK=1 python scripts/m5_e2e_18_cases.py

# v3.0 m5 评测
AV_DEV_MOCK=1 USE_V3_AGENTS=1 python scripts/m5_e2e_18_cases.py
```

### 重构硬底线
任何重构必须保证 m5 评测 18/18 不退化。这是项目的工程纪律——不容妥协。

### 启动开发环境
```bash
# 后端（默认走 v2.6）
cd backend && uvicorn main:app --reload

# 启用 v3.0
USE_V3_AGENTS=1 uvicorn main:app --reload

# 前端
cd frontend && npm run dev
```

### 数据隐私
- 所有用户数据存于本地 SQLite（`data/wealthpilot.db`）
- 不向云端上传任何持仓 / 交易数据
- 投研观点通过本地投研卡 + 第三方公开数据源（盈米 MCP / 联网搜索）获取

## 不要做的事

### 不要修改 v2.6 已稳定代码
- `decision_engine/`：核心 LLM 决策引擎，v2.6 已稳定
- `app/models.py`：ORM 模型
- 修改这些会破坏 v2.6 兜底路径，让用户体验受影响

### 不要绕开评测体系
任何代码改动必须跑 `m5_e2e_18_cases.py` 验证。直接改代码不跑评测是反模式。

### 不要在 ExecutingAgent 内部做硬编码业务逻辑
v3.0 设计是通过 `invoke_skill()` 调用——新增能力的正确做法是写新 Skill + M2 Tool，不是在 Agent 内部加 if-else。

### 不要修改 SKILL.md 的 frontmatter 字段名
`type` / `entry_point` / `tool_name` 等字段名是 SkillsLoader 的契约——改字段名等于破坏 invoke 机制。

### 不要往 git 提交本地配置
`.claude/` / `.claire/` / `data/*.db` 等已加入 .gitignore，不要 force add。

## 演进路径

### v3.1（近期）
- v2.6 死代码清理（删除 `_stream_position_decision` 等）
- 移除 `USE_V3_AGENTS` feature flag
- ReviewingAgent 真实重试（当前只发警告）
- prompt 抽离到 `prompts/*.md`（5 个意图独立文件）
- 接入 LangSmith 做结构化 trace + 可观测性

### v4.0（中期）
- 高级 Memory（Mem0 用户偏好 / 知识图谱标的关系）
- 多 Agent 真实 A2A 通信（跨进程能力扩展）
- 投资纪律完整 11 条（当前简化版仅检查纪律 3）

### 不在路线图（明确决策）
- Computer-Use / 自动下单：金融合规问题，AI 不应自动执行交易
- SaaS 化：违背"本地优先 / 数据私有"产品哲学

## 参考文档

- [README.md](./README.md)：用户视角的产品介绍 + 快速开始
- [CHANGELOG.md](./CHANGELOG.md)：完整版本变更历史（v2.0.0 → v3.0.0）
- [docs/](./docs/)：PRD + 架构设计 + 评测报告
