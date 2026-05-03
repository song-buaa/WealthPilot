# WealthPilot 架构迁移方案 V1.2

> 文档版本：V1.2 | 日期：2026-04-02 | 状态：待执行
>
> **V1.2 相较 V1.1 的更新：**
> - 前端技术栈对齐 CCBAI-Demo 实战验证版本（Tailwind v4、React Router v7）
> - 明确 shadcn/ui 的定位：复杂组件补齐，而非全量依赖
> - 新增"UI参考图"策略，解决 PC 端无截图参考的问题
> - 图标方案从手写 SVG 改为 lucide-react
> - CLAUDE.md 补充前端配置基线说明

---

## 一、背景与目标

### 现状问题

WealthPilot 目前基于 Streamlit 构建，已完成四个核心模块：

- 投资账户总览
- 投资纪律
- 投研观点
- 投资决策（含多轮对话 + Explain Panel）

Streamlit 作为快速原型工具已完成使命，但继续深挖存在明显瓶颈：

| 问题维度 | 具体表现 |
|---|---|
| UI 上限低 | 组件样式难以定制，复杂交互受框架限制 |
| 架构耦合 | 业务逻辑与页面渲染混在一起，难以维护 |
| 扩展性差 | 接入自动化工具、外部服务的边界模糊 |
| 产品化受阻 | 无法做成标准分发形态，用户体验差 |

### 迁移目标

**V1 迁移目标：Local-First Web App（本地优先的 Web 应用）**

- 用户在本地启动后端服务，用浏览器访问
- 数据存储在本地，隐私安全
- UI 自由度完全不受框架限制
- 一套代码，未来可低成本向 SaaS 或 Desktop 演进

---

## 二、产品形态与演进路线

### V1：Local-First Web App（当前迁移目标）

```
用户本地机器
├── 后端服务（FastAPI，Python）    ← 数据处理、AI调用、业务逻辑
├── 数据库（SQLite）               ← 本地数据，不上云
└── 浏览器访问 http://localhost    ← 完整Web UI体验
```

选择这个形态的原因：

- "本地数据导入"不需要 Electron，File API 完全够用
- "接 OpenClaw"通过 FastAPI 后端代理即可，不需要桌面壳
- 一套代码，无需维护多个形态，单人开发成本可控
- 架构稳定后向任何方向演进的成本都很低

### V2（可选）：Desktop 封装

当前阶段**暂不引入** Desktop 封装，待 V1 架构稳定、核心功能打磨完成后再评估。届时优先考虑 **Tauri**（轻量 WebView 壳，前端代码完全复用，不依赖 Node 运行时）而非 Electron（包体过大）或 pywebview（渲染一致性差、坑多）。

### 分发方式（V1阶段）

| 渠道 | 内容 |
|---|---|
| GitHub 仓库 | 源码、README、架构文档、截图 |
| GitHub Releases | 一键启动脚本（`.command` for macOS / `.bat` for Windows） |
| GitHub Pages（可选） | 产品介绍页、功能截图展示 |

**用户操作路径：**
`看到项目 → 下载启动脚本 → 双击运行 → 浏览器自动打开 WealthPilot`

---

## 三、目标技术栈

### 前端技术栈（对齐 CCBAI-Demo 实战验证版本）

| 层级 | 技术选型 | 说明 |
|---|---|---|
| 框架 | **React 19 + Vite** | 与 CCBAI-Demo 完全一致，已验证稳定 |
| 样式 | **Tailwind CSS v4**（`@tailwindcss/vite` 插件） | v4 无需 `tailwind.config.js`，配置更简洁；与 CCBAI-Demo 一致 |
| 路由 | **React Router v7**（HashRouter） | 与 CCBAI-Demo 一致；HashRouter 适合本地单页应用 |
| 组件库 | **shadcn/ui**（按需引入） | 仅用于复杂组件（Table、Dialog、Tabs 等）；简单组件继续用 Tailwind 手写 |
| 图标 | **lucide-react** | shadcn/ui 默认配套；不手写 SVG，不引入其他图标库 |
| 状态管理 | **Zustand** | 仅用于投资决策页跨组件状态（对话 ↔ Explain Panel 联动） |
| 语言 | **TypeScript** | — |

