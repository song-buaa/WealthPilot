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

- [x] Phase 0：代码盘点
- [ ] Phase 1：后端解耦
- [ ] Phase 2：前端骨架
- [ ] Phase 3：UI 精修

## 四个核心模块

| 模块               | 职责                     | 优先级    |
| ---------------- | ---------------------- | ------ |
| 账户总览（portfolio）  | 持仓数据展示、收益分析            | P1     |
| 投资纪律（discipline） | 投资规则管理、合规检查            | P2     |
| 投研观点（research）   | 研究观点录入与管理              | P3     |
| 投资决策（decision）   | 多轮AI对话 + Explain Panel | P0，最核心 |

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
