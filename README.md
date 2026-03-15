# WealthPilot

个人资产配置与智能财富顾问系统 MVP。

## 项目简介

WealthPilot 帮助用户将分散在多个平台的资产和负债数据统一管理，自动生成资产负债全景分析，检测策略偏离与风险，并通过 AI 大模型提供自然语言解读报告。

**核心功能**：CSV 导入持仓/负债 → 资产全景分析 → 策略偏离检测 → AI 投顾报告

## 技术栈

- Python 3.11
- Streamlit ≥ 1.32（UI 框架）
- SQLAlchemy ≥ 2.0（ORM，SQLite）
- OpenAI ≥ 1.20（AI 报告，gpt-4.1-mini）
- Plotly ≥ 5.20（交互图表）
- Pytest（单元测试）

## 快速开始

**1. 安装依赖**
```bash
pip install -r requirements.txt
```

**2. 配置 API Key**
```bash
cp .env.example .env
# 编辑 .env，填入真实的 OPENAI_API_KEY
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
├── streamlit_app.py     # 应用入口（路由分发，35 行）
├── app/                 # 核心业务逻辑
│   ├── config.py        # 全局配置常量
│   ├── models.py        # SQLAlchemy ORM 模型
│   ├── database.py      # 数据库基础设施
│   ├── state.py         # 跨页面共享状态
│   ├── analyzer.py      # 分析引擎（资产负债表 + 偏离检测）
│   ├── csv_importer.py  # CSV 解析与导入
│   └── ai_advisor.py    # OpenAI 集成
├── app_pages/           # UI 页面层
│   ├── overview.py      # 资产全景
│   ├── import_data.py   # 数据导入
│   ├── strategy.py      # 策略设定
│   └── ai_analysis.py   # AI 分析报告
├── tests/               # 单元测试
├── data/                # 运行时数据（.gitignore 忽略 .db 文件）
├── static/              # 静态资源
├── .env.example         # 环境变量模板
├── requirements.txt
└── pytest.ini
```

## 版本历史

详见 [CHANGELOG.md](CHANGELOG.md)。

当前版本：**v1.4**（目录结构整理，建立正式 Git 工作流）

## 开发工作流

```
feat/xxx 分支 → 实现 + 测试 → PR → merge main → tag vX.Y.Z
```

版本用 Git tag 管理（`git tag v1.4.0`），不再用目录隔离历史版本。

## 许可证

MIT License