### 后端技术栈

| 层级 | 技术选型 | 说明 |
|---|---|---|
| 后端框架 | **FastAPI** | Python，复用现有逻辑 |
| 数据库 | **SQLite**（不动） | 现有模型全部保留 |
| 业务逻辑 | 抽出为 services 层 | Python，基本不重写 |
| AI 调用 | 现有逻辑（不动） | 直接复用 |
| 启动方式 | 一键启动脚本 | 同时拉起前端 dev server + FastAPI |

### 关于 shadcn/ui 的使用策略

shadcn/ui 与纯 Tailwind 手写不是非此即彼的关系，两者配合使用：

| 场景 | 方案 |
|---|---|
| 简单组件（按钮、卡片、输入框、导航栏） | Tailwind utility class 手写，风格延续 CCBAI-Demo |
| 复杂组件（数据表格、模态框、Tabs、下拉菜单、Toast） | shadcn/ui 按需引入，避免重复造轮子 |
| 图标 | lucide-react（不手写 SVG，不引入 Heroicons / Font Awesome） |

**核心原则：shadcn/ui 是工具箱，不是全套 UI 框架。** 用它补齐复杂组件，简单的地方继续手写 Tailwind，保持代码风格统一。

### 为什么不用 Next.js

Next.js 的核心价值是 SSR/SEO，适合内容型网站。WealthPilot 是工具型应用，用纯 React（Vite）更轻、更快，且 CCBAI-Demo 已经验证了这套方案的可行性。

---

## 四、UI 参考图策略（Phase 2 施工前准备）

**WealthPilot 是 PC 端工具，没有现成截图参考，这是 Claude Code 生成 UI 质量不稳定的主要风险。**

CCBAI-Demo 能做好，很大程度上是因为有手机端截图作为对齐目标。WealthPilot 需要用同样的方法，提前准备参考图。

### 推荐参考来源

以下产品的截图风格与 WealthPilot 的定位（深色/中性色调、高信息密度、专业工具感）高度契合：

| 参考产品 | 参考哪个部分 |
|---|---|
| **Linear** | 左侧导航 + 内容区整体布局、列表页设计 |
| **Vercel Dashboard** | 数据卡片、指标展示、整体配色 |
| **Notion** | 侧边栏折叠交互、内容区排版 |
| **Perplexity** | 对话区 + 右侧信息面板的双栏布局（投资决策页参考） |

### 操作建议

在启动 Phase 2 之前：
1. 截取 2-3 张参考图（重点是整体布局和投资决策页的双栏设计）
2. 在 Phase 2 提示词里附上参考图，明确说"整体风格参考 X，投资决策页布局参考 Y"
3. 参考图比文字描述的效果强 5 倍，这一步不要省略

---

## 五、目标代码结构

```
WealthPilot/
│
├── backend/                          # Python 后端
│   ├── main.py                       # FastAPI 入口，含 CORS 配置
│   ├── api/                          # API 路由层（新建）
│   │   ├── portfolio.py              # 账户总览接口
│   │   ├── discipline.py             # 投资纪律接口
│   │   ├── research.py               # 投研观点接口
│   │   ├── decision.py               # 投资决策接口
│   │   └── tasks.py                  # 异步任务接口（预留骨架，供自动化层调用）
│   ├── services/                     # 业务逻辑层（从 Streamlit 抽出）
│   │   ├── portfolio_service.py
│   │   ├── discipline_service.py
│   │   ├── research_service.py
│   │   └── decision_service.py
│   ├── models/                       # 数据模型（现有 SQLAlchemy，不动）
│   ├── core/                         # LLM 调用、解析器等（现有逻辑，不动）
│   └── db/                           # SQLite 数据库文件（不动）
│
├── frontend/                         # React 前端
│   ├── src/
│   │   ├── pages/                    # 页面组件
│   │   │   ├── Dashboard.tsx         # 账户总览
│   │   │   ├── Discipline.tsx        # 投资纪律
│   │   │   ├── Research.tsx          # 投研观点
│   │   │   └── Decision.tsx          # 投资决策（最复杂）
│   │   ├── components/               # 可复用组件
│   │   │   ├── layout/               # 布局（侧边栏、顶栏）— Tailwind 手写
│   │   │   ├── charts/               # 图表组件
│   │   │   └── shared/               # 通用组件（简单的 Tailwind 手写，复杂的用 shadcn）
│   │   ├── store/                    # Zustand 状态管理
│   │   │   └── decisionStore.ts      # 投资决策页全局状态
│   │   ├── lib/
│   │   │   └── api.ts                # 后端 API 调用封装
│   │   └── App.tsx
│   ├── package.json
│   └── vite.config.ts                # 含 proxy 配置，解决本地跨域
│
├── scripts/
│   ├── start.command                 # macOS 一键启动
│   └── start.bat                     # Windows 一键启动
│
├── CLAUDE.md                         # Claude Code 项目上下文（关键！）
└── README.md
```

