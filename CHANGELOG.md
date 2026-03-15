# Changelog

All notable changes to the WealthPilot project will be documented in this file.

## [1.4.0] - 2026-03-15 - 目录整理与工作流规范化

### Changed
- 将 `M1_v1.4/` 内容提升至仓库根目录，消除嵌套版本目录
- 建立正式 Git 工作流：功能分支 → PR → `main` → tag
- 新增 `.gitignore`（排除 `.env`、`data/*.db`、`__pycache__` 等）
- 重写根目录 `README.md`（整合项目说明、架构图、开发工作流）
- 历史版本（v1.0 ~ v1.3）改用 Git tag 管理，不再用目录隔离

### Security
- `.env` 加入 `.gitignore`，确保 API Key 不进入版本控制

---

## [1.3.0] - 2026-03-14 - P2 质量提升

### Added
- 新增 `tests/test_analyzer.py` - 20 个单元测试，覆盖 `analyze_portfolio` 和 `check_deviations` 所有主要路径
- 新增 `pytest.ini` - Pytest 配置文件
- 新增导入二次确认对话框，防止误操作覆盖数据
- 持仓集中度使用 `"id:name"` 复合 key，修复同名资产覆盖 bug

### Fixed
- **同名资产集中度覆盖 bug** - 改用复合 key 避免同名资产互相覆盖
- 导入前增加确认提示，防止误操作丢失历史数据

### Changed
- `analyzer.py` - 持仓集中度 key 格式从 `name` 改为 `id:name`
- `app_pages/overview.py` - 表格展示适配新的 concentration key 格式
- `app/ai_advisor.py` - TOP5 传给 LLM 时剥掉 id 前缀

---

## [1.2.0] - 2026-03-14 - P1 工程化重构

### Added
- 新增 `app/database.py` - 数据库基础设施独立模块，职责单一
- 新增 `app/config.py` - 全局配置常量管理，统一管理硬编码参数
- 新增 `app/state.py` - 跨页面共享状态和缓存查询
- 新增 `app_pages/` 目录 - 按 multi-page 规范拆分页面：
  - `app_pages/overview.py` - 资产全景
  - `app_pages/import_data.py` - 数据导入
  - `app_pages/strategy.py` - 策略设定
  - `app_pages/ai_analysis.py` - AI 分析
- 新增 `requirements.txt` - 依赖管理
- 新增 `.env.example` - 环境变量模板

### Changed
- **重大重构** `streamlit_app.py` - 从 413 行精简到 35 行，只负责页面配置和路由分发
- `app/models.py` - 精简为只保留 ORM 定义，DB 基础设施迁移到 `database.py`
- `app/analyzer.py` - 硬编码常量改为引用 `config.py`
- `app/ai_advisor.py` - 硬编码常量改为引用 `config.py`
- 依赖关系完全单向，无循环依赖

---

## [1.1.0] - 2026-03-14 - P0 紧急修复

### Fixed
- **模块级副作用 bug** - `models.py` 中 `engine` 和 `SessionLocal` 改为懒加载，避免 import 时立即执行
- **session 泄漏 bug** - `page_strategy()` 使用 `try/finally` 包裹 session，确保异常时也能关闭
- **OpenAI 客户端初始化 bug** - `ai_advisor.py` 改为 `get_client()` 懒加载，增加 key 缺失检查
- **全局 DB 查询性能问题** - 顶层全局查询移入 `@st.cache_data` 缓存函数，避免每次刷新都查询

### Added
- 新增 `requirements.txt` - 依赖清单
- 新增 `.env.example` - 环境变量模板

---

## [1.0.0] - 2026-03-11 - MVP 初始版本

### Added
- CSV 导入持仓 / 负债数据
- 资产负债全景分析（总资产、净资产、各类资产占比、平台分布、持仓集中度）
- 策略偏离检测与风险告警（策略偏离 / 纪律触发 / 风险暴露）
- AI 自然语言解读报告（调用 OpenAI gpt-4.1-mini）
- 基础 Streamlit UI

### Architecture
- 四层分离架构：UI → 业务逻辑 → 数据层 → 外部服务
- 单文件实现（`streamlit_app.py` 约 450 行）
