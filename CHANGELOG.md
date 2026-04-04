# Changelog

All notable changes to the WealthPilot project will be documented in this file.

---

## [2.0.0] - 2026-04-04 - 全栈重写：React+FastAPI · 四核心模块完整落地 · 1.X 封板

> **里程碑**：WealthPilot 1.X 阶段正式结束。本版本完成了从 Streamlit 单体到
> React 19 + FastAPI 前后端分离架构的完整迁移，四个核心模块全部在新架构上落地
> 并交付生产可用状态。后续迭代版本号记作 2.X。

### Breaking Changes — 架构迁移

- **前端**：从 Streamlit 完全迁移至 React 19 + Vite + TypeScript + Tailwind v4 SPA
- **后端**：从 Streamlit 内嵌逻辑完全迁移至 FastAPI（RESTful API + SSE 流式接口）
- **启动方式变更**：
  - 旧：`streamlit run streamlit_app.py`
  - 新：后端 `uvicorn backend.main:app --reload --port 8000` + 前端 `npm run dev`（`frontend/`）
- **API 前缀**：所有接口统一走 `/api/*`，Vite dev server proxy 转发

### Added — backend/（FastAPI 服务层，全新）

- `backend/main.py`：FastAPI 应用入口，挂载四个路由模块
- `backend/api/portfolio.py`：持仓聚合、净值/盈亏计算、资产分布、负债管理、风险告警、多格式导入（CSV/券商CSV/截图）
- `backend/api/discipline.py`：纪律规则 CRUD、投资手册上传/重置、实时行为评估（SSE 兼容）
- `backend/api/research.py`：观点库增删改查、研究文档上传解析（URL/PDF/文本）、研究卡片审批工作流
- `backend/api/decision.py`：SSE 流式决策对话、Explain Panel 数据接口、会话生命周期管理
- `backend/api/tasks.py`：后台异步任务状态查询
- `backend/services/`：各模块业务逻辑层，对接原有 `app/`、`decision_engine/`、`intent_engine/` 核心引擎

### Added — frontend/（React SPA，全新）

**布局与导航**
- `AppLayout.tsx`：固定侧边栏 + 内容区布局，路由 `/` `/discipline` `/research` `/decision`
- `Sidebar.tsx`：带图标的导航菜单，活跃态高亮

**Dashboard（投资账户总览）**
- 净值/总资产/总负债/总盈亏四卡片，支持多货币折算
- 资产配置饼图（Recharts），按资产类别展示权重
- 持仓列表：分段筛选、盈亏着色、TOP持仓高亮
- 负债列表：类别/用途/利率展示
- 风险告警 Badge（warning/danger 分级）
- AI 综合分析报告：SSE 流式生成，打字光标动画
- 多格式数据导入：标准CSV、券商CSV（多平台适配）、截图OCR

**Discipline（投资纪律）**
- 纪律规则可视化：单标的上限/权益类上限/杠杆上限等关键规则展示
- 规则编辑：JSON 直接编辑 + 重置为默认
- 投资手册：上传 PDF/Markdown、在线预览、一键重置
- 实时行为评估：输入操作意图，即时返回纪律校验结果（通过/阻断/警告）

**Research（投研观点）**
- 观点库：观点卡片列表，支持关键词搜索、新建/编辑/删除
- 文档解析工作流：URL/PDF/文本三种输入方式 → LLM 提炼 → 研究卡片 → 一键审批为观点
- 卡片详情：bull case/bear case/关键指标/风险/行动建议完整展示
- 审批时支持字段覆写

**Decision（投资决策）**
- SSE 流式 AI 对话（左侧 70%）：打字机效果、ReactMarkdown 渲染、多轮会话
- 右侧 ExplainPanel（30%）：七模块决策依据可视化
  - 识别意图：意图类型/资产/操作/置信度
  - 持仓数据：当前仓位/盈亏/持仓平台
  - 纪律校验：规则通过/违规状态、规则明细
  - 投研观点：用户录入观点 + 联网参考，可折叠
  - 市场信号：仓位/事件/基本面/情绪四维度信号
  - AI 推理过程：LLM reasoning 条目，默认折叠
  - 最终结论：决策档位 Emoji + 结论摘要 + 策略/风险要点 + 免责声明