---

## 六、Phase 0：代码盘点（施工前必做）

**在任何迁移动作开始之前**，先让 Claude Code 输出一份结构化的代码盘点报告。这张表直接决定 Phase 1 的工作量和风险点，不能跳过。

### 盘点清单

**模块文件分布**
- 四个模块分别在哪些 `.py` 文件里？
- 有没有共享的工具函数文件？

**Streamlit 耦合度排查**
- 哪些文件大量使用 `st.session_state`？（状态管理耦合）
- 哪些文件使用了 `st.file_uploader`、`st.button`、`st.form`？（UI 组件耦合）
- 哪些文件使用了 `st.cache_data` / `st.cache_resource`？（缓存机制耦合）
- 是否有跨页面共享的 session 状态？

**可复用性评估**
- 哪些函数/类可以原样搬到 `services/` 层，不需要改动？
- 哪些逻辑必须重写，因为它依赖 Streamlit 的渲染模型？

**投资决策模块专项**
- 多轮对话的历史记录是存在 `st.session_state` 里还是有独立数据结构？
- Explain Panel 的数据是怎么传递的？是临时计算还是有对象模型？
- 这个模块的状态管理是否已经相对独立，还是深度依赖 Streamlit？

**高风险点识别**
- 预计哪个模块迁移最复杂、工作量最大？
- 有没有隐藏依赖（比如某个模块悄悄依赖另一个模块的 session 状态）？

**确认报告内容后，再授权 Phase 1 开始施工。**

---

## 七、前端信息架构草案（Phase 2 施工前确认）

### 导航结构

```
WealthPilot
├── 📊 账户总览          ← 默认首页，数据概览 + 持仓分布
├── 📋 投资纪律          ← 规则列表 + 合规状态
├── 🔬 投研观点          ← 观点列表 + 详情
└── 💡 投资决策          ← 核心功能，导航中优先展示
```

四个模块并列一级导航，账户总览作为默认落地页。

### 投资决策页布局

```
┌──────────────────────────────────────────────────────┐
│  顶部：模式切换（快速决策 / 深度分析）                │
├───────────────────────────┬──────────────────────────┤
│                           │                          │
│   左：多轮对话区（65%）    │   右：Explain Panel      │
│   - 消息列表              │        （35%，可折叠）   │
│   - 输入框                │   - 根据左侧点击内容     │
│   - 历史记录              │     动态展示解释信息     │
│                           │                          │
└───────────────────────────┴──────────────────────────┘
```

Explain Panel 采用**固定右侧栏**形态，不用抽屉（Drawer），因为投资决策场景需要对话和解释同时可见。

### Zustand Store 结构

```typescript
// frontend/src/store/decisionStore.ts
interface DecisionStore {
  messages: Message[]                   // 对话历史
  activeExplainTarget: string | null    // 当前触发 Explain 的内容
  explainContent: ExplainData | null    // Explain Panel 展示内容
  isPanelOpen: boolean                  // Panel 是否展开
}
```

其他三个模块使用组件内部 state 即可，不需要全局 store。

---

## 八、迁移施工顺序

