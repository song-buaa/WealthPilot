# WealthPilot UI 设计规范 — Phase 2 前端实施版

> 本文档是 Phase 2 前端骨架的施工依据。
> Claude Code 在搭建任何页面之前必须先读本文档。
> 来源：现有 Streamlit HTML、ui_preview-有导航栏.html、Typography System、YouMind 对话页面截图。

---

## 一、Design Tokens（设计变量）

所有颜色、尺寸、阴影统一通过 CSS 变量管理，在 `index.css` 的 `:root` 中定义，Tailwind 通过 `@theme` 扩展注册。

### 1.1 颜色系统

```css
:root {
  /* 主色调 — Ocean 深蓝 */
  --ocean-900: #0F1E35;   /* 最深，侧边栏底部渐变 */
  --ocean-800: #1B2A4A;   /* 侧边栏主色、页面标题色、主KPI背景 */
  --ocean-700: #243558;
  --ocean-600: #2D4A7A;   /* 渐变终点 */
  --ocean-50:  #F4F6FA;   /* 页面背景 */

  /* 蓝色 — 交互色 */
  --blue-500:  #3B82F6;   /* 品牌图标、active 边框、按钮 */
  --blue-200:  #BFDBFE;
  --blue-100:  #DBEAFE;

  /* 状态色 */
  --green-600: #16A34A;   /* 盈利、通过 */
  --green-100: #DCFCE7;
  --red-600:   #DC2626;   /* 亏损、警告、阻止 */
  --red-100:   #FEE2E2;
  --amber-500: #F59E0B;   /* 谨慎、注意 */
  --amber-100: #FEF3C7;

  /* 中性色 */
  --gray-700:  #374151;   /* 正文主色 */
  --gray-500:  #6B7280;   /* 次要文字 */
  --gray-400:  #9CA3AF;   /* 辅助文字、表头 */
  --gray-200:  #E5E7EB;   /* 分割线、边框 */
  --gray-100:  #F3F4F6;   /* 卡片内背景、hover */
  --white:     #FFFFFF;

  /* 尺寸 */
  --sidebar-w: 220px;
  --radius:    12px;
  --radius-sm: 8px;

  /* 阴影 */
  --shadow-sm:   0 1px 3px rgba(15,30,53,0.07), 0 1px 2px rgba(15,30,53,0.04);
  --shadow-dark: 0 6px 20px rgba(15,30,53,0.28);  /* 主KPI卡专用 */
}
```

**Tailwind v4 注册方式（在 `src/index.css` 中）：**

```css
@import "tailwindcss";

@theme {
  --color-ocean-900: #0F1E35;
  --color-ocean-800: #1B2A4A;
  --color-ocean-50:  #F4F6FA;
  --color-blue-500:  #3B82F6;
  /* ...其余同上，前缀统一加 --color- */
  --sidebar-width: 220px;
  --radius-card: 12px;
}
```

---

### 1.2 字体系统（Typography Tokens）

字体栈：`'PingFang SC', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, sans-serif`

#### 主内容区（浅色背景）

| 层级 | Token | 字号 | 字重 | 颜色 | 行高 | 适用场景 |
|---|---|---|---|---|---|---|
| H1 | `--wp-text-h1` | 20px | 700 | `#1B2A4A` | 1.4 | 页面主标题 |
| H2 | `--wp-text-h2` | 14px | 600 | `#1B2A4A` | 1.4 | 模块级标题 |
| Title | `--wp-text-title` | 13px | 600 | `#374151` | 1.4 | 卡片标题、Panel标题 |
| Body | `--wp-text-body` | 13px | 400 | `#374151` | 1.5 | 表格正文、普通内容 |
| Desc | `--wp-text-desc` | 12px | 400 | `#6B7280` | 1.5 | 说明文案、提示文字 |
| Meta | `--wp-text-meta` | 11px | 400 | `#9CA3AF` | 1.4 | 占位文字、Badge、小字 |
| Label | `--wp-text-label` | 11px | 600 | `#9CA3AF` | 1.4 | 表头（需uppercase）、KPI标签 |

#### 导航栏专属（深色背景）