- 新建对话、清空会话功能

### Changed — decision_engine/

- `llm_engine.py`：
  - system prompt 新增 markdown 加粗规范（只对关键数字/结论词加粗，禁止整句加粗）
  - `chat_answer` 各意图 prompt 精化写作要求（三段结构、字数控制、禁套话）
  - 恢复原始第6条"本系统输出仅供参考，不构成投资建议"

### Changed — app/

- `state.py`：新增 `portfolio_id` 全局状态，供 FastAPI 路由读取默认 portfolio
- `analyzer.py`：扩展持仓聚合接口，对接 portfolio_service

### Changed — requirements.txt

- 新增 `fastapi`、`uvicorn[standard]`、`python-multipart`、`sse-starlette` 等后端依赖

---

## [1.14.0] - 2026-03-28 - 意图引擎升级 · 投研提炼重构 · 多标的决策 · 资产语义匹配

### Added — 决策结论粒度扩展（`decision_engine/llm_engine.py`）

- 决策结论从 3 档（BUY / HOLD / SELL）扩展为 6 档：
  - `BUY` → 📈 加仓
  - `HOLD` → 🔍 观望
  - `TAKE_PROFIT` → 💰 部分止盈（新增）
  - `REDUCE` → 📉 逐步减仓（新增）
  - `SELL` → 🚨 减仓/清仓
  - `STOP_LOSS` → 🛑 止损离场（新增）
- LLM prompt 更新：每个决策值附带选择标准描述，模型选最精准选项而非强行归入三档
- `_build_result()` 有效集合更新，非法值仍降级为 HOLD
- `app_pages/strategy.py` 结论卡颜色映射更新（绿→琥珀→橙→橙红→红→深红）

### Added — 多标的同操作决策分发（`intent_engine/` · `app_pages/strategy.py`）

- `IntentEntities` 新增 `multi_assets: list[str]` 字段
- 意图识别 system prompt 新增三种情况的多标的处理规则：
  - 情况A：多标的同操作 → `multi_assets` 列表，`asset=null`
  - 情况B：换仓（卖A买B）→ 以 SELL 侧为主，填入 `multi_assets`
  - 情况C：单焦点标的（其他标的为背景）→ 保持原有规则
- `_process_submit` 检测到 `multi_assets >= 2` 时，顺序运行每个标的的完整决策链路
- 新增 `_process_multi_asset()` 与 `_build_multi_asset_chat_answer()`：各标的独立分析，合并为一条 chat 回答，右侧链路面板展示最后一个标的的决策详情

### Added — 资产名称 LLM 语义匹配（`decision_engine/data_loader.py`）

- 新增 `_resolve_asset_by_llm()`：当 `find_target()` 精确/模糊匹配失败时，调用 gpt-4.1-mini 进行语义理解
- 把用户描述的资产名 + 实际持仓列表（名称+平台+类别）送给模型，利用大模型泛化理解能力匹配
  - 自然理解平台别名：招行=招商银行、建行=建设银行等
  - 理解品类语义：稳健/低风险≈固收类，进取/成长≈权益类
  - 解决"招行的稳健理财"→实际持仓叫"稳健投资"这类命名不一致问题
- 结果按 `asset_name` 缓存（session 级内存，避免同一标的重复调用）
- 调用轻量：max_tokens=50，timeout=8s，temperature=0，约 $0.001/次

### Fixed — 投研观点残缺条目问题（`decision_engine/data_loader.py`）

- **根本修复**：投研观点来源从直接透传 `ResearchViewpoint` 字段改为从 `ResearchCard` 提炼
  - `_distill_research_cards()`：读取 `ResearchCard` 的 thesis/bull_case/bear_case/key_drivers/risks/action_suggestion 全量字段，调用 gpt-4.1-mini 按重要性提炼 3-5 条完整结论句
  - 结果缓存 24h（用户资料不常变）
  - 避免了"利润率走势"这类只有标题没有内容的残缺条目被直接透传