```
Phase 0          Phase 1          Phase 2          Phase 3
──────────       ──────────       ──────────       ──────────
代码盘点    →   后端解耦    →   前端骨架    →   UI精修
确认风险        FastAPI建立      React接管        视觉深度优化
1-2小时         1-2天            2-3天            持续迭代
```

### Phase 1：后端解耦

**目标：** Streamlit 完全退出，FastAPI 接管，前端暂时不动

**严格禁止：**
- ❌ 不做前端页面
- ❌ 不重写数据库模型
- ❌ 不重写 AI 调用逻辑
- ❌ 不引入新技术依赖

**具体任务：**
1. 把各模块业务逻辑从 Streamlit 页面文件抽出，放入 `services/` 层
2. 建立 FastAPI，为四个模块各建对应的 API endpoints
3. 在 `api/tasks.py` 预建异步任务接口骨架（只定义结构，不实现逻辑）
4. 配置 CORS，允许 `localhost:5173` 访问
5. 用 curl 或 Postman 验证所有核心接口

**验收标准：** 不启动 Streamlit，命令行调用 API 能拿到正确数据

---

### Phase 2：前端骨架

**目标：** 用 React 把现有功能重新实现，先做到"完整可用"，不追求精致

**严格禁止：**
- ❌ 不抠像素级视觉细节
- ❌ 不做复杂动效
- ❌ 不做高级交互

**具体任务：**
1. 用 Vite 创建 React 19 项目，安装 Tailwind v4（`@tailwindcss/vite`）+ React Router v7 + Zustand + shadcn/ui + lucide-react
2. 搭基础布局：左侧固定导航 + 右侧内容区
3. 在 `vite.config.ts` 配置 proxy（`/api` → `http://localhost:8000`）
4. 附上参考图，告知 Claude Code 整体风格方向
5. 按顺序迁移四个模块（由易到难）：账户总览 → 投资纪律 → 投研观点 → 投资决策
6. 投资决策页：左侧对话区 + 右侧 Explain Panel，状态用 Zustand 管理
7. 前后端完整联调

**验收标准：** 四个模块功能与 Streamlit 版本对等

---

### Phase 3：UI 精修（持续迭代）

**目标：** 在正确架构上做视觉和交互深度优化

| 优先级 | 模块 | 重点 |
|---|---|---|
| P0 | 投资决策页 | 对话体验、Explain Panel 联动、信息层级 |
| P1 | 账户总览 | 持仓分布图、收益曲线、数据可视化 |
| P2 | 整体视觉规范 | 颜色体系、字体体系、间距体系统一 |
| P3 | 投研观点 | 列表筛选、详情页布局 |

---

## 九、给 Claude Code 的提示词模板

### Phase 0：代码盘点

```
请对 WealthPilot 项目做一次完整的代码盘点，输出分析报告。

【目的】
迁移到 FastAPI + React 架构之前的准备工作，
我需要先了解现有代码结构，再决定迁移顺序和风险处理方式。

【请回答以下问题，输出结构化报告】

1. 模块文件分布
   - 四个模块（账户总览/投资纪律/投研观点/投资决策）分别在哪些文件里？
   - 有哪些共享工具函数文件？

2. Streamlit 耦合度排查（逐模块）
   - 哪些文件大量使用 st.session_state？
   - 哪些文件使用了 st.file_uploader / st.button / st.form 等UI组件？
   - 哪些文件使用了 st.cache_data / st.cache_resource？
   - 是否有跨页面共享的 session 状态？

3. 可复用性评估（逐模块）
   - 哪些函数/类可以原样搬到 services/ 层？
   - 哪些逻辑必须重写？

4. 投资决策模块专项
   - 多轮对话历史存在哪里？st.session_state 还是独立数据结构？
   - Explain Panel 的数据是怎么传递的？
   - 这个模块的状态管理是否有独立对象模型？

5. 高风险点
   - 预计哪个模块迁移最复杂？
   - 有没有隐藏的模块间依赖？

【重要】只输出分析报告，不要改任何代码。
```

---

### Phase 1：后端解耦

