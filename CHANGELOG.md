# Changelog

All notable changes to the WealthPilot project will be documented in this file.

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
- `pages/overview.py` - 表格展示适配新的 concentration key 格式
- `app/ai_advisor.py` - TOP5 传给 LLM 时剥掉 id 前缀

---

## [1.2.0] - 2026-03-14 - P1 工程化重构

### Added
- 新增 `app/database.py` - 数据库基础设施独立模块，职责单一
- 新增 `app/config.py` - 全局配置常量管理，统一管理硬编码参数
- 新增 `app/state.py` - 跨页面共享状态和缓存查询
- 新增 `pages/` 目录 - 按 multi-page 规范拆分页面：
  - `pages/overview.py` - 资产全景
  - `pages/import_data.py` - 数据导入
  - `pages/strategy.py` - 策略设定
  - `pages/ai_analysis.py` - AI 分析
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
- 初始文件结构：
  - `app/models.py` - ORM 模型 + DB 初始化（混合）
  - `app/csv_importer.py` - CSV 解析 + DB 写入（混合）
  - `app/analyzer.py` - 分析引擎
  - `app/ai_advisor.py` - OpenAI 调用
  - `streamlit_app.py` - UI + 路由 + 业务编排（~450 行）

---

## 重构总结

### 版本对比

| 指标 | v1.0 (原始) | v1.3 (重构后) |
|------|------------|--------------|
| `streamlit_app.py` | 413 行 | 35 行 |
| Python 模块数 | 5 个 | 11 个（职责各自单一） |
| 硬编码常量 | 散落 2 个文件 7 处 | 全部收归 `config.py` |
| DB 基础设施 | 混在 `models.py` | 独立 `database.py` |
| 单元测试 | 0 | 20 个（覆盖核心分析引擎） |
| import 副作用 | 3 处 | 0 |
| session 泄漏风险 | 1 处 | 0 |
| 同名资产 Bug | 存在 | 已修复 |
| 误操作防护 | 无 | 全量导入二次确认 |
| 总代码行数 | 1,035 | 1,441（含 275 行测试） |

### 技术栈

- Python 3.11
- Streamlit ≥1.32.0
- SQLAlchemy ≥2.0.0
- Pandas ≥2.0.0
- OpenAI ≥1.20.0
- Plotly ≥5.20.0
- Pytest（测试框架）

### M2 建议方向

1. **投顾引擎** - 基于 `check_deviations` 告警，自动生成再平衡建议
2. **Decision Log UI** - `DecisionLog` 表结构已就绪，添加记录和查看页面
3. **数据导出** - 支持导出 PDF / Excel 报告
4. **多组合支持** - 当前只有一个默认组合，架构已预留 `portfolio_id`