- `ResearchViewpoint` 仅读取 `action_suggestion` 和 `invalidation_conditions` 两个高置信度字段作为补充（最多 2 条）
- 联网搜索解析优化：过滤以冒号结尾的节标题行（如 `**机构评级**:`），清除 `**` markdown 粗体标记

### Fixed — 联网投研搜索格式问题（`decision_engine/data_loader.py`）

- Perplexity API 有时返回分节标题格式（`**Section:**\n内容`），标题行被误作独立条目展示
- 修复解析逻辑：跳过以 `:` 结尾的行，清除残余 `**` 标记
- 搜索 prompt 明确禁止输出分节标题，要求每条直接写结论性内容

---

## [1.13.0] - 2026-03-24 - 投资决策模块业务逻辑稳定版

> **封板状态**：投资决策核心逻辑全部验证通过，UI 样式冻结，可进入下一轮功能迭代。
> 本版本不含新功能，聚焦逻辑正确性审计与已知 bug 修复。

### Fixed — 规则来源统一（`decision_engine/data_loader.py`）

- **原问题**：`InvestmentRules` 从 `Portfolio` 表读取 `max_single_stock_pct`，该字段由用户在「策略设定」Tab 手动输入，默认值 15%，与「投资纪律」页面使用的 `DISCIPLINE_RULES`（40%）完全割裂，导致决策引擎和纪律中心对同一仓位给出相互矛盾的结论
- **修复**：`InvestmentRules` 四个字段全部改为读取 `discipline/config.py` 的 `DISCIPLINE_RULES` 常量
  - `max_single_position`：15%（Portfolio 表）→ 40%（`DISCIPLINE_RULES`）
  - `max_equity_pct`：Portfolio 表 → 80%
  - `min_cash_pct`：Portfolio 表 → 20%
  - `max_leverage_ratio`：Portfolio 表 → 1.0
- **效果**：全系统规则口径唯一，投资决策与投资纪律模块结论一致

### Fixed — general_chat 模型 ID 无效（`decision_engine/llm_engine.py`）

- **原问题**：`llm_engine.chat()` 使用 `claude-haiku-4-20250514`，该模型 ID 不存在，导致每次普通问答（`general_chat` 路径）都静默 fallback，返回"抱歉，系统暂时繁忙"
- **修复**：改为 `claude-haiku-4-5-20251001`（Haiku 4.5 正式 ID）
- **效果**：普通对话路径正常走 LLM，不再静默失败

### Fixed — 左侧 Chat 回答改为完整 LLM 生成（`decision_engine/llm_engine.py`）

- **原问题**：`generate_chat_answer()` 使用 `claude-haiku-4-20250514` + max_tokens=500，模型 ID 无效且 token 不足，实际输出为机械 fallback 文本
- **修复**：切换为 `claude-sonnet-4-20250514` + max_tokens=800，Prompt 增加结构引导（建议判断 / 推理解释 / 行动风险），字数下限 180 字
- **效果**：左侧回答为完整自然语言，有数据引用，不再是模板拼接

### Reverted — 回退 3 次失败的 UI 布局重构（`app_pages/strategy.py`）

- 回退提交：`aaebcec` / `5926cb7` / `1a24a57`
- 原因：尝试通过 CSS `:has()` 选择器 + JS iframe DOM 操控实现左右独立滚动，因 Streamlit 每个 widget 有多层 wrapper div 导致 flex chain 断裂，方案无效，页面行为与修改前无差异
- 当前版本：`st.container(height=420)` 消息区 + `st.divider()` + text_area 输入
- **UI 样式已冻结，暂不继续调整**

### Added — 归档设计文档（`docs/`）

- `WealthPilot Typography System.md`
- `WealthPilot 投资决策页 UI结构规范.md`
- `投资决策模块PRD V3.1（补充优化）.md`

