# WealthPilot

个人智能投顾系统 — 资产配置 · 投资决策 · 纪律管理

**当前版本：v2.4.0**

## 项目简介

WealthPilot 是一个面向个人投资者的本地化智能财富管理平台。
帮助用户统一管理多平台资产，通过 AI 大模型提供自然语言投资决策支持，
并以规则引擎 + LLM 双引擎驱动投资纪律约束。

v2.0 完成了从 Streamlit 单体到 **React 19 + FastAPI 前后端分离**架构的完整迁移，
四个核心模块全部落地生产可用状态。

## 核心功能

| 模块 | 功能描述 | 状态 |
|------|----------|------|
| 投资账户总览 | 多账户持仓聚合、资产分布图表、净值/盈亏展示、AI综合分析报告 | ✅ v2.0 |
| 投资纪律 | 规则引擎 + 心理偏差检测、实时行为评估、手册管理 | ✅ v2.0 |
| 投研观点 | 观点库管理、文档解析工作流（URL/PDF/文本）、LLM提炼 | ✅ v2.0 |
| 投资决策 | SSE 流式 AI 对话、七模块 ExplainPanel、多轮会话、智能标的澄清 | ✅ v2.4 |
| 资产配置 | 五大类配置管理、AI 对话式配置方案、纪律校验 | ✅ v2.3 |
| 用户画像与投资目标 | 风险偏好 · 投资目标 · 持仓截图解析 · 多维度画像 | ✅ v2.1 |
| 养老规划 | 退休现金流测算与缺口分析 | 🚧 规划中 |
| 资产负债总览 | 个人 / 家族全景 | 🚧 规划中 |

## 技术栈

### 前端
- **React 19** + **Vite** + **TypeScript**
- **Tailwind CSS v4**
- **Recharts**（资产分布饼图）
- **ReactMarkdown** + remark-gfm（AI 对话渲染）
- **Lucide React**（图标）

### 后端
- **Python 3.11**
- **FastAPI** + **uvicorn**（RESTful API + SSE 流式接口）
- **SQLAlchemy ≥ 2.0**（ORM，SQLite）
- **OpenAI ≥ 1.20**（LLM 调用）
- **Perplexity API**（联网投研搜索，OpenAI 兼容接口）

### 基础模型

| 功能场景 | 模型 | 说明 |
|---------|------|------|
| 意图识别 | gpt-4.1 | 用户输入 → 意图分类 + 实体提取 + 置信度 |
| 投资决策（主对话） | gpt-4.1 | 六步管道终端推理，生成 7 档结论 + chat_answer |
| 组合评审 / 通用对话 | gpt-4.1-mini | PortfolioReview / GeneralChat 等非核心意图 |
| 投研卡片解析 | gpt-4.1-mini | PDF / URL / 文本 → 结构化 ResearchCard |
| 投研蒸馏 | gpt-4.1-mini | ResearchCard → 3~5 条浓缩要点 |
| 联网投研搜索 | sonar-pro / gpt-4o-search-preview | Perplexity 优先，未配置时降级 OpenAI |
| 持仓截图解析 | gpt-4o | 银行 / 券商截图 OCR → 结构化持仓 |
| 用户画像（图片） | gpt-4.1 | 风险偏好问卷截图 → 画像字段提取 |
| 用户画像（文本） | gpt-4.1-mini | 文本输入 → 画像字段提取 |
| 持仓报告 | gpt-4.1-mini | 资产总览页 AI 综合分析 |
| 告警解读 | gpt-4.1-nano | 单条偏离告警的简要解读 |

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

## 目录结构

