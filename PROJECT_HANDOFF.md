# WealthPilot — 项目交接文档

**版本**：v1.9.1
**整理日期**：2026-03-22
**交接对象**：后续 UI 收敛与体验优化开发者（Manus）

---

## 一、项目概述

### 1.1 这是什么

WealthPilot 是一个**个人智能投顾系统**，面向有一定资产规模的个人投资者，核心功能包括：

- 投资账户总览（KPI、资产配置图表、持仓明细、负债明细、风险告警）
- 投资纪律管理（交易前评估、风险约束检查、行为约束引擎）
- 投研观点库（研报导入 → AI 提炼 → 观点入库 → 决策检索）
- AI 综合分析报告（调用 GPT-4 生成投资建议）
- 其余模块（养老规划、投资决策等）处于占位/早期阶段

### 1.2 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.9+ | 语言 |
| Streamlit | ≥ 1.32 | Web UI 框架 |
| SQLAlchemy | ≥ 2.0 | ORM（SQLite 持久化） |
| OpenAI SDK | ≥ 1.20 | LLM 集成（gpt-4.1-mini / gpt-4.1-nano） |
| Plotly | ≥ 5.20 | 交互图表（雷达图、饼图、折线图） |
| Pandas | ≥ 2.0 | 数据处理 |

### 1.3 运行方式

```bash
# 1. 安装依赖（建议使用虚拟环境）
pip install -r requirements.txt

# 2. 配置环境变量（必须配置 OPENAI_API_KEY）
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY=sk-xxxx

# 3. 启动应用
streamlit run streamlit_app.py

# 4. 运行测试
pytest
```

**注意**：`data/wealthpilot.db` 是 SQLite 数据库，已在 `.gitignore` 中排除。首次运行时自动创建。

---

## 二、目录结构说明

```
WealthPilot/
│
├── streamlit_app.py          # ★ 主入口。页面配置、侧边栏导航、路由分发
├── ui_components.py          # ★ 全局 UI 组件库（设计 Token、CSS 注入函数）
│
├── app/                      # 业务逻辑层（不含 UI）
│   ├── config.py             #   全局常量（阈值、AI 模型名、UI 图标）
│   ├── database.py           #   SQLAlchemy engine/session/init_db
│   ├── state.py              #   Streamlit 跨页面共享状态
│   ├── models.py             #   ORM 数据模型（Portfolio / Position / Liability / Research*）
│   ├── analyzer.py           #   分析引擎（BalanceSheet 计算、偏离告警）
│   ├── ai_advisor.py         #   LLM 调用（分析报告、告警解读、研报解析）
│   ├── csv_importer.py       #   CSV 导入/导出（持仓、负债）
│   ├── bank_screenshot.py    #   银行截图 OCR 识别
│   ├── platform_importers.py #   券商平台导入器
│   ├── fx_service.py         #   外汇服务（汇率转换）
│   ├── research.py           #   投研检索引擎（多因子评分，MVP 非 embedding）
│   └── discipline/           #   投资纪律子模块
│       ├── config.py         #     纪律规则配置
│       ├── models.py         #     纪律相关数据模型
│       ├── decision_engine.py#     决策判断逻辑
│       ├── risk_engine.py    #     风险约束检查
│       └── psychology_engine.py#   行为约束引擎
│
├── app_pages/                # UI 展现层（每文件对应一个页面）
│   ├── __init__.py           #   模块导出
│   ├── overview.py           # ★★ 投资账户总览（最复杂，约 1400+ 行）
│   ├── discipline.py         # ★  投资纪律（约 1600 行）
│   ├── research.py           # ★  投研观点（约 960 行）
│   ├── strategy.py           #    投资决策（约 86 行，简单）
│   ├── retirement_life.py    #    养老规划（约 151 行，简单）
│   ├── import_data.py        #    数据导入（约 86 行）
│   ├── ai_analysis.py        #    AI 分析报告入口（约 68 行）
│   └── placeholder.py        #    占位页面（未实现页面的通用骨架）
│
├── data/                     # 运行时数据（不提交）
│   └── wealthpilot.db        #   SQLite 数据库（.gitignore 排除）
│
├── docs/                     # 设计文档
│   ├── research_opinions_module_design.md
│   ├── research_module_update_log.md
│   └── research_module_regression_fix_log.md
│
├── tests/                    # 单元测试
├── scripts/                  # 工具脚本
├── archive/                  # 历史版本存档
│
├── CHANGELOG.md              # 版本变更历史
├── README.md                 # 快速开始指南
├── requirements.txt          # Python 依赖
└── .env.example              # 环境变量模板
```

---

## 三、页面结构说明

### 3.1 已实现页面