### 业务逻辑审计结论

| 项目 | 结论 |
|------|------|
| 多轮对话上下文继承 | ✅ 逻辑正确，last_intent 从历史 user 消息取，context 正确构建 |
| 每轮独立 decision_id | ✅ UUID 保证唯一，general_chat 不生成 |
| 投资纪律唯一规则来源 | ✅ 已统一至 DISCIPLINE_RULES 常量 |
| 左侧 LLM 生成（非模板） | ✅ Sonnet + max_tokens=800，完整自然语言 |
| general_chat 路径 | ✅ 模型 ID 已修正 |
| 异常路径覆盖 | ✅ hypothetical / aborted / fallback / data_error 全覆盖 |

---

## [1.12.1] - 2026-03-23 - 投资决策模块缺陷修复封板（Manus 测试回归通过）

> **封板状态**：投资决策模块本轮开发已闭环，可进入下一轮功能开发。
> 基于《WealthPilot 投资决策模块正式测试报告 v1.1》（Manus，2026-03-23）完成全部 P0/P1/P2 问题修复，并落地 2 条产品策略确认项。回归验证 13/13 通过。

### Fixed — BUG-01 P0：API Key 缺失导致入口级阻塞（`app_pages/strategy.py`）

- **原问题**：未配置 `ANTHROPIC_API_KEY` 时，页面入口 `st.stop()` 导致"策略设定"等非 AI 功能全部不可用
- **修复**：入口改为非阻塞 `st.info()` 提示；API Key 检查下移至点击"开始分析"后，仅拦截 AI 分析链路
- **效果**：无 API Key 时，Tab 2 策略设定可正常访问和保存

### Fixed — BUG-02 P1：百分比负值被静默替换（`decision_engine/data_loader.py`）

- **原问题**：`_safe_pct()` 将负值静默 fallback 为默认值（如 `-0.1 → 0.25`），用户无感知
- **修复**：负值（`v < 0`）抛出 `ValueError`，`decision_flow` 新增专属捕获，向用户展示"⚠️ 数据异常"
- **效果**：非法负值明确报错，不再静默替换

### Fixed — BUG-03 P1：模糊匹配首项风险（`data_loader.py` + `decision_flow.py`）

- **原问题**：多个相似标的并存时（如"理想汽车"/"理想汽车-W_1"），系统静默选择第一个
- **修复**：重构为 `_find_all_positions()`，引入**精确匹配优先**策略（归一化名称完全相等 → 优先返回；无精确匹配时才使用模糊结果）；`LoadedData` 新增 `ambiguous_matches` 字段；多候选时流程 `ABORTED` 并展示候选名称列表
- **效果**：精确输入（如"理想汽车"）唯一命中；短词（如"理想"）触发歧义提示，要求用户精确输入

### Fixed — BUG-04 P2：LLM 结论自动修正缺乏提示（`llm_engine.py` + `strategy.py`）

- **原问题**：LLM 返回非标准值时静默修正为 HOLD，用户无感知
- **修复**：`LLMResult` 新增 `decision_corrected` / `original_decision` 字段；UI 展示灰色小字提示"已自动修正"
- **效果**：修正事件透明化，用户可感知结论经过自动调整

### Fixed — BUG-05 P2：空仓减仓 FlowStage 与提示文案歧义（`decision_flow.py`）

- **原问题**：未持有标的 + 减仓意图时，规则 `violation=True` 但流程继续至 LLM，最终返回 `FlowStage.DONE` + HOLD 建议，用户无法判断这是无效操作
- **修复**：规则校验后检测该场景，直接 `FlowStage.ABORTED`，UI 展示红色"⛔ 无效操作"
- **效果**：空仓减仓明确中断，状态与提示文案一致

### Added — 产品策略落地（`data_loader.py`）

**策略 A — 不支持单字匹配**
- `_find_all_positions()` 增加 `len(name_lower) < 2` 守卫，单字直接返回空，不进行任何匹配
- 避免"理"误匹配"理想汽车"等场景

