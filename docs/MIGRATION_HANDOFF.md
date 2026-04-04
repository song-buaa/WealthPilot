# WealthPilot — React 前端迁移交接文档

> 更新日期：2026-04-03
> Worktree：`.claude/worktrees/crazy-hofstadter`（基于 main 分支开的隔离分支）
> 目标：将原 Streamlit 多页应用完整迁移为 React 19 + FastAPI 架构

---

## 一、技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 19, Vite, TypeScript, Tailwind v4, recharts |
| 路由 | HashRouter (`#/dashboard` 等) |
| 后端 | FastAPI + uvicorn，端口 8000 |
| 前端开发服务器 | Vite，端口 5173 |
| 数据库 | SQLite（app/models.py，SQLAlchemy） |
| AI | Anthropic Claude API（截图识别、AI报告、投资决策） |

**启动方式：**
```bash
# 后端（无 --reload，改动后需手动 kill 重启）
/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 \
  -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# 前端
cd frontend && npm run dev
```

---

## 二、已完成页面

### ✅ Step 1 — Dashboard（投资账户总览）`#/dashboard`

**文件：** `frontend/src/pages/Dashboard.tsx`

#### 已实现的所有模块：

1. **页面标题** — "投资账户总览" + 副标题 "Investment Portfolio Overview"

2. **KPI 卡片行（3列网格）**
   - 总资产卡片（深蓝渐变，含净资产/负债）
   - 浮动盈亏卡片（含收益率，正红负绿 A股色彩）
   - 杠杆倍数卡片（= 总资产/净资产，带安全/警戒/高风险状态，hover tooltip）

3. **大类资产配置卡（AllocationCard）**
   - 5类资产：权益/固收/货币/另类/衍生
   - 4列网格：标签 | 目标区间进度条 | 当前占比 | 偏离状态徽章
   - hover tooltip：标签显示"目标区间 X%~Y%"，区间条显示区间，彩色圆点显示当前金额
   - 偏离徽章：超配红色↑/低配蓝色↓/区间内绿色✓，含偏差百分点

4. **平台分布饼图（PlatformCard）**
   - recharts PieChart，从12点钟方向顺时针，按占比降序
   - hover 选中片段放大效果（`activeShape` + `Sector outerRadius+8`）
   - hover tooltip 显示平台名/市值/占比
   - 右侧图例含颜色/名称/百分比

5. **资产明细表（PositionsTable）**
   - 列：平台、资产名称、资产代码、资产大类、头寸、市值(美元)、市值(港元)、市值(人民币)、占比%、盈亏(人民币)、盈亏%
   - 平台名显示为蓝色标签（badge）
   - 盈亏颜色：正值红色 `#DC2626`，负值绿色 `#16A34A`（A股惯例）
   - 右上角显示持仓数量

6. **负债明细表（LiabilitiesTable）**
   - 列：名称、类别、用途、金额、年利率
   - 利率直接显示（API返回 3.0 = 3%，不需×100）

7. **导入/导出区块（持仓）** — 折叠面板
   - Tab1 通用CSV全量覆盖：`POST /api/portfolio/import/csv`
   - Tab2 券商CSV按平台替换（老虎/富途）：`POST /api/portfolio/import/broker-csv?broker=`
   - Tab3 截图识别按平台替换：`POST /api/portfolio/import/screenshot?platform=`
   - 折叠时顶部显示快捷导出按钮；展开时按钮在内容区（与"选择文件导入"同行）
   - 导出：`GET /api/portfolio/export/positions.csv`（UTF-8 BOM，Excel不乱码）

8. **导入/导出区块（负债）** — 折叠面板
   - 导入：`POST /api/portfolio/liabilities/import/csv`
   - 导出：`GET /api/portfolio/export/liabilities.csv`（UTF-8 BOM）

9. **投资预警（AlertsSection）**
   - 位置：AI综合分析报告之前
   - 三类告警：策略偏离 / 纪律触发 / 风险暴露
   - 杠杆告警使用倍数口径（= 总资产/净资产），与KPI卡和投资纪律阈值一致

10. **AI综合分析报告（AIReportSection）**
    - SSE 流式输出，复用 `streamDecisionChat`
    - 展示各分析阶段进度 + 最终结论

11. **全局浮动 Tooltip（DataTip）**
    - `createPortal` 挂载到 `document.body`
    - `data-tip` attribute 驱动，精确跟随鼠标

#### 已修复的问题：
- CSV导出 UTF-8 BOM（`_csv_response` in `backend/api/portfolio.py`）
- 杠杆告警计算口径统一（`app/analyzer.py`）
- 盈亏颜色 A股惯例（正红负绿）
- 浮动盈亏/资产明细中利率/盈亏数值格式
- 导出按钮贴边问题（`marginRight: 8`）
- 页面两侧留白（AppLayout padding `28px 64px`）

---

## 三、后端已完成

### `backend/api/` — FastAPI 路由

| 文件 | 路由前缀 | 状态 |
|---|---|---|
| `portfolio.py` | `/api/portfolio` | ✅ 完整 |
| `discipline.py` | `/api/discipline` | ✅ 完整 |
| `research.py` | `/api/research` | ✅ 完整 |
| `decision.py` | `/api/decision` | ✅ 完整（SSE） |

### `backend/services/` — 业务逻辑层
- `portfolio_service.py` ✅（含 broker CSV、截图导入、全量/按平台替换）
- `discipline_service.py` ✅
- `research_service.py` ✅
- `decision_service.py` ✅