| 页面名称 | 文件路径 | 状态 | 说明 |
|---------|---------|------|------|
| 投资账户总览 | `app_pages/overview.py` | ✅ 已实现 | 最复杂，iframe 嵌入方案，UI 已接近设计稿 |
| 投资纪律 | `app_pages/discipline.py` | ✅ 已实现 | 含交易前评估、历史记录、风险检查 |
| 投研观点 | `app_pages/research.py` | ✅ 已实现 | v1.9.0 新增，四 Tab 页面 |
| 投资决策 | `app_pages/strategy.py` | ✅ 简单实现 | 框架已建，内容待完善 |
| 养老规划 | `app_pages/retirement_life.py` | ✅ 简单实现 | 基础表单和计算 |

### 3.2 占位页面（显示"开发中"）

以下页面在 `streamlit_app.py` 的导航中存在，但路由到 `placeholder.py`：

| 页面名称 | 所属分组 |
|---------|---------|
| 用户画像和投资目标 | 📈 投资规划 |
| 新增资产配置 | 📈 投资规划 |
| 投资记录 | 📈 投资规划 |
| 收益分析 | 📈 投资规划 |
| 生活账户总览 | 🏠 财务规划 |
| 购房规划 | 🏠 财务规划 |
| 消费规划 | 🏠 财务规划 |
| 个人资产负债总览 | 📊 资产负债总览 |
| 家族资产负债总览 | 📊 资产负债总览 |

### 3.3 导航路由逻辑（streamlit_app.py）

```python
_IMPLEMENTED = {
    "投资账户总览": overview,
    "投资纪律": discipline,
    "投资决策": strategy,
    "养老规划": retirement_life,
    "投研观点": research,
}
# 命中 → 调用对应模块的 render()
# 未命中 → 调用 placeholder.render(page_name)
```

---

## 四、UI 相关实现说明

### 4.1 整体 UI 架构：两套方案并存

WealthPilot 的 UI 实现存在**两套方案**，理解这一点是接手的关键：

#### 方案 A：`streamlit_app.py` + `ui_components.py`（全局基础样式）

- `streamlit_app.py` 通过 `st.markdown("""<style>...</style>""")` 注入侧边栏样式
- `ui_components.py` 中的 `inject_global_css()` 函数注入全局设计 Token（颜色、字体、阴影等）
- 这是原始的"纯 Streamlit 原生组件 + CSS 注入"方案
- **各非 overview 页面**（discipline、research、strategy 等）大体沿用此方案

#### 方案 B：`components.html()` iframe 嵌入（overview.py 专用）

- `app_pages/overview.py` 使用 `st.components.v1.html()` 将自定义 HTML/CSS/JS 嵌入到 iframe
- **大量 HTML 字符串直接写在 Python 文件中**（通过 f-string 注入数据）
- 页面被拆分为两个 iframe（顶部 + 底部），中间嵌入 Streamlit 原生的 expander 组件
- 这是为了对齐 `ui_preview.html` 设计稿而采用的特殊方案

### 4.2 overview.py 的内部结构

```
overview.py
├── 常量定义（颜色、平台类型映射）
├── _build_overview_html()    # 构建顶部 iframe HTML
│   └── 包含：页面 header、KPI 卡片、大类资产配置图表、平台分布图、资产明细表格
├── _build_bottom_html()      # 构建底部 iframe HTML
│   └── 包含：负债明细表格、风险告警卡片、AI 分析报告入口
├── _render_import_panel()    # 导入/导出面板（Streamlit 原生，非 iframe）
└── render()                  # 主渲染函数
    ├── st.markdown(CSS)      # 注入 expander 样式（在 iframe 渲染前）
    ├── components.html(html_top, height=...)   # 顶部 iframe
    ├── st.expander(...)       # 导入/导出区（Streamlit 原生）
    └── components.html(html_bottom, height=...) # 底部 iframe
```

### 4.3 iframe 高度计算机制

overview.py 中的 iframe 高度通过 Python 手动计算，而不是自适应：

```python
# 顶部 iframe（固定内容，含资产明细表格，max-height: 494px）
height_top = 640 + 480 - 56  # = 1064
# Streamlit 会在此基础上再 +16px，实际 iframe = 1080px

# 底部 iframe（随数据变化）
height_bottom = (
    80 + n_inv_liab * 42 + 60   # 负债卡片
    + max(0, n_alerts) * 95     # 告警条目
    + (70 if n_alerts > 0 else 0)
    + 88                         # AI 入口 + 边距
)
```

> **注意**：Streamlit 的 `components.html(height=N)` 实际渲染的 iframe 高度为 `N + 16px`。

### 4.4 CSS 注入方式

overview.py 中的 CSS 注入有两条路径：