**策略 B — 百分比 0 值为合法边界值**
- `_safe_pct()` 修改条件：`0` 直接返回 `0.0`，不替换为默认值
- 负值（`v < 0`）仍为非法输入，抛出 `ValueError`

### Added — 交接文档

- `INVESTMENT_DECISION_TEST_HANDOFF.md`：投资决策模块完整测试交接说明（测试范围/入口/数据依赖/已知限制）
- `TEST_DATA_TEMPLATE.md`：配套测试数据模板（典型用例 + 边界用例 + 信号层速查表）
- `INVESTMENT_DECISION_FIX_SUMMARY.md`：修复验证交接文档（每项 Bug 修复说明 + 最小回归测试建议 7 条）

---

## [1.12.0] - 2026-03-23 - 投资决策引擎 MVP 上线（PRD V2.0）

### Added — `decision_engine/` 决策引擎包（7 文件）

全新模块，基于 PRD V2.0 实现完整投资决策链路：

| 文件 | 职责 |
|------|------|
| `intent_parser.py` | 自然语言 → 结构化意图（Claude Sonnet 4），置信度 < 0.6 触发澄清问题 |
| `data_loader.py` | 多源数据加载：SQLite 持仓 + ResearchViewpoint + mock 用户画像 |
| `pre_check.py` | 三要素前置校验（画像 / 持仓 / 纪律规则） |
| `rule_engine.py` | 仓位纪律规则校验，violation / warning 分级 |
| `signal_engine.py` | 四维信号生成（仓位 / 事件 / 基本面 / 情绪） |
| `llm_engine.py` | Claude Sonnet 4 综合推理 → BUY / HOLD / SELL |
| `decision_flow.py` | 全链路编排 + FlowStage 状态机（INTENT→LOADED→PRE_CHECK→RULE_CHECK→SIGNAL→LLM→DONE/ABORTED） |

### Changed — `app_pages/strategy.py` 重写

- 新增「🧠 决策引擎」Tab（8 步结构化展示：意图 → 数据 → 规则 → 信号 → AI 推理 → 结论 → 免责声明）
- 原「⚙️ 策略设定」Tab 完整保留
- 快捷示例按钮（3 个预设查询）

### Added — 依赖

- `anthropic>=0.86.0` 加入 `requirements.txt`

---

## [1.11.0] - 2026-03-23 - 接入 Manus UI 调整成果，建立功能开发新基线

### Changed
- 接入 Manus 对 `app_pages/overview.py` 和 `streamlit_app.py` 的 UI 微调成果
- 建立 `feature/manus-handoff-v2` 分支作为后续功能开发主线
- 打 `backup/pre-manus-v1.10.0` 安全备份 tag

---

## [1.10.0] - 2026-03-22 - 投资账户总览 UI 深度重构 + 项目交接归档

### Changed — 投资账户总览（app_pages/overview.py）重大 UI 升级

**全局导航 & 侧边栏（streamlit_app.py）**
- 侧边栏标题改为"WealthPilot · 个人智能投顾"，应用深蓝渐变主题（#1B2A4A → #243558）
- 页面标题从"WealthPilot - 个人资产配置与智能投顾"改为"WealthPilot · 个人智能投顾"
- 侧边栏导航按钮样式统一：浅灰文字 + 蓝色激活态边框

**大类资产配置模块**
- 资产类别顺序调整为：货币、固收、权益、另类、衍生（与行业惯例一致）
- 各行高度统一固定（44px），消除内容多少引起的高度不一致
- 偏离标签从两行合并为一行（百分比 + 箭头同行显示）
- 图表区域整体压缩，减少空白
- 悬浮 tooltip 重设计：鼠标放标签显示底层资产示例；放图点显示金额；放目标区域显示百分比范围
- 货币类目标区间显示修正为"1~10 万元"而非百分比