```
WealthPilot/
├── frontend/                    # React SPA（v2.0 新增）
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
├── backend/                     # FastAPI 服务层（v2.0 新增）
│   ├── main.py                  # 应用入口，路由挂载
│   ├── api/
│   │   ├── portfolio.py         # 持仓/负债/告警/导入接口
│   │   ├── discipline.py        # 纪律规则/手册/评估接口
│   │   ├── research.py          # 观点/文档/卡片接口
│   │   └── decision.py          # SSE 对话/Explain/会话接口
│   └── services/                # 业务逻辑层，对接核心引擎
│
├── decision_engine/             # 投资决策引擎（v1.X 沿用，持续迭代）
│   ├── data_loader.py           # 持仓 + 投研数据加载（LLM 语义匹配）
│   ├── llm_engine.py            # LLM 决策生成（7 档结论 + confidence）
│   ├── rule_engine.py           # 规则前置检查
│   ├── signal_engine.py         # 4 维度信号生成
│   ├── pre_check.py             # 决策前置校验
│   ├── decision_flow.py         # 决策流程编排
│   └── types.py
│
├── intent_engine/               # 意图识别引擎（v1.X 沿用）
├── app/                         # 核心业务逻辑（v1.X 沿用）
│   ├── models.py                # SQLAlchemy ORM 模型
│   ├── database.py              # 数据库基础设施
│   ├── analyzer.py              # 持仓分析引擎
│   ├── discipline/              # 纪律子模块
│   └── ...
│
├── streamlit_app.py             # 旧版入口（v1.X 保留，已不维护）
├── data/                        # 运行时数据（handbook 等）
├── docs/                        # 设计文档归档
├── tests/                       # 单元测试
├── requirements.txt
└── .env.example
```

## 架构说明

### 投资决策链路（v2.0）

```
用户输入（浏览器）
  → POST /api/decision/chat（SSE 流式）
  → backend/services/decision_service.py
  → intent_engine（意图识别 · 多标的分发）
  → decision_engine/data_loader（持仓加载 · LLM 语义匹配 · 投研提炼）
  → decision_engine/rule_engine（纪律规则前置检查）
  → decision_engine/signal_engine（4 维度信号）
  → decision_engine/llm_engine（GPT-4.1 生成 7 档决策结论 + confidence + chat_answer）
  → SSE 推流 → Decision.tsx ExplainPanel 实时渲染
```

### 决策结论 7 档

| 档位 | 含义 |
|------|------|
| buy_init | 新建仓 |
| buy_more | 加仓 |
| hold | 观望 |
| trim | 减仓 |
| exit | 清仓 |
| wait | 等待信号 |
| need_info | 信息不足 |

### ExplainPanel 七模块

投资决策右侧面板按以下顺序展示完整决策依据链路：

1. **识别意图** — 意图类型 / 目标资产 / 操作方向 / 置信度
2. **持仓数据** — 当前仓位权重 / 盈亏 / 持仓平台
3. **纪律校验** — 规则通过/违规、规则明细
4. **投研观点** — 用户录入观点 + 联网参考（可折叠）
5. **市场信号** — 仓位 / 事件不确定性 / 基本面 / 情绪
6. **AI 推理过程** — LLM reasoning 条目（默认折叠）
7. **最终结论** — 决策档位 + 结论摘要 + 策略/风险要点

## 版本历史

详见 [CHANGELOG.md](CHANGELOG.md)。

**近期主要版本：**
- **v2.4.0**（2026-04-10）：决策对话策略优化 Phase 2 — 多轮持久化 · 智能标的澄清 · 7 档结论 · 并行投研搜索
- **v2.3.0**（2026-04-06）：资产配置模块 V1 — 五大类配置管理 · AI 对话 · 纪律校验
- **v2.2.0**（2026-04-05）：决策 I/O Contract v1.0 — 结构化输入/输出改造
- **v2.1.0**（2026-04-04）：用户画像模块重构 — 单页双模态 · 图片解析 · 本地冲突校验
- **v2.0.0**（2026-04-04）：全栈重写，React+FastAPI，四核心模块完整落地，1.X 封板

## 开发工作流

```
feat/xxx 分支 → 实现 + 测试 → PR → merge master → tag vX.Y.Z
```

后续迭代版本号：**2.X**（基于 React+FastAPI 新架构）

## 许可证

MIT License