1. **Python `st.markdown()` → 主页面 DOM**：用于设置 Streamlit 原生 expander 的样式
2. **iframe 内 `<style>` 标签**：直接写在 `_build_overview_html()` / `_build_bottom_html()` 返回的 HTML 字符串中

关键：`st.markdown(CSS)` 必须在 `components.html()` **之前**调用，否则 Streamlit emotion CSS 会覆盖。

### 4.5 ui_preview.html 与当前代码的关系

> `ui_preview.html` 是设计稿参考文件（原始 HTML 原型）。

overview.py 的实现目标是让"投资账户总览"页面在视觉上对齐 `ui_preview.html`。当前状态：

| 区域 | 与设计稿对齐程度 | 备注 |
|------|----------------|------|
| 页面 header（标题 + 徽章） | ✅ 基本一致 | |
| KPI 卡片（总资产、浮动盈亏、杠杆倍数） | ✅ 基本一致 | 含杠杆分级徽章和悬浮提示 |
| 大类资产配置图表（当前 vs 目标区间） | ✅ 基本一致 | 水平条形图，含偏离标签 |
| 平台分布饼图 | ✅ 基本一致 | Plotly 实现 |
| 资产明细表格 | ✅ 基本一致 | 含标签、大类居中对齐 |
| 负债明细表格 | ✅ 基本一致 | 仅显示"投资杠杆"用途 |
| 风险告警卡片 | ✅ 基本一致 | |
| AI 综合分析报告 | ⚠️ 简化版本 | 仅入口按钮，无展开内容 |

---

## 五、已知问题清单

### 5.1 overview.py 相关问题（UI 层）

#### P1 — 视觉不一致

| 问题 | 影响区域 | 原因 |
|------|---------|------|
| expander（导入/导出数据）视觉风格与 iframe 卡片存在细微差异 | 两个 iframe 之间 | Streamlit 原生组件 vs iframe 中自定义卡片，CSS 覆盖不完全 |
| iframe 高度计算为硬编码，当数据量异常时可能裁切或留白 | 底部 iframe | 手动计算 `height_bottom`，依赖 `n_inv_liab`、`n_alerts` 精确值 |
| 顶部 iframe 与 expander 之间仍有约 16px 的灰色间距 | overview 页面中部 | Streamlit 的 flex `gap: 16px` 无法彻底消除 |

#### P2 — 功能层面

| 问题 | 影响区域 | 原因 |
|------|---------|------|
| 平台分布图图例文字（如"国金证券"）在某些宽度下会被截断 | 饼图 legend | Plotly legend 宽度限制 |
| 悬浮提示（tooltip）在 Safari 上兼容性未验证 | 大类配置图、KPI 卡片 | iframe 内自定义 JS tooltip |
| 导入/导出 CSV 功能在 Streamlit 部分版本下 session_state 缓存问题 | _render_import_panel | 已知 Streamlit 限制 |

### 5.2 非 overview 页面的问题

| 问题 | 影响页面 | 说明 |
|------|---------|------|
| `ui_components.py` 注入的全局 CSS 与侧边栏样式存在选择器冲突可能 | 所有页面 | 尚未全面测试 |
| discipline.py、research.py 的卡片样式与 overview.py 不一致 | 投资纪律、投研观点 | 两套 UI 方案并存导致 |
| placeholder 页面仅显示占位文字，无任何内容 | 9 个未实现页面 | 预期行为，但用户体验差 |

### 5.3 架构层面的已知限制

1. **双 iframe 架构的脆弱性**：overview.py 把页面拆成两个 iframe，中间夹一个原生 expander，高度全部硬编码。每次数据变化（持仓数量、告警数量）都需要重新校准 `height_bottom`。
2. **Streamlit 热更新问题**：在某些系统环境下，修改 `app_pages/overview.py` 后 Streamlit 不自动热更新，需要手动 `touch` 文件或重启进程。
3. **streamlit run 路径依赖**：必须在项目根目录执行 `streamlit run streamlit_app.py`，否则相对路径（数据库、`.env`）会失效。

---

## 六、后续接手建议

### 6.1 上手顺序

1. **先看**：`streamlit_app.py`（1 分钟摸清导航结构）
2. **再看**：`app_pages/overview.py` 的 `render()` 函数和 `_build_overview_html()` 函数（理解 iframe 方案）
3. **对比**：如果有 `ui_preview.html`，对照设计稿与当前实现的差距
4. **了解数据**：`app/models.py` + `app/analyzer.py`（理解 BalanceSheet 数据结构）

### 6.2 不建议轻易改动的文件