| 层级 | Token | 字号 | 字重 | 颜色 | 备注 |
|---|---|---|---|---|---|
| Nav-Brand | `--wp-nav-brand` | 15px | 700 | `#FFFFFF` | Logo品牌名 |
| Nav-Sub | `--wp-nav-sub` | 11px | 400 | `rgba(200,214,232,0.65)` | Logo副标题 |
| Nav-Section | `--wp-nav-section` | 10px | 600 | `rgba(200,214,232,0.45)` | 分组标题，uppercase + letter-spacing:0.8px |
| Nav-Item | `--wp-nav-item` | 13px | 400 | `rgba(200,214,232,0.75)` | 菜单项 normal 态 |
| Nav-Active | `--wp-nav-active` | 13px | 600 | `#93C5FD` | 菜单项 active 态 |

---

## 二、全局布局

### 2.1 整体结构

```
┌─────────────────────────────────────────────────┐
│  侧边栏 220px（固定）  │  主内容区（flex: 1）    │
│  深色渐变背景          │  浅色背景 #F4F6FA       │
│                       │  overflow-y: auto       │
└─────────────────────────────────────────────────┘
```

```css
body {
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: var(--ocean-50);
}
```

### 2.2 内容区内边距

```css
.content-area {
  flex: 1;
  overflow-y: auto;
  padding: 24px 24px 24px 24px;
  min-width: 0;
}
/* 滚动条 */
.content-area::-webkit-scrollbar { width: 4px; }
.content-area::-webkit-scrollbar-thumb { background: var(--gray-200); border-radius: 2px; }
```

---

## 三、侧边栏规范

参考来源：`ui_preview-有导航栏.html`（比现有 Streamlit 版本更好看，以此为准）

### 3.1 背景与尺寸

```css
.sidebar {
  width: 220px;
  flex-shrink: 0;
  background: linear-gradient(180deg, #1B2A4A 0%, #0F1E35 100%);
  border-right: 1px solid rgba(255,255,255,0.05);
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}
.sidebar::-webkit-scrollbar { width: 0; }  /* 隐藏滚动条 */
```

### 3.2 品牌区（Logo）

```
padding: 20px 16px 16px
border-bottom: 1px solid rgba(255,255,255,0.07)

[图标 36×36 圆角10px 蓝色渐变] [WealthPilot 15px/700/白] 
                                [个人智能投顾 11px/400/rgba(255,255,255,0.38)]
```

品牌图标背景：`linear-gradient(135deg, #3B82F6, #1D4ED8)`，阴影：`0 2px 8px rgba(59,130,246,0.4)`

### 3.3 导航分组

**分组标题（Section Header）：**
```
padding: 14px 12px 6px
icon 14px + label 10px/600/rgba(200,214,232,0.45) uppercase letter-spacing:0.8px
```

**导航项（Nav Item）：**
```css
.nav-item {
  padding: 6px 8px 6px 28px;  /* 左缩进体现层级 */
  border-radius: 7px;
  font-size: 13px;
  font-weight: 400;
  color: rgba(255,255,255,0.48);
  transition: all 0.14s;
  margin-bottom: 1px;
}
.nav-item:hover {
  background: rgba(255,255,255,0.06);
  color: rgba(255,255,255,0.78);
}
.nav-item.active {
  background: rgba(59,130,246,0.16);
  color: #93C5FD;
  font-weight: 600;
  border-left: 2px solid #3B82F6;
  padding-left: 26px;  /* 减2px抵消border宽度 */
}
```

**分割线：**
```css
.nav-divider {
  height: 1px;
  background: rgba(255,255,255,0.07);
  margin: 6px 12px;
}
```

### 3.4 导航结构（以 ui_preview-有导航栏.html 为准）

```
📊 WealthPilot
   个人智能投顾系统
─────────────────────────
📈 投资规划              ← Section Header
   用户画像和投资目标
   新增资产配置
   投资账户总览           ← 默认 active
   投资纪律
   投研观点
   投资决策
   投资记录
   收益分析
─────────────────────────
🏠 财务规划              ← Section Header
   生活账户总览
   养老规划
   购房规划
   消费规划
─────────────────────────
📊 资产负债总览           ← Section Header
   个人资产负债总览
   家族资产负债总览
```

