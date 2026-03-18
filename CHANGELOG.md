# Changelog

All notable changes to the WealthPilot project will be documented in this file.

## [1.7.1] - 2026-03-19 - 投资纪律导航修复 + 资产配置图标准化

### Fixed - 投资纪律页面导航彻底重建

**根本原因：** Streamlit 1.32.x 中 `st.tabs()` 与 `st.radio` + 手写 CSS 均存在缺陷：
- `st.tabs()`：tab 选中状态存储在浏览器前端，任何 Python button 点击触发 rerun 后 tab 重置为第一页
- `st.radio` + CSS：CSS 选择器无法匹配 Streamlit 1.32.x 实际 DOM 结构，radio 圆圈仍然显示，标签换行，视觉损坏

**修复方案：** 新增 `_discipline_nav()` 函数，三档兼容自动降级：
1. `st.segmented_control(use_container_width=True)` — Streamlit ≥ 1.40，原生 tab 样式 + 全宽（`TypeError` 捕获，自动降级）
2. `st.segmented_control` — Streamlit 1.36~1.39，原生 tab 样式（`AttributeError` 捕获，自动降级）
3. **全宽等宽按钮组（当前环境 1.32.x 实际生效）**：`st.columns(3)` + `st.button(use_container_width=True)`，active 页 `type="primary"`，inactive `type="secondary"`

**选中状态持久化：** 导航选中值写入 `st.session_state["discipline_nav"]`，任何内容区 button 点击触发 rerun 均不会重置导航位置。

- 恢复完整导航标签名：`"📊  账户风险仪表盘"` / `"🔍  交易前评估"` / `"📖  纪律手册速查"`
- 删除所有手写 CSS（破损的 radio 圆圈隐藏逻辑）
- `render()` 中移除 `st.radio()` 调用，统一走 `_discipline_nav()`

### Changed - 资产配置图目标区间读取纪律配置

**`app_pages/overview.py`**：资产配置柱状图的目标区间从 `portfolio` 表的自定义字段改为读取 `app/discipline/config.py` 中的 `RULES["asset_allocation_ranges"]`，确保总览图与纪律仪表盘数据来源一致：
- 货币：按绝对金额（`monetary_min/max_amount`）换算为当前总资产占比，图注显示「X万~Y万元」
- 固收 / 权益 / 另类 / 衍生：直接读取规则百分比，图注格式与纪律手册一致

### Changed - 侧边栏导航调整

**`streamlit_app.py`**：
- 新增「**投资纪律**」导航入口（对应 `app_pages/discipline`），位于「养老&生活规划」之后
- 「投资策略」更名为「**投资决策**」，与纪律模块形成区分

---

## [1.7.0] - 2026-03-18 - 投资纪律模块：规则重组 + 仪表盘重排

### Changed - 投资纪律手册规则重组（12条 → 11条）
- 删除「规则6 — 账户级防御熔断」，防熔断逻辑保留在检查清单附加区及 risk_engine，不再作为独立规则条目
- 原「规则9 — 加仓节奏纪律」提升为**规则6**（紧接规则5止损之后），强调加仓节奏是与止损同等级的 HARD 约束
- 后续规则顺延：原规则10→9（长期底仓）、原规则11→10（情绪冷却）、原规则12→11（交易前检查清单）
- 更新规则7内部交叉引用：「规则9（加仓节奏）」→「规则6（加仓节奏）」
- 最终 11 条规则顺序：
  1. 杠杆工具分级管理 🔴
  2. 跨资产类别配置约束与偏离度控制 🔴
  3. 单一标的仓位上限 🔴
  4. 流动性管理（子弹纪律）🔴
  5. 止损与逻辑判断 🔴
  6. **加仓节奏纪律** 🔴（原规则9）
  7. 动态仓位管理 🔵
  8. 左侧交易原则 🔵
  9. **长期持仓底仓机制** 🔵（原规则10）
  10. **情绪冷却与禁止交易纪律** 🔴（原规则11）
  11. **交易前强制检查清单** 🔴（原规则12）

### Changed - 仪表盘指标重排（Tab 1）
- 顶部指标从 4 列缩减为 **3 列**，顺序从左到右：**杠杆工具（规则1）→ 最大单仓（规则3）→ 流动性（规则4）**
- 删除「账户熔断」metric 卡片（熔断检查移至 Tab 3 检查清单附加区）

### Changed - 仪表盘表格左右互换（Tab 1）
- 左侧（窄列）改为「资产配置（规则2）」
- 右侧（宽列）改为「持仓集中度（规则3）」

### Changed - 规则编号同步更新（代码层）
- `app_pages/discipline.py`：Tab 3 检查清单标题 规则12→规则11；评估器 help 文本规则9→规则6/规则10→规则9；「账户级熔断（规则6，加仓专用）」去除规则编号
- `app/discipline/config.py`：position_sizing 注释规则9→规则6；cooldown_rules 注释规则11→规则10；core_holding_floor 注释规则10→规则9；circuit_breaker 注释去除规则编号
- `app/discipline/risk_engine.py`：`_check_add_rhythm` docstring 规则9→规则6；`_check_circuit_breaker` docstring 去除规则编号；违规消息 `[规则9·单次上限]`→`[规则6·单次上限]`，`[规则9·间隔]`→`[规则6·间隔]`，`[规则6·账户熔断]`→`[账户熔断]`；模块 docstring HARD_RULE 列表同步更新
- `app/discipline/models.py`：`is_core_holding` 注释规则10→规则9；`last_add_date` 注释规则9→规则6