| 文件 | 原因 |
|------|------|
| `app/models.py` | 改动 ORM 模型需同步数据库迁移（当前无 Alembic，手动迁移） |
| `app/discipline/` 整个目录 | 投资纪律引擎逻辑复杂，有独立测试 |
| `app/csv_importer.py` | 字段映射逻辑已经过多次调整，改动容易破坏现有数据 |
| `streamlit_app.py` 的 `_NAV_SECTIONS` | 改导航顺序会影响 session_state 的 `current_page` 默认值 |

### 6.3 UI 优化建议（优先级排序）

#### 短期（收益最高）

1. **消除 iframe 双层方案**：考虑将 `overview.py` 的 iframe 方案逐步替换为 Streamlit 原生组件 + CSS 注入，这是根治高度计算和间距问题的最佳路径。但工作量较大，建议分模块渐进替换。

2. **统一 expander 样式**：当前 expander（导入/导出数据）通过 `st.markdown` CSS 注入样式，但与 iframe 中的卡片仍有差异。可以考虑把导入/导出功能也放入 iframe，或接受目前的视觉轻微差异。

3. **底部 iframe 自适应高度**：当前 `height_bottom` 硬编码，可以在 iframe 内部加 `window.frameElement.style.height = document.body.scrollHeight + 'px'` 的 JS 自动适应，但注意 Streamlit rerun 会重置。

#### 中期（体验提升）

4. **统一 discipline.py / research.py 的卡片样式**：这两个页面目前用 `ui_components.py` 方案，与 overview.py 风格不一致。建议提取通用卡片组件，统一 border-radius、shadow、padding。

5. **占位页面内容填充**：9 个未实现页面目前显示占位文字。至少可以加上"规划中"的功能介绍和一个时间线，提升用户体验。

#### 长期（架构优化）

6. **引入 Streamlit 自定义组件**：使用 `streamlit-component-lib`（React/TypeScript）创建真正可复用的卡片、图表组件，彻底摆脱 iframe 方案的限制。

7. **投研检索升级为 RAG**：当前 `app/research.py` 是基于关键词的多因子评分，可升级为 embedding + 向量数据库方案。

### 6.4 运行稳定性注意事项

1. **数据库**：首次运行自动创建 `data/wealthpilot.db`，不要手动修改数据库文件。如果需要重置数据，删除文件后重启即可。

2. **OpenAI API**：AI 分析报告、研报解析等功能依赖 OpenAI API，如果 API Key 未配置或 API 不可用，对应功能会报错但不影响其他页面。

3. **Streamlit 版本**：建议使用 `1.32.x` 至 `1.44.x` 范围内的版本。较新版本的 Streamlit 可能改变 CSS 选择器（如 `.stExpander` → 其他类名），导致样式注入失效。

4. **Python 版本**：推荐 3.9 ~ 3.11。3.12+ 在某些 SQLAlchemy 版本下有兼容性问题。

5. **热更新问题**：如果修改 `app_pages/overview.py` 后 Streamlit 不自动刷新，在终端执行：
   ```bash
   touch app_pages/overview.py
   ```

---

## 七、版本历史速查

| 版本 | 日期 | 主要内容 |
|------|------|---------|
| v1.9.1 | 2026-03-20 | 投研观点模块稳定性修复（17 项 bug 修复） |
| v1.9.0 | 2026-03-19 | 投研观点模块 MVP（AI 提炼 + 多因子检索） |
| v1.8.0 | 2026-03-19 | 全局导航重构 + 产品框架扩展 |
| v1.7.1 | 2026-03-17 | 投资纪律导航重建 + 资产配置图标准化 |
| v1.x   | 早期 | 基础功能建设（见 CHANGELOG.md）|

> 完整版本历史见 [CHANGELOG.md](./CHANGELOG.md)

---

## 八、文件速查索引

### UI 高度相关文件（接手 UI 优化时优先看）

```
streamlit_app.py          ← 侧边栏主题、导航样式
ui_components.py          ← 全局 CSS 设计 Token
app_pages/overview.py     ← 投资账户总览（最复杂，iframe 双层方案）
app_pages/discipline.py   ← 投资纪律页面样式
app_pages/research.py     ← 投研观点页面样式
```

### 业务逻辑文件（慎改）

```
app/models.py             ← 数据库结构（改动需手动迁移）
app/analyzer.py           ← 分析引擎核心
app/discipline/           ← 投资纪律引擎（有测试覆盖）
app/ai_advisor.py         ← LLM 调用（改动影响 AI 功能）
```

### 可以安全扩展的文件

```
app_pages/placeholder.py  ← 占位页面（可安全替换为真实内容）
app_pages/strategy.py     ← 投资决策（框架已建，可填充内容）
app/config.py             ← 配置常量（调整阈值、模型名）
```