**Phase 2 实施说明：** 当前只有四个模块有实际功能（投资账户总览、投资纪律、投研观点、投资决策），其余导航项渲染出来但点击后展示"功能开发中"占位页即可，不影响导航栏完整呈现。

---

## 四、页面标题规范

每个页面顶部统一结构：

```html
<div class="page-header">
  <!-- 图标容器：38×38 圆角10px ocean渐变背景 -->
  <div class="page-header-icon">📊</div>
  <div>
    <div class="page-title">投资账户总览</div>         <!-- 20px/700/#1B2A4A -->
    <div class="page-subtitle">共 8 个持仓 · 更新于今天</div>  <!-- 12px/400/#9CA3AF -->
  </div>
</div>
```

```css
.page-header { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
.page-header-icon {
  width: 38px; height: 38px; border-radius: 10px;
  background: linear-gradient(135deg, #1B2A4A, #2D4A7A);
  display: flex; align-items: center; justify-content: center; font-size: 17px;
}
.page-title { font-size: 20px; font-weight: 700; color: #1B2A4A; letter-spacing: -0.3px; }
.page-subtitle { font-size: 12px; color: #9CA3AF; margin-top: 1px; }
```

---

## 五、卡片规范

### 5.1 基础卡片

```css
.card {
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 1px 3px rgba(15,30,53,0.07), 0 1px 2px rgba(15,30,53,0.04);
}
.card-title {
  font-size: 13px; font-weight: 600; color: #374151;
  display: flex; align-items: center; gap: 6px;
  margin-bottom: 16px;
}
.card-title-badge {
  margin-left: auto; font-size: 11px; font-weight: 400; color: #9CA3AF;
}
```

### 5.2 KPI 卡片三级体系

**主卡（Primary）— 总资产，深色背景：**
```css
.kpi-primary {
  background: linear-gradient(135deg, #1B2A4A 0%, #0F1E35 100%);
  border-radius: 12px; padding: 20px 24px;
  box-shadow: 0 6px 20px rgba(15,30,53,0.28);
  min-height: 100px;
}
/* 标签：11px/600/rgba(255,255,255,0.48) uppercase letter-spacing:0.6px */
/* 数值：28px/700/#fff letter-spacing:-1px tabular-nums */
/* 子信息：13px/rgba(255,255,255,0.55)，强调值rgba(255,255,255,0.9)/600 */
```

**次卡（Secondary）— 净资产、收益等：**
```css
.kpi-secondary {
  background: #FFFFFF; border: 1px solid #E5E7EB;
  border-radius: 12px; padding: 16px 18px; min-height: 100px;
  box-shadow: var(--shadow-sm);
}
/* 标签：11px/600/#9CA3AF uppercase letter-spacing:0.5px */
/* 数值：20px/700/#1B2A4A，盈利用#16A34A，亏损用#DC2626 */
/* Delta标签：11px/600，带箭头图标 */
```

**辅卡（Tertiary）— 杠杆率、预警数等：**
```css
.kpi-tertiary {
  /* 同Secondary但数值 18px/600/#374151 */
  /* 警示状态数值用 #DC2626 */
}
```

**KPI 网格布局：**
```css
.kpi-row {
  display: grid;
  grid-template-columns: 2fr 1fr 1fr;  /* 主卡占2份 */
  gap: 12px;
  margin-bottom: 16px;
}
```

---

## 六、表格规范

### 6.0 全局数字格式规范

适用于所有页面的数字展示，不仅限于表格：

| 类型 | 规则 | 示例 |
|---|---|---|
| 金额 | 千分位分隔，保留 2 位小数 | `¥1,234,567.89` |
| 百分比 | 保留 1 位小数 | `12.5%` |
| 涨跌幅 | 保留 2 位小数，正数加 `+` | `+3.45%` / `-2.10%` |
| 数量/份额 | 无小数或保留 2 位，视品种而定 | `500` / `1,234.56` |
| 所有数字 | 统一使用 `font-variant-numeric: tabular-nums` | 对齐列宽，避免跳动 |

---

### 6.1 表格样式