```
我需要把 WealthPilot 项目从 Streamlit 迁移到 FastAPI + React 架构。

【第一阶段目标：仅做后端解耦】

具体任务：
1. 把业务逻辑从 Streamlit 页面文件抽出，建立 backend/services/ 层
2. 用 FastAPI 建立 API 层，覆盖四个模块：
   - 投资账户总览（portfolio）
   - 投资纪律（discipline）
   - 投研观点（research）
   - 投资决策（decision）
3. 在 backend/api/tasks.py 预建异步任务接口骨架
   （只定义接口结构，不实现逻辑，供未来自动化工具调用）
   至少包含：POST /tasks/create、GET /tasks/{task_id}/status
4. 在 main.py 配置 CORS（允许 localhost:5173 访问）
5. 保留现有数据库模型（SQLAlchemy / SQLite），不重写
6. 保留现有 LLM 调用逻辑，不重写

【严格禁止】
- 不做前端页面
- 不改动数据库结构和数据模型
- 不引入新的数据处理库
- 不一次性大范围重构，一个模块确认没问题再动下一个

【请先输出接口设计方案，包含：】
- 每个模块的主要 API endpoints（路径、方法、入参、返回结构）
- services 层的文件结构
- tasks.py 的接口设计草案

确认方案后再开始写代码。
```

---

### Phase 2：前端骨架

```
后端 FastAPI 已完成（Phase 1）。现在开始 Phase 2：搭建 React 前端骨架。

【技术栈（严格按照以下版本，不要自行升降级）】
- React 19 + Vite
- Tailwind CSS v4，使用 @tailwindcss/vite 插件（不要用 tailwind.config.js）
- React Router v7，使用 HashRouter
- shadcn/ui（仅用于复杂组件：Table、Dialog、Tabs、DropdownMenu 等）
- lucide-react（图标库，不手写 SVG，不引入其他图标库）
- Zustand（状态管理，仅用于投资决策页）
- TypeScript

【组件策略】
- 简单组件（导航栏、按钮、卡片、输入框）：Tailwind utility class 手写
- 复杂组件（数据表格、模态框、多级菜单）：shadcn/ui 按需引入
- 不混用其他组件库

【信息架构】
左侧固定导航，四个模块并列：账户总览 / 投资纪律 / 投研观点 / 投资决策
默认落地页：账户总览

【投资决策页布局】
左右两栏：左侧多轮对话区（65%）+ 右侧 Explain Panel（35%，可折叠）
Zustand store 管理：messages / activeExplainTarget / explainContent / isPanelOpen

【vite.config.ts 配置要求】
proxy: { '/api': 'http://localhost:8000' }

【迁移顺序（由易到难）】
账户总览 → 投资纪律 → 投研观点 → 投资决策

【目标：基础可用，不追求精致】
不做动效，不做高级交互，不抠像素，先跑通功能

【请先输出】
- 前端目录结构和依赖清单（package.json 关键依赖）
- 各页面组件清单
- decisionStore.ts 的 state 和 action 设计

确认后再开始写代码。
```

---

## 十、CLAUDE.md 模板

以下是推荐的 `CLAUDE.md` 内容，**每次启动新会话前让 Claude Code 先读这个文件**：