**KPI 卡片**
- "杠杆率"更名为"杠杆倍数"，计算公式改为：总资产 / 净资产（而非负债率），显示格式 "2.31x"
- 杠杆倍数新增分级徽章：🟢 安全 / 🟡 可控 / 🟠 警戒 / 🔴 高风险 / 🔴 危险
- 杠杆倍数悬浮时显示对应级别的风险提示文字
- 顶部页面内边距从 24px 压缩至 2px，整体内容上移

**资产明细表格**
- "资产大类"列内容居中对齐

**负债明细**
- 仅显示 `purpose == "投资杠杆"` 的负债条目（过滤消费贷等无关负债）

**导入/导出数据模块**
- 位置从页面底部移至资产明细与负债明细之间（更符合操作流程）
- 视觉样式完全重设计，使用与页面卡片一致的风格：
  - 圆角 16px、box-shadow 匹配卡片、标题字体颜色改为 navy #1E3A5F
  - 内容区域 padding、tab 样式、按钮样式、上传区域样式全部重写

**技术架构调整**
- `_build_overview_html()` 拆分为顶部 `_build_overview_html()` + 底部 `_build_bottom_html()`
- 在两个 iframe 之间插入 Streamlit 原生 expander，实现导入/导出区块的正确定位
- iframe 高度计算公式重新校准（补偿 Streamlit components.html 自动 +16px 的偏差）
- 消除导入/导出 expander 与下方 iframe 之间的 16px 间距（CSS margin 补偿）

### Added — 项目归档文档

- 新增 `PROJECT_HANDOFF.md`：面向接手开发者的完整交接文档
  - 项目概述、技术栈、运行方式
  - 目录结构说明（逐文件注释）
  - 页面结构说明（已实现 vs 占位页面）
  - UI 实现架构（iframe 双层方案详解）
  - 已知问题清单
  - 后续开发建议与注意事项

---

## [1.9.1] - 2026-03-20 - 投研观点模块稳定性修复（两轮精修）

### Fixed - P0：候选观点卡页面崩溃（DetachedInstanceError）

**`app_pages/research.py`**：`_render_cards()` 在关闭 SQLAlchemy Session 后访问 `card.viewpoint`、`card.document` 懒加载关系，导致崩溃。修复：查询时加 `joinedload()` 预加载所有关联对象，确保 Session 关闭前数据已完整读入内存。

### Fixed - P1：Markdown 文件上传后内容不显示 / 抛 StreamlitAPIException

**`app_pages/research.py`**：Streamlit 规则要求 widget 的 `session_state` 值必须在 widget 渲染之前设置，上一轮修复的注入时序仍然错误（先渲染 `st.text_area`，再写 `session_state`，触发 API 异常）。修复：将 `session_state["ri_md_content"] = content` 移到 `st.text_area(key="ri_md_content")` 渲染之前执行，删除不必要的末尾 `st.rerun()`。

### Fixed - P1：Tab 导航跳转有底层警告（widget 后修改绑定 state）

**`app_pages/research.py`**：解析完成后直接修改 `session_state["research_nav"]`，违反 Streamlit"widget 渲染后不可修改其绑定 state"规则，导致日志告警。修复：引入独立中转变量 `_research_nav_target`，在 `render()` 顶部所有 widget 实例化之前统一应用跳转意图，彻底消除警告。

### Fixed - P1：`saved_only` 状态资料无法触发 AI 解析

**`app_pages/research.py`**：「待解析资料」下拉框过滤条件硬编码为 `status == "pending"`，`saved_only` 资料被排除。修复：改为 `status in ("pending", "saved_only")`。

### Fixed - P1：重复解析同一资料生成多张候选卡

**`app_pages/research.py`**：解析前检查 `document_id` 是否已有对应卡片，若存在则更新字段并提示用户，避免重复写入。

### Fixed - P1：重复导入同名资料无警告

**`app_pages/research.py`**：保存前按 `title` 查重，存在同名记录时展示 `st.warning`（含已有资料上传时间）并中止提交。

### Fixed - P2：决策检索排序区分度极低（同分并列）

