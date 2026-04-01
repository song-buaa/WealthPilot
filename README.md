# WealthPilot

个人智能投顾系统 — 资产配置 · 投资决策 · 纪律管理

## 项目简介

WealthPilot 是一个面向个人投资者的本地化智能财富管理平台，帮助用户统一管理多平台资产，通过 AI 大模型提供自然语言投资决策支持，并以规则引擎 + LLM 双引擎驱动投资纪律约束。

**当前版本：v1.14.0**

## 核心功能

| 模块 | 功能描述 | 状态 |
|------|----------|------|
| 投资账户总览 | 多账户持仓聚合、资产分布图表、收益率展示 | ✅ 已上线 |
| 投资纪律 | 规则引擎 + 心理偏差检测，约束持仓行为 | ✅ 已上线 |
| 投资决策 | AI 多轮对话、6 档决策结论、多标的分发 | ✅ 已上线 |
| 投研观点 | 联网搜索 + 本地研报，LLM 提炼关键结论 | ✅ 已上线 |
| 养老规划 | 退休现金流测算与缺口分析 | ✅ 已上线 |
| 财务规划（购房/消费等） | 规划页面 | 🚧 开发中 |
| 资产负债总览 | 个人 / 家族全景 | 🚧 开发中 |

## 技术栈

- **Python 3.11**
- **Streamlit ≥ 1.32**（UI 框架，司南风格深色侧边栏）
- **SQLAlchemy ≥ 2.0**（ORM，SQLite）
- **OpenAI ≥ 1.20**（gpt-4.1-mini，投研搜索 / 语义匹配 / 报告生成）
- **Anthropic ≥ 0.86**（claude-sonnet-4 / claude-haiku-4.5，投资决策对话）
- **Plotly ≥ 5.20**（交互图表）
- **Pandas ≥ 2.0**（数据处理）
- **Perplexity API**（联网投研搜索，通过 OpenAI 兼容接口调用）

## 快速开始

**1. 安装依赖**
```bash
pip install -r requirements.txt
```

**2. 配置 API Key**
```bash
cp .env.example .env
# 编辑 .env，填入以下 Key：
#   OPENAI_API_KEY      — GPT-4.1-mini（投研 / 语义匹配）
#   ANTHROPIC_API_KEY   — Claude Sonnet/Haiku（投资决策对话）
#   PERPLEXITY_API_KEY  — 联网投研搜索（可选）
source .env
```

**3. 启动应用**
```bash
streamlit run streamlit_app.py
```

**4. 运行测试**
```bash
pytest
```

## 目录结构

```
WealthPilot/
├── streamlit_app.py         # 应用入口，路由分发（司南风格侧边栏）
├── app/                     # 核心业务逻辑
│   ├── config.py            # 全局配置常量
│   ├── models.py            # SQLAlchemy ORM 模型
│   ├── database.py          # 数据库基础设施
│   ├── state.py             # 跨页面共享状态
│   ├── analyzer.py          # 持仓分析引擎
│   ├── csv_importer.py      # CSV 持仓导入
│   ├── ai_advisor.py        # OpenAI 集成（基础报告）
│   ├── research.py          # 联网投研搜索
│   ├── fx_service.py        # 汇率服务
│   ├── platform_importers.py# 多平台导入适配
│   ├── bank_screenshot.py   # 银行截图解析
│   ├── discipline/          # 投资纪律子模块
│   │   ├── config.py        # DISCIPLINE_RULES 全局规则常量（唯一规则来源）
│   │   ├── engine_runner.py # 纪律引擎执行器
│   │   ├── risk_engine.py   # 风险规则引擎
│   │   ├── psychology_engine.py # 心理偏差检测
│   │   └── models.py        # 纪律相关数据模型
│   └── utils/               # 工具函数
│       └── position_aggregator.py
├── app_pages/               # UI 页面层
│   ├── overview.py          # 投资账户总览
│   ├── strategy.py          # 投资决策（AI 对话）
│   ├── discipline.py        # 投资纪律
│   ├── research.py          # 投研观点
│   ├── retirement_life.py   # 养老规划
│   ├── import_data.py       # 数据导入
│   └── placeholder.py       # 未实现页面占位
├── intent_engine/           # 意图识别与编排
│   ├── engine.py            # 意图引擎主入口
│   ├── intent_recognizer.py # LLM 意图识别
│   ├── orchestrator.py      # 意图编排器
│   ├── context_manager.py   # 多轮对话上下文管理
│   ├── subtask_runner.py    # 子任务执行
│   ├── output_renderer.py   # 输出渲染
│   └── types.py             # IntentEntities 等类型定义
├── decision_engine/         # 投资决策引擎
│   ├── data_loader.py       # 持仓数据 + 投研数据加载（含 LLM 语义匹配）
│   ├── llm_engine.py        # LLM 决策生成（6 档结论）
│   ├── rule_engine.py       # 规则前置检查
│   ├── signal_engine.py     # 信号生成
│   ├── pre_check.py         # 决策前置校验
│   ├── decision_flow.py     # 决策流程编排
│   └── types.py             # 决策相关类型
├── ui_components.py         # 可复用 UI 组件
├── tests/                   # 单元测试 + E2E 测试
├── data/                    # 运行时数据（handbook_official.md 等）
├── docs/                    # 设计文档归档
├── scripts/                 # 工具脚本
├── archive/                 # v1.0–v1.3 历史快照
├── .env.example             # 环境变量模板
├── requirements.txt
└── pytest.ini
```

## 架构说明

### 投资决策链路

```
用户输入
  → intent_engine（意图识别 · 多标的分发）
  → decision_engine/data_loader（持仓加载 · LLM 语义匹配 · 投研提炼）
  → decision_engine/rule_engine（纪律规则前置检查）
  → decision_engine/llm_engine（Claude Sonnet 生成 6 档决策结论）
  → app_pages/strategy.py（渲染对话 + 决策卡片）
```

### 决策结论 6 档

| 档位 | 含义 |
|------|------|
| BUY | 加仓 |
| HOLD | 观望 |
| TAKE_PROFIT | 部分止盈 |
| REDUCE | 逐步减仓 |
| SELL | 减仓 / 清仓 |
| STOP_LOSS | 止损离场 |

### 规则唯一来源

投资纪律规则统一由 `app/discipline/config.py` 的 `DISCIPLINE_RULES` 常量维护，决策引擎与纪律页面共用同一套标准，避免口径矛盾。

## 版本历史

详见 [CHANGELOG.md](CHANGELOG.md)。

**近期主要版本：**
- **v1.14.0**（2026-03-28）：决策结论扩展为 6 档 · 多标的同操作分发 · LLM 资产语义匹配 · 投研提炼重构
- **v1.13.0**（2026-03-24）：投资决策业务逻辑稳定封板，规则来源统一，模型 ID 修正
- **v1.12.1**（2026-03-23）：投资决策模块缺陷修复封板，Manus 测试回归 13/13 通过
- **v1.9.0**：投资纪律模块上线（规则引擎 + 心理偏差检测）
- **v1.7.0**：意图引擎上线，支持自然语言多轮对话

## 开发工作流

```
feat/xxx 分支 → 实现 + 测试 → PR → merge master → tag vX.Y.Z
```

## 许可证

MIT License
