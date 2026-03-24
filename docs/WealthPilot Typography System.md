# WealthPilot Typography System

> 本文档定义了 WealthPilot 项目的全局字体层级规范（Typography System）。
> 所有页面、模块、组件的文字渲染均需严格遵循此规范，不再使用硬编码的字号和颜色。

---

## 一、最终版 Typography 层级表

### 1.1 主内容区（浅色背景）

| 层级 | CSS Token | 字号 | 字重 | 颜色 | 行高 | 适用场景 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **H1** | `--wp-text-h1` | 20px | 700 | `#1B2A4A` | 1.4 | 页面主标题（如：投资账户总览） |
| **H2** | `--wp-text-h2` | 14px | 600 | `#1B2A4A` | 1.4 | 模块级大标题（如：导入/导出数据） |
| **Title** | `--wp-text-title` | 13px | 600 | `#374151` | 1.4 | 卡片标题、Tabs 激活态、Panel 标题 |
| **Nav** | `--wp-text-nav` | 12px | 500 | `#6B7280` | 1.4 | Tabs 非激活态、次级导航文字 |
| **Body** | `--wp-text-body` | 13px | 400 | `#374151` | 1.5 | 表格正文、普通正文内容 |
| **Desc** | `--wp-text-desc` | 12px | 400 | `#6B7280` | 1.5 | 说明文案、提示文字、内联说明 |
| **Meta** | `--wp-text-meta` | 11px | 400 | `#9CA3AF` | 1.4 | 控件选项、占位文字、辅助小字、Badge |
| **Label** | `--wp-text-label` | 11px | 600 | `#9CA3AF` | 1.4 | 表头（需 uppercase）、KPI 标签 |

### 1.2 导航栏专属（深色背景）

| 层级 | CSS Token | 字号 | 字重 | 颜色 | 附加样式 | 适用场景 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Nav-Brand** | `--wp-nav-brand` | 15px | 700 | `#FFFFFF` | - | Logo 品牌名 |
| **Nav-Sub** | `--wp-nav-sub` | 11px | 400 | `rgba(200,214,232,0.65)` | - | Logo 副标题 |
| **Nav-Section** | `--wp-nav-section` | 10px | 600 | `rgba(200,214,232,0.45)` | uppercase, letter-spacing: 0.8px | 分组标题（小标签风格） |
| **Nav-Item** | `--wp-nav-item` | 13px | 400 | `rgba(200,214,232,0.75)` | - | 菜单项 normal 态 |
| **Nav-Active** | `--wp-nav-active` | 13px | 600 | `#93C5FD` | - | 菜单项 active 态 |

---

## 二、统一落地文件

所有 Typography Token 和全局基础字体规则将集中写入：
**`ui_components.py` 中的 `inject_global_css()` 函数**

**原因：**
1. `inject_global_css()` 已经在 `streamlit_app.py` 和各个子页面中被调用，是真正的全局 CSS 入口。
2. 将 Token 定义在 `:root` 下，后续所有页面的 CSS 注入块（如 `overview.py` 中的 `<style>`）都可以直接使用 `var(--wp-text-title)` 等变量，不再硬编码具体数值。

---

## 三、将被替换的旧样式

1. **导航栏（`streamlit_app.py`）**：
   - 移除硬编码的 `font-size:13px;font-weight:700;color:rgba(255,255,255,0.88)`（分组标题）
   - 移除硬编码的 `font-size:15px` 和 `font-size:11px`（Logo 区域）
   - 增加 `section[data-testid="stSidebar"] .stButton > button p` 的字体覆盖规则，解决 16px 覆盖 13px 的问题。

2. **导入/导出模块（`overview.py`）**：
   - 替换 Tabs 的 `12px / 600 / #1E3A5F` 为 `var(--wp-text-nav)` 和 `var(--wp-text-title)`
   - 替换说明文字的 `14px / 400 / #31333F` 为 `var(--wp-text-desc)`
   - 替换平台 radio label 的 `14px / 400` 为 `var(--wp-text-meta)`
   - 移除蓝底提示条的 `16px / 400 / #0054A3` 和蓝底背景，改为 `var(--wp-text-desc)` 内联说明

3. **投资账户总览页（`ui_components.py`）**：
   - 将现有的 `.card-title`、`.table-cell`、`th` 等硬编码字体值，全部替换为对应的 `--wp-text-*` Token。

---

## 四、第一阶段应用区域

1. **左侧导航栏**：全面应用 Nav 专属 Token，解决分组标题层级和菜单项 16px 覆盖问题。
2. **投资账户总览页**：将 iframe 内的卡片、表格、KPI 标签全部接入 Typography Token。
3. **导入/导出数据模块**：彻底解决内部字体层级混乱，应用 Desc、Meta、Title 等 Token，去掉蓝底提示条背景。

---

*文档生成时间：2026-03-22*