**`app/research.py`**：基础分（validity + freshness + approval）权重过高，掩盖关键词相关性得分；且自然语言中的标的名未参与匹配。修复：
- 降低基础分权重（最高从 18 降至 9），还原关键词得分的区分作用
- 新增「查询文本子串包含标的名」匹配 +15 分，无需分词即可处理「拼多多现在适不适合加仓」类查询

### Fixed - P2：超长文本截断无提示

**`app_pages/research.py`**：正文超过 4000 字时展示 `st.warning`，告知用户截断范围。

### Fixed - P2：AI 生成标签数量过多

**`app/ai_advisor.py`**：Prompt 末尾追加约束「suggested_tags 最多 5 个」，减少标签库噪音。

### Fixed - P2：Tab 名称与面包屑文案不一致

**`app_pages/research.py`**：第四个导航项统一改为「🔍  决策检索」。

### Added - 文档

- `docs/research_opinions_module_design.md`：投研观点模块设计说明（供外部 AI 测试评估使用）
- `docs/research_module_update_log.md`：第一轮修复日志
- `docs/research_module_regression_fix_log.md`：第二轮精修日志（本次）

---

## [1.9.0] - 2026-03-19 - 投研观点模块 MVP 上线

### Added - 投研观点（`投研观点`）全新模块

**数据层**（`app/models.py`）：新增三张数据库表：
- `ResearchDocument`：原始研究资料（标题、来源类型、正文、解析状态等）
- `ResearchCard`：AI 提炼的候选观点卡（thesis、bull/bear case、驱动因素、风险、建议等）
- `ResearchViewpoint`：用户审核入库的正式观点（有效性、认可程度、标签、来源追溯等）

**AI 层**（`app/ai_advisor.py`、`app/config.py`）：
- 新增 `generate_research_card()` 函数，调用 `gpt-4.1-mini` 将研报原文提炼为结构化 JSON 观点卡
- 新增配置常量 `AI_RESEARCH_MODEL`、`AI_RESEARCH_MAX_TOKENS`

**检索层**（`app/research.py`）：
- 新增 `retrieve_research_context()` 多因子评分检索函数，支持自然语言 + 标的 + 市场过滤
- 评分维度：标的名精确/模糊匹配、市场匹配、标签命中、全文关键词、有效性权重、认可度权重、时效性衰减

**UI 层**（`app_pages/research.py`）：完整四 Tab 页面：
- **资料导入**：支持纯文本/Markdown/链接/PDF 四种类型，保存后可选立即 AI 解析
- **候选观点卡**：审阅 AI 提炼结果，逐卡操作（认可/编辑修改/仅保留/丢弃）
- **观点库**：5 维度筛选 + 关键词搜索，支持内联修改有效性状态
- **决策检索**：自然语言查询，展示带评分的召回结果

**路由**（`streamlit_app.py`）：「投研观点」从 placeholder 切换到正式 `research.render()`

---

## [1.8.0] - 2026-03-19 - 全局导航重构 + 产品框架扩展

### Changed - 侧边栏导航重构：平铺列表 → 三分组按钮导航

**`streamlit_app.py`** 全面重写：
- 导航从单级平铺改为三组分层结构：**📈 投资规划**（8项）/ **🏠 财务规划**（4项）/ **📊 资产负债总览**（2项）
- 删除「投资持仓数量」侧边栏指标
- 删除「AI 分析报告」独立页面入口，功能入口移入「投资账户总览」
- 「AI 综合分析报告」入口整合进投资账户总览页底部

### Added - 未实现页面占位模块

**`app_pages/placeholder.py`**：新增通用占位渲染器，覆盖 11 个规划中但尚未实现的功能页（用户画像、新增资产配置、收益分析、生活账户总览、购房/养老/消费规划、个人/家族资产负债总览等）。

### Changed - 投资账户总览页：整合 AI 报告入口

**`app_pages/overview.py`**：
- 删除「风险告警」独立区块
- 新增页面底部「AI 综合分析报告」区块，包含风险告警摘要 + 「✨ 生成报告」按钮（功能建设中）
- 告警明细移入可折叠 `expander`

---

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