---

## [1.6.0] - 2026-03-18 - 数字格式规整 + 截图识别扩展（国金/雪盈）

### Fixed - 数字格式统一
- 所有市值/盈亏列统一保留 2 位小数：市值(美元/港币/人民币)、占比%、盈亏(美元/港币/人民币)、盈亏%
- 修复 `platform_importers.py` 中 `round(x * rate)` 不带精度参数导致 CNY 市值取整为整数的问题（老虎/富途两处）
- 修复 `overview.py` 手动编辑保存时汇率换算同样未保留小数的问题
- 将 `_fmt_hkd` / `_fmt_cny` / `_fmt_pnl_cny` 格式函数从 `,.0f` 改为 `,.2f`，占比% 从 `:.1f%` 改为 `:.2f%`

### Added - 截图识别扩展：国金证券 & 雪盈证券
- `app/bank_screenshot.py` 新增 `BROKER_PROMPTS`（国金/雪盈专用提示词）
- 新增 `parse_broker_screenshot()` 函数：解析券商持仓列表（返回 list 而非固定分类 dict）
- 新增 `broker_positions_to_db()` 函数：调用 fx_service 换算汇率，生成可写入 DB 的持仓格式
  - 雪盈证券：提取 USD 市值/盈亏，换算 CNY 存储
  - 国金证券：截图已为 CNY（"普通交易-持仓"页顶部标注"人民币 CNY"），直接使用；反推 HKD 供港币列展示
- 抽取 `_call_vision_api()` 公共函数，银行/券商识别共用，消除重复代码
- `overview.py` 截图识别 Tab 扩展：平台从 3 家银行增至 5 个平台（+ 国金证券、雪盈证券）
  - 银行走固定分类更新流程（按名称 patch）；券商走整平台替换流程（`_import_positions_by_platform`）
  - 券商识别预览：雪盈显示市值(美元)/盈亏(美元)，国金显示市值(人民币)/盈亏(人民币)

### Changed - 代码清理
- 移除 `_render_asset_section` 和 `_update_bank_positions` 中的临时调试日志（`/tmp/wealthpilot_debug.log`）

---

## [1.5.0] - 2026-03-18 - 产品重构 + 数据更新 + 券商直连导入

### Changed - 导航与模块拆分
- 「资产全景」→「投资账户总览」，「持仓明细」→「资产明细」
- 新增「养老&生活规划」独立导航模块，公积金/年金/日常消费类负债归入此处，与投资资金完全隔离
- 数据导入融合进各明细区块，删除独立的「数据导入」导航栏

### Changed - 投资账户总览
- 资产明细与负债明细改为平铺两楼层，去掉 Tab 切换
- 新增盈亏原始货币列（美元/港币），与市值保持一致的多币种展示
- 大类资产命名统一：「现金」→「货币」，固定货币/固收/权益/另类/衍生五类
- 大类资产配置图：鼠标悬停柱子显示该类资产示例，替换原来的 expander 说明
- 负债明细只展示 `purpose=投资杠杆` 的条目，养老/生活类不纳入杠杆率计算
- 下载+导入操作区重新设计：下载按钮 + 单个「导入」expander（内含「通用CSV」和「券商官方CSV」两个 Tab）

### Added - FX Rate Service
- 新增 `app/fx_service.py`，对接 Frankfurter API
- 支持 `latest` / `historical` 两种模式，月末复盘可指定历史汇率
- 汇率换算由确定性代码完成，不让 LLM 参与计算
- 保留未来替换 provider（ECB/HKMA）的扩展能力

### Added - 券商直连导入
- 新增 `app/platform_importers.py`，专门解析券商官方 CSV
- 支持**老虎证券**对账单 CSV（多 section 格式，自动提取 USD/CNY 汇率）
- 支持**富途证券**持仓 CSV（标准表格格式，调用 fx_service 获取汇率）
- 按平台替换持仓，其他平台数据不受影响
- 导入前显示解析预览，确认后生效

### Added - 养老&生活规划模块
- 新增 `app_pages/retirement_life.py`
- 公积金、年金、购房类资产独立展示，不纳入投资账户总览
- 日常消费类负债（信用卡/闪电贷/e招贷）归入此模块

### Changed - 数据更新（2026-03-16/17）
- 老虎证券：15 条持仓全量更新（LI/META/PDD/AAPL/TSLA/TSLA/BRK.B 等）
- 富途证券：MSFT/QQQ/PDD 更新为最新市值
- 雪盈证券 LI 盈亏修正为 -44.50%（-$4,175.35 USD）
- `Position` 模型新增 `profit_loss_original_value` 字段（原始货币盈亏）

### Fixed - SSH 推送配置
- 配置 `~/.ssh/config` 让 GitHub SSH 走 port 443（`ssh.github.com`），解决 port 22 被拦问题

---

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