```css
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  font-size: 11px; font-weight: 600; color: #9CA3AF;
  text-transform: uppercase; letter-spacing: 0.5px;
  padding: 8px 12px; border-bottom: 1px solid #E5E7EB;
  text-align: left;
}
tbody td { padding: 10px 12px; border-bottom: 1px solid #F3F4F6; color: #374151; }
tbody tr:hover { background: #F9FAFB; }
tbody tr:last-child td { border-bottom: none; }

/* 数值对齐：数字列右对齐，tabular-nums */
.td-number { text-align: right; font-variant-numeric: tabular-nums; }
/* 盈亏色 */
.text-profit { color: #16A34A; font-weight: 600; }
.text-loss   { color: #DC2626; font-weight: 600; }
```

---

## 七、投资决策页专项规范

参考来源：YouMind 对话页面截图（双栏布局 + 对话气泡风格）

### 7.1 整体布局

参考 Cursor 的双栏设计：左边是对话内容+固定输入框，右边是代码/详情内容。
两栏**完全独立滚动**，高度都撑满视口，互不影响。

```
┌──────────────────────────────┬─────────────────────────┐
│  左：对话区（flex:1）         │  右：决策链路面板（300px）│
│  ┌──────────────────────┐    │                         │
│  │  消息列表             │    │  [可独立上下滚动]        │
│  │  overflow-y: auto    │    │                         │
│  │  flex:1              │    │                         │
│  └──────────────────────┘    │                         │
│  ┌──────────────────────┐    │                         │
│  │  输入框（固定底部）    │    │                         │
│  │  flex-shrink: 0      │    │                         │
│  └──────────────────────┘    │                         │
└──────────────────────────────┴─────────────────────────┘
```

**关键交互要求：**
- 左右两栏各自独立滚动，互不联动
- 左栏输入框**始终固定在左栏底部**，不随消息列表滚动而消失
- 右栏决策链路内容可独立上下滚动
- 整个页面本身**不滚动**，高度 = 视口高度，由两栏内部各自处理溢出

**宽度约束（防止双栏变形）：**
- 左栏：`flex: 1`，`min-width: 640px`
- 右栏：默认 `300px`，`min-width: 280px`，不随内容撑宽（`flex-shrink: 0`）

```css
/* 投资决策页：撑满整个内容区，不允许页面级滚动 */
.decision-page {
  height: 100%;          /* 撑满父容器 */
  display: flex;
  overflow: hidden;      /* 页面本身不滚动 */
}

/* 左栏：对话区 */
.chat-area {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;  /* 上：消息列表 / 下：输入框 */
  border-right: 1px solid #E5E7EB;
  overflow: hidden;        /* 自身不滚动，交给子元素 */
}

/* 消息列表：占满剩余高度，独立滚动 */
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}
.chat-messages::-webkit-scrollbar { width: 4px; }
.chat-messages::-webkit-scrollbar-thumb { background: #E5E7EB; border-radius: 2px; }

/* 输入框区：固定在左栏底部，不参与滚动 */
.chat-input-area {
  flex-shrink: 0;          /* 不被压缩 */
  padding: 16px 24px;
  border-top: 1px solid #E5E7EB;
  background: #FFFFFF;
}

/* 右栏：决策链路面板，独立滚动 */
.explain-panel {
  width: 300px;
  flex-shrink: 0;
  overflow-y: auto;        /* 独立滚动 */
  padding: 20px;
  background: #FFFFFF;
  transition: width 0.2s ease;
}
.explain-panel::-webkit-scrollbar { width: 4px; }
.explain-panel::-webkit-scrollbar-thumb { background: #E5E7EB; border-radius: 2px; }
.explain-panel.collapsed { width: 0; padding: 0; overflow: hidden; }
```