### `frontend/src/lib/api.ts` — 前端 API 封装
- `portfolioApi`：getSummary / getPositions / getLiabilities / getAlerts / importCsv / importBrokerCsv / importScreenshot / importLiabilitiesCsv / deletePositions / export endpoints
- `disciplineApi`：getRules / updateRules / resetRules / getHandbook / evaluate
- `researchApi`：getViewpoints / createViewpoint / updateViewpoint / deleteViewpoint / getCards / parseText / approveCard
- `decisionApi`：getExplain / clearSession
- `streamDecisionChat`：SSE 异步生成器

---

## 四、待完成页面

### 🔲 Step 2 — 投资纪律 `#/discipline`

**原 Streamlit 文件：** `app_pages/discipline.py`
**后端服务：** `backend/services/discipline_service.py` + `backend/api/discipline.py`
**API 类型定义：** `api.ts` 中已有 `disciplineApi`

**需要实现的模块：**
1. 纪律规则展示（单一持仓上限、杠杆倍数上限等）
2. 规则编辑（PUT `/api/discipline/rules`）
3. 重置为默认（DELETE `/api/discipline/rules`）
4. 投资纪律手册（GET `/api/discipline/handbook`，markdown渲染）
5. 交易决策评估（POST `/api/discipline/evaluate`，输入交易意图文字，输出评估结果）

**关键配置文件：** `app/discipline/config.py`（单一持仓上限 40%/警戒30%，杠杆 1.20x/1.35x）

---

### 🔲 Step 3 — 投研观点 `#/research`

**原 Streamlit 文件：** `app_pages/research.py`
**后端服务：** `backend/services/research_service.py` + `backend/api/research.py`

**需要实现的模块：**
1. 观点列表（支持搜索过滤）
2. 新建/编辑观点（含 object_type / stance / thesis / 支持论点 / 反对论点 / 风险 / 操作建议 / 作废条件 等字段）
3. 删除观点
4. 研报解析卡（粘贴文本 → AI解析 → 生成草稿卡 → 用户审阅/修改 → 一键转为观点）
5. 有效性状态管理（有效/存疑/已作废）

---

### 🔲 Step 4 — 投资决策 `#/decision`

**原 Streamlit 文件：** `app_pages/decision.py`
**后端服务：** `backend/services/decision_service.py` + `backend/api/decision.py`（SSE）

**需要实现的模块：**
1. 左栏：对话输入框 + SSE流式输出（已有 `streamDecisionChat` 可复用）
2. 右栏：决策详情面板（intent / stages / conclusion），调用 `GET /api/decision/explain/{id}`
3. 阶段进度可视化（各审核维度：纪律/杠杆/集中度/心理/观点支撑）
4. 历史会话管理（session_id，`DELETE /api/decision/session/{id}`）

**注意：** 此页面需要 `height: 100%, overflow: hidden`，AppLayout 已对 `/decision` 路由特殊处理。

---

### 🔲 Step 5 — 其他页面（低优先级，可用占位符）

- `#/profile` 用户画像和投资目标
- `#/allocation` 新增资产配置
- `#/records` 投资记录
- `#/analysis` 收益分析
- `#/pension` 养老规划
- `#/housing` 购房规划
- `#/consumption` 消费规划
- `#/net-worth` 个人/家族资产负债总览

---

## 五、关键设计规范

### 色彩
- 主色蓝：`#3B82F6` / `#1D4ED8`
- 正盈亏（A股）：红色 `#DC2626`
- 负盈亏（A股）：绿色 `#16A34A`
- 背景：`#F8FAFC`，卡片：`#fff`，边框：`#E5E7EB`

### 组件模式
- 卡片：`background:#fff, border:1px solid #E5E7EB, borderRadius:12px, boxShadow:var(--shadow-sm)`
- 主按钮 `btnPrimary`：蓝色渐变，`padding: 8px 16px, fontSize: 12`
- 次按钮 `btnSecondary`：白底边框，`padding: 7px 14px, fontSize: 12`
- Tooltip：`data-tip` attribute + `DataTip` 全局组件（`createPortal`）

### API 约定
- 所有请求走 `/api` 前缀，Vite proxy 转发到 `localhost:8000`
- 错误格式：`{ detail: string }`
- 盈亏利率：API 返回值直接是百分比数值（如 `3.0` = 3%，`-4.76` = -4.76%）

### 布局
- AppLayout 主内容区：`padding: 28px 64px`
- 侧边栏：220px 固定宽

---

## 六、文件树（关键路径）

```
.claude/worktrees/crazy-hofstadter/
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx          ✅ 完成
│   │   │   ├── Discipline.tsx         🔲 待做
│   │   │   ├── Research.tsx           🔲 待做
│   │   │   └── Decision.tsx           🔲 待做
│   │   ├── components/layout/
│   │   │   ├── AppLayout.tsx          ✅
│   │   │   └── Sidebar.tsx            ✅
│   │   └── lib/
│   │       └── api.ts                 ✅ 完整封装
├── backend/
│   ├── main.py                        ✅
│   ├── api/
│   │   ├── portfolio.py               ✅
│   │   ├── discipline.py              ✅
│   │   ├── research.py                ✅
│   │   └── decision.py                ✅
│   └── services/
│       ├── portfolio_service.py       ✅
│       ├── discipline_service.py      ✅
│       ├── research_service.py        ✅
│       └── decision_service.py        ✅
└── app/
    ├── analyzer.py                    ✅（杠杆告警已修复）
    ├── models.py                      ✅
    ├── discipline/config.py           ✅（纪律阈值配置）
    └── ...
```