```markdown
# WealthPilot — Claude Code 项目上下文

## 项目愿景
AI 驱动的个人投资决策工作台。本地运行，数据私有，UI 专业。

## 当前架构目标
Local-First Web App：FastAPI 后端 + React 前端，本地浏览器访问。

## 技术栈（不得擅自修改版本或替换）

### 前端
- React 19 + Vite
- Tailwind CSS v4（@tailwindcss/vite 插件，无 tailwind.config.js）
- React Router v7（HashRouter）
- shadcn/ui（复杂组件按需引入）
- lucide-react（图标，不手写 SVG，不引入其他图标库）
- Zustand（仅投资决策页使用）
- TypeScript

### 后端
- FastAPI + SQLAlchemy + SQLite

## 当前进度
- [ ] Phase 0：代码盘点
- [ ] Phase 1：后端解耦
- [ ] Phase 2：前端骨架
- [ ] Phase 3：UI 精修

## 四个核心模块
| 模块 | 职责 | 优先级 |
|---|---|---|
| 账户总览（portfolio） | 持仓数据展示、收益分析 | P1 |
| 投资纪律（discipline） | 投资规则管理、合规检查 | P2 |
| 投研观点（research） | 研究观点录入与管理 | P3 |
| 投资决策（decision） | 多轮AI对话 + Explain Panel | P0，最核心 |

## 施工禁区（未经确认不得触碰）
- backend/models/ — 数据库模型，不改动
- backend/db/ — SQLite 文件，不改动
- backend/core/ — LLM调用和解析器，不改动

## 组件策略
- 简单组件用 Tailwind 手写
- 复杂组件（Table/Dialog/Tabs）用 shadcn/ui
- 图标用 lucide-react
- 不引入其他 UI 库

## 已知风险
- 投资决策模块的 session_state 耦合可能最重，Phase 1 重点处理
- 前后端联调：FastAPI 已配 CORS，Vite proxy 配置见 vite.config.ts

## 未来方向（不是当前目标，不要提前设计）
- Desktop 封装（Tauri，架构稳定后评估）
- OpenClaw 自动化接入（tasks.py 接口已预留骨架）
- SaaS 化（数据层改造即可）
```

---

## 十一、关键执行原则

1. **Phase 0 不能跳过。** 盘点报告是整个迁移的地基，没有它就没有对风险的判断依据。

2. **Phase 2 开始前准备参考图。** 截取 2-3 张风格参考截图（推荐 Linear / Vercel Dashboard / Perplexity），附在 Phase 2 提示词里。这一步对 UI 质量的影响超过任何技术栈选择。

3. **每个 Phase 先出方案再施工。** 提示词里都有"先输出设计方案，确认后再写代码"的流程，严格执行。

4. **Phase 间做完整验收。** Phase 1 完成 → 验收 API → 再开 Phase 2。Phase 2 完成 → 验收功能对等 → 再开 Phase 3。

5. **CLAUDE.md 是长会话的锚点。** 每次新会话开始，让 Claude Code 先读 CLAUDE.md，再告知当前 Phase。

6. **数据库绝对不动。** SQLite + 现有数据模型是最稳定的资产，迁移全程保持不变。

---

## 十二、风险与应对

| 风险 | 概率 | 应对方式 |
|---|---|---|
| 投资决策模块 session_state 耦合深，抽离工作量大 | 高 | Phase 0 重点排查，Phase 1 最后处理这个模块 |
| Explain Panel 联动逻辑在 React 里状态混乱 | 中 | Phase 2 提前用 Zustand 定好 store 结构，不临场设计 |
| Tailwind v4 配置与 v3 不同，Claude Code 写出 v3 语法 | 中 | 在 CLAUDE.md 和提示词里明确标注 v4 + `@tailwindcss/vite`，无 `tailwind.config.js` |
| Phase 2 无参考图，UI 输出质量不稳定 | 高 | 提前准备参考截图，附在 Phase 2 提示词里 |
| 前后端跨域问题 | 中 | FastAPI CORS + Vite proxy 双重保障，Phase 1 就配置好 |
| Claude Code 长会话跑偏，自由发挥重构 | 高 | CLAUDE.md 锚定方向，每次会话开始先读文档 |

---

## 十三、未来扩展路径（不是当前范围）

以下方向在 Phase 3 完成、架构稳定后再评估，**不要提前设计**：

- **自动化接入**：tasks.py 接口骨架已预留，届时按需实现具体逻辑
- **Desktop 封装**：优先评估 Tauri（前端代码完全复用，轻量）
- **SaaS 化**：数据层从 SQLite 迁移到云数据库，前后端架构不需要大改
- **数据库升级**：SQLite → PostgreSQL，按需升级

---

*文档由 Claude 生成 | WealthPilot 架构迁移方案 V1.2*
*V1.2 更新：前端技术栈对齐 CCBAI-Demo（React 19 / Tailwind v4 / React Router v7）、明确 shadcn/ui 使用策略、新增 UI 参考图策略、lucide-react 图标方案、CLAUDE.md 补充前端配置基线*