**React 组件结构对应关系：**
```jsx
<div className="decision-page">
  <div className="chat-area">
    <div className="chat-messages">   {/* 独立滚动 */}
      {messages.map(...)}
    </div>
    <div className="chat-input-area"> {/* 固定底部 */}
      <ChatInput />
    </div>
  </div>
  <div className="explain-panel">     {/* 独立滚动 */}
    <ExplainContent />
  </div>
</div>
```
```

### 7.2 对话气泡

参考 YouMind 截图风格：简洁、内容优先，不过度装饰。

**用户消息：**
```css
.msg-user {
  align-self: flex-end;
  background: #1B2A4A;
  color: #FFFFFF;
  border-radius: 18px 18px 4px 18px;
  padding: 10px 14px;
  max-width: 70%;
  font-size: 14px; line-height: 1.5;
}
```

**AI 消息：**
```css
.msg-ai {
  align-self: flex-start;
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  border-radius: 4px 18px 18px 18px;
  padding: 12px 16px;
  max-width: 80%;
  font-size: 14px; line-height: 1.6;
  box-shadow: var(--shadow-sm);
}
```

**Stage 进度提示（流式输出中）：**
```css
.msg-stage {
  align-self: flex-start;
  background: transparent;
  color: #9CA3AF;
  font-size: 12px;
  padding: 4px 0;
  display: flex; align-items: center; gap: 6px;
}
/* 前面加动态 loading 小点 */
```

**结论徽章（done事件后展示）：**
```css
.conclusion-badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 10px; border-radius: 20px;
  font-size: 12px; font-weight: 600; margin-top: 8px;
}
.badge-cautious  { background: #FEF3C7; color: #92400E; }
.badge-buy       { background: #DCFCE7; color: #14532D; }
.badge-sell      { background: #FEE2E2; color: #7F1D1D; }
.badge-hold      { background: #F3F4F6; color: #374151; }
```

### 7.3 输入区

```css
.chat-input-area {
  padding: 16px 0 0;
  border-top: 1px solid #E5E7EB;
  margin-top: auto;
}
.chat-input-box {
  display: flex; gap: 8px; align-items: flex-end;
}
.chat-input {
  flex: 1;
  border: 1px solid #E5E7EB;
  border-radius: 12px;
  padding: 10px 14px;
  font-size: 14px; color: #374151;
  resize: none; min-height: 44px; max-height: 120px;
  outline: none;
  transition: border-color 0.15s;
}
.chat-input:focus { border-color: #3B82F6; }
.chat-send-btn {
  width: 36px; height: 36px;
  background: #1B2A4A; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  color: white; cursor: pointer; flex-shrink: 0;
  transition: background 0.15s;
}
.chat-send-btn:hover { background: #2D4A7A; }
```

### 7.4 Explain Panel 内容结构

```
[Panel Header]
  标题：决策详情    [折叠按钮 ✕]

[意图区]
  资产：理想汽车    操作：加仓判断
  置信度：91%

[各阶段折叠区]
  ▶ 规则校验     ✅ 通过
  ▶ 信号分析     ⚠️ 谨慎
  ▶ AI推理      展开显示推理过程

[结论区]
  结论级别 Badge + 一句话总结
```

---

## 八、状态与反馈规范

### 8.1 空状态

```css
.empty-state {
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  padding: 48px 24px; color: #9CA3AF; text-align: center;
}
/* icon 40px，title 14px/500/#6B7280，desc 13px/400/#9CA3AF */
```

### 8.2 预警/提示条

```css
/* 不用蓝底大块提示，改用内联小标签风格 */
.alert-inline {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 12px; border-radius: 8px;
  font-size: 12px; font-weight: 500;
}
.alert-warn { background: #FEF3C7; color: #92400E; border: 1px solid #FDE68A; }
.alert-error { background: #FEE2E2; color: #7F1D1D; border: 1px solid #FECACA; }
.alert-info { background: #DBEAFE; color: #1E40AF; border: 1px solid #BFDBFE; }
```

### 8.3 加载状态

使用 `lucide-react` 的 `Loader2` 图标 + `animate-spin`，不自定义加载动画。

---

### 8.4 占位页（未实现功能）

导航中尚未实现的页面点击后展示统一占位样式。**文字内容保留现有 Streamlit 版本的原文，不改动。** 只统一视觉样式：

```css
.placeholder-page {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 48px 24px;
  text-align: center;
  color: #9CA3AF;
}
.placeholder-icon {
  font-size: 40px;
  margin-bottom: 16px;
  opacity: 0.5;
}
.placeholder-title {
  font-size: 16px; font-weight: 600; color: #6B7280;
  margin-bottom: 8px;
}
.placeholder-desc {
  font-size: 13px; color: #9CA3AF; max-width: 320px; line-height: 1.6;
}
```

布局要求：内容整体垂直居中于内容区，不靠顶部堆叠。

---
## 九、图标使用规范

- **统一使用 `lucide-react`**，不手写 SVG，不引入其他图标库
- 导航栏图标用 emoji（📊📋🔬💡）保持现有风格
- 功能性图标（按钮、操作、状态）全部用 lucide-react
- 常用图标对应：
  - 上传：`Upload`
  - 下载/导出：`Download`
  - 删除：`Trash2`
  - 编辑：`Pencil`
  - 展开/折叠：`ChevronRight` / `ChevronDown`
  - 发送：`ArrowUp`（参考YouMind风格，圆形按钮内）
  - 加载：`Loader2`（配合 `animate-spin`）
  - 警告：`AlertTriangle`
  - 成功：`CheckCircle2`
  - 关闭：`X`

---

## 十、Tailwind v4 使用约定

1. **无 `tailwind.config.js`**，所有自定义通过 `index.css` 的 `@theme {}` 块配置
2. **颜色引用**：`text-ocean-800`、`bg-ocean-50`、`border-gray-200`（需在 @theme 注册）
3. **不使用 `@apply`** 封装复杂组件，直接写 utility class 或单独 CSS
4. **间距基准**：4px（Tailwind 默认），即 `gap-3` = 12px，`p-5` = 20px，`p-6` = 24px
5. **圆角**：卡片用 `rounded-xl`（12px），小组件用 `rounded-lg`（8px），按钮用 `rounded-full` 或 `rounded-lg`

---

## 十一、各页面重点说明

### 账户总览（Dashboard）
- 整体与现有 Streamlit 版本保持一致，不大改内容，只重新用 React 实现
- KPI 三级体系严格按本文第五节
- 图表用 **recharts**（shadcn/ui 默认配套，已在 React 生态验证）

### 投资纪律（Discipline）
- 主体是规则列表 + 交易评估表单
- 评估结果用 alert-inline 样式展示，不用大块颜色背景

### 投研观点（Research）
- 卡片列表为主，支持关键词搜索
- 新增观点用侧滑抽屉（shadcn/ui `Sheet` 组件）

### 投资决策（Decision）
- 严格按本文第七节
- **SSE 消费**：用原生 `EventSource` 或 `fetch` + `ReadableStream`，不引入额外库

---

## 十二、Phase 2 施工优先级

### P0（必须完成，Phase 2 验收标准）
- Sidebar 导航（完整三分组结构，active 状态，占位页跳转）
- PageHeader（每个页面顶部标题区）
- Card / KPI 三级体系
- Table（持仓列表、规则列表等）
- Decision 双栏框架（独立滚动 + 固定输入框 + ExplainPanel 骨架）
- ChatInput + 消息气泡（user / AI / stage / conclusion badge）

### P1（Phase 2 尽量完成，不强求）
- Alert inline 样式
- EmptyState 空状态
- Sheet 侧滑抽屉（投研观点新增）
- 表单组件（交易评估输入）

### P2（留给 Phase 3，Phase 2 不做）
- 响应式移动端适配
- 动画与过渡效果
- 多主题切换
- 复杂拖拽 / 可配置布局
- 自定义图表皮肤
- 细节 hover 打磨、滚动条美化

---

## 十三、Phase 2 不做什么

以下内容**明确禁止**在 Phase 2 实现，遇到相关需求一律推迟到 Phase 3：

- ❌ 响应式/移动端适配
- ❌ 动效、过渡动画（除 Explain Panel 折叠的 `transition: width 0.2s`）
- ❌ 多主题 / 暗色模式
- ❌ 拖拽排序、可配置布局
- ❌ 图表皮肤定制（用 recharts 默认样式即可）
- ❌ 像素级视觉打磨（间距微调、hover 细节）
- ❌ 任何 Streamlit 版本没有的新功能

**Phase 2 的唯一目标：四个模块功能可用，视觉方向正确，不求精致。**

---

*文档版本：1.1 | 基于现有设计素材整理 | 供 Claude Code Phase 2 施工使用*
*V1.1 更新：补充全局数字格式规范、双栏宽度约束、占位页样式规范、施工优先级、Phase 2 禁止事项*
