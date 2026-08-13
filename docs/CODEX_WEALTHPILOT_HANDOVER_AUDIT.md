# WealthPilot — Codex 接管审计与首次运行记录

> 审计日期：2026-07-26（Asia/Shanghai）
> 审计范围：仓库事实勘探、隔离本地启动、基础构建/测试/页面验证。
> 证据等级：**实际运行** > **自动化验证** > **静态代码确认** > **文档声明**。本文件不以历史 Claude Code 上下文为事实来源。

## 1. 执行摘要

WealthPilot 是本地优先的个人投资决策工作台：以统一持仓、投资纪律和投研上下文为输入，经 PEER 四 Agent + Skills 协议生成可解释的投资辅助分析，并提供人工确认的投资行动/执行计划入口。当前可确认的产品代码基线是 **v3.12**；工作区另有一批尚未提交的 **v3.14 K 线 Provider 解耦**在途修改，不能把它视为稳定发布基线。

本次在**临时 SQLite 数据库**、Demo 种子数据、关闭行情放行和 Mock 交易设置下，成功启动 FastAPI 与 Vite；浏览器实际加载密码门、投资账户总览和投资决策页面，并验证前后端代理/API 通信。没有写入 `data/wealthpilot.db`、没有调用券商，也没有发起 LLM 决策请求。

结论：项目可以作为本地 Demo/开发环境启动并进行账户总览、画像、纪律、投研与决策页面的人工验收；真实 LLM 决策、真实行情、券商同步/下单和当前在途的 v3.14 执行计划链路，不应宣称已经在本次通过端到端验证。

## 2. Git 与工作区基线

| 项目 | 事实 |
|---|---|
| 仓库 | `git@github.com:song-buaa/WealthPilot.git` |
| 当前分支 | `main` |
| HEAD | `11cd2e9` — `fix(arch): 架构图补第13个Skill方块 + 对账表补遗漏`（2026-06-16） |
| 远端关系 | `main` 与 `origin/main` 同指向 `11cd2e9`；远端默认分支仍是 `origin/master`（`bdcd244`） |
| 本地 `master` | `35ca490`，相对 `origin/master` ahead 1；未切换、不建议在未确认前合并 |
| Tags | 最新可见 `v3.11.1`、`v3.11.0`，其后 README/提交显示 v3.12，版本标记未同步 |
| 工作区 | 20 个已修改文件、约 30 个未跟踪文件；包含 v3.14 execution-plan、Demo、测试、数据库备份和带 ` 2` 后缀的重复文件 |

工作区**不是干净快照**。已修改/未跟踪内容均视为用户在途工作：本次没有执行 `checkout`、`reset`、`clean`、`stash`、提交或推送，也没有改写这些文件。`git diff --check` 未报告空白错误。

`.gitignore` 已覆盖 `.env`、`.env.local`、数据库、知识库索引、日志、构建产物及券商密钥；`data/wealthpilot.db.empty_20260615` 和 `data/wealthpilot.db.bak.pre_demo_e2e` 目前未跟踪，不能假定其可安全删除或覆盖。

## 3. 产品定位与功能地图

面向需要将多平台持仓、投资研究和个人纪律统一到同一决策流程的个人投资者。它是“LLM 推理 + 持仓上下文 + 纪律约束”的辅助决策系统，不应被理解为自动交易系统；交易始终需人工确认。

| 模块 | 主要入口/实现 | 完成度证据 |
|---|---|---|
| 投资账户总览 | `frontend/src/pages/Dashboard.tsx`；`/api/portfolio/*` | **实际运行验证**：种子数据总览、24 条持仓、负债、配置和平台分布已显示 |
| 用户画像与目标 | `UserProfile.tsx`；`/api/profile/*` | **静态/API 验证**：页面存在，GET 返回 200；未提交编辑 |
| 投资纪律 | `Discipline.tsx`；`/api/discipline/*`；`app/discipline/` | **静态/API 验证**：规则 API 返回 200；未逐项业务验证 |
| 投研观点 | `Research.tsx`；`/api/research/*`；SQLite V2 卡片 | **静态/API 验证**：V2 cards API 返回 200；历史 PRD 说明收敛仍有在途项 |
| AI 投资决策 | `Decision.tsx`；`/api/decision/chat` SSE；PEER Agents | **页面实际验证**、核心链路静态确认；本次未发起会产生外部 LLM 调用的完整对话 |
| 投资行动/订单 | `Action.tsx`；`/api/action/*`；`backend/services/action/` | **实现和单测确认**；本次未生成草稿/下单，Demo 模式也应拦截交易路径 |
| 执行计划 | `/api/execution-plan/*`；`backend/services/execution_plan/` | **部分实现**：v3.11 已入库；v3.14 Provider 解耦处于未提交工作区，未作为稳定功能验收 |
| 券商同步/真实下单 | Tiger/Futu/Snowball/国金/IBKR adapters | **接口/适配层实现**；需要凭证、网关和人工风险确认，本次未调用 |
| 私有知识库 | `knowledge_base/`、`backend/knowledge/`、Chroma | **实现和部分单测确认**；嵌入型检索测试因环境条件跳过 |

产品阶段判断：**可运行的本地开发/Demo 产品，包含已实现的高风险真实券商接入能力；不是经过本次完整生产验收的成品。**

## 4. 技术架构与目录地图

| 层级 | 技术与职责 |
|---|---|
| 前端 | React 19、TypeScript、Vite 8、Tailwind v4、React Router v7、Zustand、Radix；`frontend/src` |
| 后端 | Python 3.11、FastAPI、Uvicorn、SQLAlchemy 2、Pydantic 2、SQLite；入口 `backend/main.py` |
| 决策编排 | `backend/agents/` 的 Planning → Executing → Expressing → Reviewing；`backend/services/decision_service_v3.py` 串联 SSE |
| Skills/工具 | `skills/*/SKILL.md` 定义 13 个能力；`backend/skills/loader.py` 和 `backend/graph/tools.py` 调用 |
| 领域与数据 | `app/models.py`、`app/database.py`；`decision_engine/` 为稳定底层决策引擎；`backend/services/action/` 为行动订单领域 |
| 知识层 | Markdown 真相源 `knowledge_base/`；`backend/knowledge/` 管理 Chroma 索引/检索 |
| 外部服务 | OpenAI、Alpha Vantage、AKShare、盈米 MCP、Tiger/Futu/IBKR/国金/Snowball；均应按 optional + graceful degradation 处理 |

`backend/main.py` 在 lifespan 中初始化表和默认 portfolio。Demo 模式会跳过券商、调度器和订单轮询，随后加载种子持仓、画像、负债和资料；非 Demo 模式可启动 Mock adapter、订单轮询及定时同步/触发评估。

## 5. 核心业务链路

### A. 账户总览（本次实际验证）

`Dashboard.tsx` → `frontend/src/lib/api.ts` → `/api/portfolio/*` → `backend/api/portfolio.py`/服务层 → SQLAlchemy SQLite → 页面 KPI、资产配置、持仓与负债表。

隔离 Demo 的 `GET /api/portfolio/summary` 返回了资产、负债、平台分布和 24 个种子持仓；浏览器页面与接口数据一致。

### B. AI 投资决策（静态确认 + 页面验证）

`Decision.tsx` → `POST /api/decision/chat`（SSE）→ `decision_service.run_chat_stream` → Planning（意图/Skill 选择）→ Executing（持仓、投研、纪律、信号/降级数据）→ Expressing（唯一流式 LLM 输出）→ Reviewing（硬校验、必要时 LLM 评分）→ SSE 文本/解释数据。

此链路依赖 OpenAI Key；行情/投研依赖可能降级。浏览器已验证决策页面、会话入口和预设问题可渲染，**未验证真实模型输出质量或所有五类意图**。历史 `docs/m5_e2e_report.md` 的通过记录仅是历史文档声明，不能代替本次实跑。

### C. 执行计划与人工下单（静态确认）

决策建议 → `wp-generate-execution-plan` → 因子快照/确定性规则引擎 → ExecutionPlan/Tranche 草案 → 用户确认 → Action 的 SymbolStrategy/OrderRecord → 风控 + BrokerAdapter。规则数字应由执行计划规则引擎产出，LLM 仅给解释。当前 v3.14 工作区计划将 K 线从 broker 强依赖改为 `Broker → AV → Seed` Provider fallback；尚未形成提交基线，本次没有下单或调用真实券商。

## 6. 配置、依赖与数据

运行时：系统 Python 为 3.7.1，不适用；项目可用 Conda Python 为 **3.11.13**。Node 为 **22.23.1**，npm 为 **10.9.8**。Python 依赖由根 `requirements.txt` 描述（未锁版本）；前端由 `frontend/package-lock.json` 锁定。仓库不存在 Dockerfile、Docker Compose、Makefile 或统一启动脚本。

| 变量/组 | 用途 | 必填性及缺失行为 |
|---|---|---|
| `WEALTHPILOT_OPENAI_API_KEY`、`OPENAI_BASE_URL` | 决策、评分、OCR、嵌入 | Key 对真实 LLM 链路必填；缺失时相应 LLM 能力不可用/降级 |
| `ALPHA_VANTAGE_API_KEY`、`AV_API_KEY_1..4`、`AV_DEV_MOCK` | 美股基本面、新闻、开发 mock | 可选；外部数据缺失应降级 |
| `YINGMI_API_KEY`、`YINGMI_MCP_URL` | 基金 MCP | 可选；基金外部数据降级 |
| `PUBLIC_DEMO_MODE`、`DEMO_ACCESS_PASSWORD`、`DEMO_ALLOW_MARKET_DATA` | 单端口 Demo、密码门、是否允许免费行情 | 默认 Demo 为 true；密码为空时应用**拒绝启动**；行情开关默认 true |
| `WEALTHPILOT_DB_PATH` | SQLite 文件位置 | 可选；默认 `data/wealthpilot.db`；审计时应设临时路径避免写用户数据 |
| `BROKER_MODE` | `mock` / 真实券商 adapter 路由 | 缺失默认 `mock` |
| `TIGER_*`、`FUTU_*`、`SNOWBALL_*`、`GUOJIN_*`、`IBKR_*` | 券商同步/交易和本地网关 | 真实路径所需；缺失时相应 adapter 不能工作，不应阻断 Demo |
| `ENABLE_IBKR_LIVE_TRADING`、`ENABLE_TIGER_LIVE_TRADING`、`*_READ_ONLY_MODE` | 真实交易安全开关 | 默认应保持关闭/只读；不应为验收打开 |
| `WP_USE_SKILL_RETRIEVE_PRINCIPLES`、`WP_USE_SKILL_OUTPUT_VALIDATOR` | Skill 新/旧调用路径 | 当前代码默认 `1`（走新 Skill），显式 `0` 才走旧路径 |

配置差异：`.env.example` 未列 Demo 三变量、`WEALTHPILOT_DB_PATH`、`BROKER_MODE` 与两个 `WP_USE_SKILL_*` flag；然而 `PUBLIC_DEMO_MODE` 默认开启且无密码会 fail-closed。这会使“只复制 `.env.example`”的首次启动失败，应在用户确认工作区在途 `.env.example` 改动后补齐。

## 7. 本地启动与人工验收

### 推荐的安全 Demo 启动方式

以下示例使用隔离数据库，不会修改真实本地组合。先在一个终端执行：

```bash
audit_db_dir=$(mktemp -d /tmp/wealthpilot-demo.XXXXXX)
PUBLIC_DEMO_MODE=true \
DEMO_ACCESS_PASSWORD='<自行设置的本地密码>' \
DEMO_ALLOW_MARKET_DATA=false \
BROKER_MODE=mock \
WEALTHPILOT_DB_PATH="$audit_db_dir/wealthpilot.db" \
/Users/songbin/opt/anaconda3/envs/wealthpilot/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

另一终端：

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

访问 `http://127.0.0.1:5173/`，输入上面的本地 Demo 密码。后端健康检查为 `http://127.0.0.1:8000/api/health`（Demo 密码门启用时需已有认证 cookie）。不使用 Demo 时，至少显式设置 `PUBLIC_DEMO_MODE=false BROKER_MODE=mock`，并在启动前确认目标数据库不是用户真实数据。

建议人工验收顺序：

1. 登录后检查“投资账户总览”的 KPI、资产配置与 24 条种子持仓。
2. 打开“用户画像”和“投资纪律”，确认资料和规则 API 无错误展示。
3. 打开“投研观点”，确认空/种子状态可用；不要把短期信号当作已人工审批的长期观点。
4. 打开“投资决策”，确认新对话、预设问题和输入框正常；只有在已配置许可的 OpenAI Key 后才发送实际问题。
5. 对执行计划/投资行动仅检查展示和草稿，不连接真实券商、不提交订单。

## 8. 自动化与实际验证结果

| 验证 | 命令/方式 | 结果 |
|---|---|---|
| Python 语法 | `python -m compileall -q app backend decision_engine intent_engine research_v2 tests` | **通过** |
| 前端构建 | `cd frontend && npm run build` | **通过**；产物主 JS 约 1,057 KB，Vite 给出 >500 KB chunk 警告 |
| 前端 lint | `cd frontend && npm run lint` | **失败**：27 errors、4 warnings，详见风险 P1 |
| 默认 pytest | `python -m pytest` | **405 passed / 26 failed / 7 skipped**，共 438 collected，124.21s |
| 后端启动 | 临时 DB + Demo/Mock 设置，Uvicorn `127.0.0.1:8000` | **实际运行通过** |
| API smoke | 密码验证、health、portfolio summary、profile、discipline rules、research V2 cards、conversations | **均返回 200/预期数据** |
| 前端启动 | `npm run dev -- --host 127.0.0.1 --port 5173` | 初始失败（Rolldown 原生 binding 缺失）；`npm ci` 后**实际运行通过** |
| 浏览器验收 | 密码门 → Dashboard → Decision | **实际运行通过**，未见应用页面阻断错误 |
| M5 18-case | `scripts/m5_e2e_18_cases.py` | **本次未运行**：脚本硬编码无认证的 8000 端口、会改写已跟踪 `docs/m5_e2e_report.md`，并会发起真实 LLM 请求；不应在隔离 Demo/脏工作区无确认下执行 |

pytest 失败分类：分析器 fixture 与现行 portfolio 数据模型（8）；已切换为默认启用的 Skill 路径而旧测试仍断言 flag 默认关闭（4）；`intent_engine.run` 旧 `session_id` 签名（2）；投研来源前缀/renderer 预期（7）；汇率固定值与当前汇率（4）；general route 状态预期（1）。这些均为已存在的兼容性/测试基线问题，未在本轮修改。

## 9. 本轮修改

| 文件/对象 | 修改 | 原因 | 业务逻辑影响 |
|---|---|---|---|
| `frontend/node_modules/`（忽略的生成目录） | `npm ci` 按现有 `package-lock.json` 重建依赖 | 修复缺失 `@rolldown/binding-darwin-arm64` 导致的 Vite dev server 启动阻塞 | 无；未改 lockfile/源码 |
| `frontend/dist/`（忽略的生成目录） | `npm run build` 生成 | 构建验证 | 无 |
| 本文档 | 新增 | 提供后续 Codex 可复用的接管事实、风险与启动入口 | 无 |

没有修改产品逻辑、数据库 schema、真实数据、密钥、Git 历史或任何在途业务代码。

## 10. 风险清单与文档/代码差异

| 等级 | 风险 | 证据与影响 | 建议 |
|---|---|---|---|
| P1 | 前端 lint 失绿 | 27 errors，含 `Decision.tsx` 条件调用 Hook、同步 effect setState、`any`/unused 等 | 单立修复任务；先处理 conditional hooks 和运行时关联项，禁止批量格式化 |
| P1 | Demo 首次启动配置不自洽 | 默认 Demo=true 且密码缺失会 fail-closed；`.env.example` 未列必要 Demo 配置 | 在确认当前 `.env.example` 在途修改后补全安全示例和 README 启动说明 |
| P1 | 全量测试未绿 | 26 失败覆盖旧分析器、旧意图 API、renderer、汇率和双轨 flag 测试 | 先界定每组是应更新测试还是恢复兼容；逐组修复并保存新的基线 |
| P1 | 工作区混入大量未跟踪/重复文件 | v3.14 关键实现未提交，多个 ` 2.py`/` 2.md` 副本和 DB 备份混在一起 | 先由所有者确认 v3.14 目标文件集，再做安全的分组提交/归档；当前不要清理 |
| P2 | 版本信息分裂 | AGENTS.md 仍以 v3.6 为“当前”，README/提交为 v3.12，未跟踪 PRD 为 v3.14，最新 tag v3.11.1 | 确定发布线后同步 README、AGENTS、CHANGELOG、tag 语义 |
| P2 | 投研存储设计有未收敛历史 | v3.12 PRD 记录 V1/V2 双轨、三写与 research Chroma 路径已放弃 | 按 v3.12 PRD 决策明确数据迁移/归档前先备份并验证消费者 |
| P2 | 历史测试与当前默认策略脱节 | C1/C2 旧测试假定 flags 默认关闭，代码默认走 Skill | 测试应反映当前默认值；不要重引 feature flag 双轨 |
| P2 | 真实外部依赖的可验证性 | 券商/行情/LLM 都依赖密钥、网关或网络；本次按安全边界未调用 | 为每个 provider 补低成本、明确的 mock/contract smoke，保持失败降级 |
| P3 | 构建 bundle 较大 | Vite 对单 JS chunk 给出 >500 KB warning | 将来按路由/重组件 code split，非启动阻塞 |
| P3 | Pydantic 迁移警告 | pytest 报 class-based `Config` deprecated | Pydantic v3 升级前按模块迁移 `ConfigDict` |
| P3 | 历史文档噪声 | `docs/archive/PROJECT_HANDOFF.md` 是 v1.9.1 Streamlit 历史，不反映当前 React/FastAPI 运行方式 | 保留归档标记，后续只引用本文件/当前版本文档 |

## 11. 后续开发建议

1. **必须先处理**：确认并收敛当前 v3.14 在途工作（尤其 `kline_provider.py`、execution-plan models/tests 和前端面板）；建立一个可复现、干净的提交基线，再继续开发。
2. **必须先处理**：修复或重新界定 26 个 pytest 失败，优先分析器、决策入口签名和默认 Skill flag；每组完成后运行相关测试，代码改动再运行 M5。
3. **建议随后处理**：补齐 `.env.example`/README 的 Demo 配置与隔离数据库启动说明，确保首次启动不因 fail-closed 密码门而迷失。
4. **建议随后处理**：单独修复前端 lint 的实际 Hook 规则问题，随后恢复 lint 绿灯；不要把 lint 规则降级来掩盖问题。
5. **可以暂缓**：bundle 分包、Pydantic v3 前瞻迁移、archive 文档的整理和非生产 `intent_engine/` TODO 清理。

## 12. 给后续 Codex 会话的上下文摘要

- 从 `main@11cd2e9` 开始理解；它与 `origin/main` 一致，但默认远端分支还是 `master`，不要擅自切换。
- **先检查 `git status`**。当前包含用户未提交 v3.14 Execution Plan K 线 Provider fallback 开发和重复文件，绝不可 reset/clean/覆盖。
- 当前实际 Web 架构是 React/Vite + FastAPI，不是 archive 内的 Streamlit；入口 `backend/main.py`，前端页面 `frontend/src/pages/`。
- 决策主线是 `decision_service_v3.py` 的 PEER Agents；`decision_engine/` 是稳定底层，不要直接修改。Skills 的 frontmatter 字段是 loader 契约。
- Demo 默认启用且必须设置 `DEMO_ACCESS_PASSWORD`；验收时用临时 `WEALTHPILOT_DB_PATH`、`BROKER_MODE=mock`、`DEMO_ALLOW_MARKET_DATA=false`，不触碰真实数据库/券商。
- 本次已证明：Python compile、前端 build、Vite+FastAPI+Demo Dashboard/Decision 页面、关键只读 API 可运行。未证明：真实 LLM、真实行情/券商、下单与 v3.14 端到端计划链路。
- 当前质量基线：pytest `405 passed, 26 failed, 7 skipped`；lint `27 errors, 4 warnings`。修复时先判定历史预期与现行行为，不为全绿修改投资业务规则。
- 本文档是最新接管入口；老 `docs/archive/PROJECT_HANDOFF.md`（v1.9.1/Streamlit）仅为历史材料。

## v3.14 在途工作审计与基线收敛计划

> 审计日期：2026-07-26。此节基于当前脏工作区，**只做盘点与验证**；没有删除、移动、覆盖、暂存、提交或推送任何用户文件。

### 正式范围与完成度

唯一明确的 v3.14 需求来源是未跟踪的 `docs/wp_v3.14_kline_provider_prd.md`。它要把 Execution Plan 的日 K 线取得从 broker 硬依赖拆为 `KlineProvider` 接口与 `Broker → AV → Seed` 有序 registry，使公开 Demo 不注册 broker，并且保持前端 `factor_snapshot` 契约零感知。范围仅为执行计划的日 K 线/因子来源；决策报告、交易规则、风控、Action/OrderRecord、前端布局以及 `trigger_evaluator`（列为 v3.15）均不在本版内。

| 需求 | 对应实现 | 对应测试/证据 | 当前状态 | 缺口 |
|---|---|---|---|---|
| Provider 抽象与有序 fallback | `kline_provider.py`：`KlineProvider`、`KlineResult`、`KlineProviderRegistry` | `test_factors.py` 注入 mock registry | 部分实现 | 无独立 registry/provider 单测；异常、空值和超时仅汇总为 provider 名，无法区分原因 |
| dev `[Broker, AV, Seed]`、Demo `[AV, Seed]` | `build_kline_registry()` | 隔离 smoke 确认 Demo 注册 `[av, seed]` | 部分实现 | 默认值与 `backend/core/demo_mode.py` 不一致（前者 false、后者 true）；未验证 dev broker 优先 |
| Broker provider 保持原行为 | `BrokerKlineProvider` | 仅静态检查 | 实现但未验证 | 使用 `from utils.symbol`，当前根环境无 `utils` 包；broker 会直接降级，不能满足 dev broker 优先验收 |
| AV provider 日线 fallback | `AVKlineProvider` | 无离线单测 | 部分实现 | 仅支持 US，与 PRD“HK 部分可用、需实测”不一致；绕过 `DEMO_ALLOW_MARKET_DATA=false` 直接 `urlopen`，隔离 Demo 仍可能联网 |
| Seed demo fallback | `SeedKlineProvider` | 隔离 smoke：`600519:CN` 260 bars、`kline_source=seed`、52w 可算 | 部分实现 | 以种子现价 + `datetime.now()` 合成 bars，不是 PRD 所称一次性生成并提交的静态 OHLC；每天日期变化，复现性/审计性不足 |
| 单一 FactorComputer 与 52w 计算 | `factors.py` 的 `compute_factors_from_bars` | 当前 `test_factors.py` 14/14 通过 | 已实现但未验证真实源 | 旧 `_fetch_raw_kline`/`_fetch_52w` 仍留在模块中，形成死实现与旧测试副本诱因 |
| metadata 兼容与降级可见 | `FactorSnapshot` / `DataSourceMeta` | 注入 failing broker + delayed AV 的 smoke | 与需求冲突 | fallback 后 `broker` 仅在 `degraded_reason`，不在 PRD 要求的 `degraded_fields`；`KlineResult.delayed_minutes=15` 未写回快照 |
| 三源全空诚实降级 | `build_factor_snapshot` | `test_factors.py::test_degraded_no_kline` | 已实现 | 未覆盖 API/UI 的“人工锚点价入口仍可用” |
| 前端零感知 | `FactorSnapshot` 字段未删；无正式前端变更 | `npm run build` 通过 | 部分实现 | 元数据透传未完整；无 JSON schema/前端 contract test |
| Demo AAPL 走 AV、A 股走 Seed | Provider + factors | A 股 Seed smoke 通过 | 部分实现 | AAPL→AV 需真实外部请求，本轮按隔离边界未调用；AV Demo gate 缺失；无正式验收测试 |
| 新旧数据库可启动 | 既有 `ExecutionPlan` models + `app.database.init_db` | 临时 SQLite 初始化检查 | 与需求冲突/既有阻塞 | `init_db()` 只导入 Action models，未导入 execution-plan models；新库无 `execution_plans`/`execution_tranches` 表，持久化草稿会失败 |

结论：v3.14 可以识别为一套有明确 PRD、核心实现和定向测试的在途开发，但尚不是可提交的完整实现。最小阻塞是 Provider 元数据/环境安全契约未满足，以及全新数据库的执行计划表初始化缺失；二者都应在提交前处理或明确拆出前置兼容提交。

### 工作区完整分类（A–G）

盘点时工作区为 20 个已修改、29 个未跟踪文件。下表逐项列出，不将文件名本身当作唯一判断依据。

| 路径 | Git 状态 | 分类 | 关联/主要内容 | 建议进入 v3.14 提交 | 风险与推荐处理 |
|---|---|---|---|---|---|
| `.env.example` | M | F | 全局 `OPENAI_API_KEY` 重命名 | 否 | 独立配置迁移，另案提交 |
| `README.md` | M | F | 同上，启动说明变量名 | 否 | 与 Demo 配置缺口一起另案审阅 |
| `app/ai_advisor.py` | M | F | 全局 LLM key 重命名 | 否 | 独立兼容性改动 |
| `app/bank_screenshot.py` | M | F | 全局 LLM key 重命名 | 否 | 独立兼容性改动 |
| `backend/agents/planning_agent.py` | M | F | 全局 LLM key 重命名 | 否 | 独立兼容性改动 |
| `backend/agents/reviewing_agent.py` | M | F | 全局 LLM key 重命名 | 否 | 独立兼容性改动 |
| `backend/graph/tools.py` | M | F | 执行计划 rationale 与其他工具的 LLM key 重命名 | 否 | 不是 Provider 解耦本体，单独验证 |
| `backend/knowledge/store.py` | M | F | Embedding key 重命名 | 否 | 独立知识层改动 |
| `backend/services/action/action_planner.py` | M | F | Action LLM key 重命名 | 否 | 不混入 v3.14 |
| `backend/services/execution_plan/adjustment_parser.py` | M | F | 执行计划调整 LLM key 重命名 | 否 | 虽位于本模块，但无 PRD 证据表明属于 v3.14 |
| `backend/services/execution_plan/factors.py` | M | A | Provider 接入、统一因子、bars 计算 52w | 是，但须修复阻塞 | v3.14 核心候选 |
| `backend/services/execution_plan/tests/test_factors.py` | M | B | 注入 registry 的因子回归测试 | 是，但须补 provider/metadata 覆盖 | 当前 14/14 通过 |
| `backend/services/profile_service.py` | M | F | 全局 LLM key 重命名 | 否 | 独立兼容性改动 |
| `decision_engine/llm_engine.py` | M | F | 全局 LLM key 重命名 | 否 | 不可触碰底层引擎来收敛 v3.14 |
| `intent_engine/_llm_client.py` | M | F | 全局 LLM key 重命名 | 否 | 独立历史模块改动 |
| `research_v2/processor.py` | M | F | 全局 LLM key 重命名 | 否 | 独立投研模块改动 |
| `scripts/m3_v1_baseline.py` | M | F | 脚本变量名更新 | 否 | 独立脚本改动 |
| `scripts/test_knowledge_retrieve.py` | M | F | 脚本变量名更新 | 否 | 独立脚本改动 |
| `tests/knowledge/test_store.py` | M | F | 测试环境变量更新 | 否 | 随全局 key 迁移提交 |
| `tests/skills/test_wp_retrieve_principles.py` | M | F | 测试环境变量更新 | 否 | 随全局 key 迁移提交 |
| `backend/services/execution_plan/kline_provider.py` | ?? | A | 新 Provider/registry/Broker/AV/Seed | 是，但须修复阻塞 | v3.14 核心新增文件 |
| `docs/wp_v3.14_kline_provider_prd.md` | ?? | B | v3.14 唯一正式 PRD | 是 | 先修正文档与代码冲突后纳入 |
| `docs/CODEX_WEALTHPILOT_HANDOVER_AUDIT.md` | ?? | B | 本接管与 v3.14 审计 | 是，最后文档提交 | 不应与核心实现混淆 |
| `backend/api/demo 2.py` | ?? | E | 与 `backend/api/demo.py` 逐字相同 | 否 | 重复副本；保留待后续安全删除 |
| `backend/core/demo_mode 2.py` | ?? | E | 与正式文件逐字相同 | 否 | 重复副本 |
| `backend/scripts/probe_factors 2.py` | ?? | E | 与正式文件逐字相同 | 否 | 重复副本 |
| `backend/scripts/probe_rule_engine 2.py` | ?? | E | 与正式文件逐字相同 | 否 | 重复副本 |
| `backend/scripts/probe_skill_orchestrator 2.py` | ?? | E | 与正式文件逐字相同 | 否 | 重复副本 |
| `backend/services/demo_seed_loader 2.py` | ?? | E | 与正式文件逐字相同 | 否 | 重复副本 |
| `backend/services/execution_plan/__init__ 2.py` | ?? | E | 与正式文件逐字相同 | 否 | 重复副本 |
| `backend/services/execution_plan/models 2.py` | ?? | E | 与正式文件逐字相同 | 否 | 重复副本 |
| `backend/services/execution_plan/tests/__init__ 2.py` | ?? | E | 与正式文件逐字相同（空） | 否 | 重复副本 |
| `backend/services/execution_plan/tests/test_factors 2.py` | ?? | E | 旧版因子测试，仍 patch 已不再主用的 helpers | 否 | 被 pytest 收集，造成 3 个伪回归；后续确认后删除/隔离 |
| `backend/services/execution_plan/tests/test_trigger_evaluator 2.py` | ?? | E | 与正式文件逐字相同 | 否 | 重复且会重复收集 |
| `demo_seed/demo_seed_viewpoints 2.md` | ?? | E | 与正式文件逐字相同 | 否 | 重复副本 |
| `docs/v3.10/PRD_ibkr_v3.10.md 2.md` | ?? | E | 与正式文档逐字相同 | 否 | 重复文档 |
| `docs/v3.10/m2_5_probe_open_result 2.md` | ?? | E | 与正式文档逐字相同 | 否 | 重复文档 |
| `docs/v3.8/v3.8.5plus_C_range_master_plan 2.md` | ?? | E | 与正式文档逐字相同 | 否 | 重复文档 |
| `docs/v3.8/v3.8.6_C1_validation_report 2.md` | ?? | E | 与正式文档逐字相同 | 否 | 重复文档 |
| `frontend/src/components/DemoPasswordGate 2.tsx` | ?? | E | 与正式组件逐字相同 | 否 | 重复副本 |
| `frontend/src/components/ExecutionPlanPanel 2.tsx` | ?? | E | 与正式组件逐字相同 | 否 | 重复副本；前端并无 v3.14 正式改动 |
| `knowledge_base/research_views/demo_seed_viewpoints 2.md` | ?? | E | 与正式文件逐字相同 | 否 | 重复副本 |
| `scripts/probe_ibkr_live_connect 2.py` | ?? | E | 与正式脚本逐字相同 | 否 | 重复副本，且不可在审计中运行 |
| `tests/test_legacy_selected_skills_compat 2.py` | ?? | E | 与正式测试逐字相同 | 否 | 重复副本 |
| `tests/test_skill_manifest 2.py` | ?? | E | 与正式测试逐字相同 | 否 | 重复副本 |
| `tests/test_skill_reconcile 2.py` | ?? | E | 与正式测试逐字相同 | 否 | 重复副本 |
| `data/wealthpilot.db.bak.pre_demo_e2e` | ?? | E | 10.6 MB SQLite 备份（2026-06-10） | 否 | 可能含真实个人数据；未读取内容；应移至受控本地备份且保持忽略 |
| `data/wealthpilot.db.empty_20260615` | ?? | E | 176 KB SQLite 快照（2026-06-15） | 否 | 可能是空库基线；代码未引用；后续确认后安全归档/忽略 |
| `docs/public/demo_seed_positions.csv` | ?? | G | 旧 schema、22 行持仓样例，与现行 `demo_seed/demo_seed_positions.csv` 不同且未被引用 | 否 | 用途不明，可能含演示数据；保留待来源确认 |

分类汇总：A=2、B=3、C=0、D=0（构建/缓存已被忽略，未出现在 status）、E=25、F=18、G=1。A/B 的 5 个候选文件不是当前可直接提交集：必须先处理以下阻塞并重新验证。

### 技术审计结论

- **确定性与交易边界**：`execute_generate_execution_plan` 仍按 factors → rule engine → LLM rationale → validator 调用；本次 diff 未改变规则引擎、Action、OrderRecord 或风控。Provider 改动会改变因子数据输入和潜在计划数值，故不能仅以“前端零改动”认定安全。
- **前后端契约**：`FactorSnapshot` 的主字段保留，前端当前读取既有字段；但 `delayed_minutes` 丢失、fallback provider 未进入 `degraded_fields`，不符合 PRD 的可追溯契约。注入 failing broker + AV result 的实际输出为 `degraded_reason='K线降级: broker 不可用'`、`degraded_fields=[]`、`delayed_minutes=None`。
- **环境安全**：`AVKlineProvider` 直接访问 AV，而没有采用已有 `DEMO_ALLOW_MARKET_DATA=false` 短路；这与隔离 Demo 原则冲突。当前 PRD 所要求的 AAPL→AV 验收不能在本次安全配置下运行。
- **Broker 可用性**：当前根 Python 环境找不到 `utils` 包；Provider 与保留的旧 helper 均引用 `utils.symbol`，因此 broker path 会在网络调用前失败并被 registry 吞为降级。应以现行 `backend.utils.symbol` 或项目确认的运行时 import root 为准，不能假定“原样搬运”即等价。
- **数据/迁移**：v3.14 未新增 ORM 字段或表。反而发现既有启动路径 `app.database.init_db()` 未导入 execution-plan models；临时新库只创建了 Action 与业务表，缺少 `execution_plans`、`execution_tranches`。这不是本次 provider diff 引入，但会阻断 v3.14 的 `persist-draft` 在干净 DB 中可用。
- **重复文件影响**：`test_factors 2.py` 是旧实现测试，不是正式 v3.14 测试；因 pytest 文件模式会自动收集，导致整个 execution-plan 目录运行显示 3 failed / 118 passed。排除副本、明确列出现行 6 个测试文件后，92 passed。重复文件是干净基线的直接阻塞，但本轮不删除。

### 定向验证结果

| 命令/方式 | 结果 | 说明 |
|---|---|---|
| `pytest backend/services/execution_plan/tests -q` | 118 passed、3 failed | 失败均来自未跟踪 `test_factors 2.py` 旧测试副本 |
| 明确列出现行 6 个 execution-plan 测试文件 | 92 passed、4 个 SQLAlchemy legacy warnings | v3.14 修改的 `test_factors.py` 14/14 通过 |
| 隔离 Demo Seed smoke（临时环境、无外部调用） | 通过 | registry=`[av, seed]`；`600519:CN` 得 260 bars、`kline_source=seed`、52w 可算 |
| failing broker + delayed AV 注入 smoke | 暴露缺口 | `broker` 不在 `degraded_fields`，`delayed_minutes` 未透传 |
| 临时 SQLite `startup()` 表检查 | 失败/阻塞 | 未创建 `execution_plans` 与 `execution_tranches` |
| `python -m compileall`（execution-plan/API/tools） | 通过 | 仅语法验证 |
| `frontend/npm run build` | 通过 | 仍有 >500 KB bundle warning；未证明运行时 contract |

未运行真实 LLM、真实 AV、Tiger/Futu/IBKR 或真实交易。也没有为通过测试调整任何业务逻辑。

### 推荐的安全提交序列（仅方案，不执行）

1. **前置兼容修复** — `fix(execution-plan): register plan models during database init`：只包含数据库初始化注册及“新库含 execution plan 表”的定向测试。它依赖于确认 v3.11 模型确实应在首次启动创建；回滚风险低，但必须先完成，否则 demo 草稿持久化无基线。
2. **v3.14 核心** — `feat(execution-plan): add kline provider fallback registry`：包含 `kline_provider.py`、`factors.py`。提交前须修正 symbol import、Demo AV gate、`delayed_minutes`/`degraded_fields` 契约，并决定 Seed 是提交的静态 OHLC fixture 还是明确标示的合成演示数据；不能混入全局 key rename。
3. **v3.14 定向测试** — `test(execution-plan): cover provider fallback metadata`：包含正式 `test_factors.py` 及新增的 registry/AV gate/Seed/全空/metadata contract tests。前置为第 2 项；需验证 broker→AV、Demo→Seed、全空、字段 shape。
4. **v3.14 文档** — `docs(execution-plan): document kline provider fallback`：包含 v3.14 PRD 与本审计文档更新。前置为已确认最终行为；应修正 PRD 对 HK/Seed 来源的表述。
5. **独立配置迁移** — `chore(config): rename OpenAI environment variable`：包含上表 18 个 F 修改，作为单独可回滚提交；须先全仓搜索旧变量和验证缺失变量行为。它不是 v3.14 前置提交，除非产品明确将其纳入同一发布。
6. **重复/本地文件清理（后续单独授权）**：在确认来源后删除/归档 25 个 E 文件，并把数据库快照移到受控本地备份或补 `.gitignore`；`docs/public/demo_seed_positions.csv` 在确认来源前维持 G。绝不能与功能提交混合。

当前不适合将任何 v3.14 代码直接合并进 `main`。最小可回滚路径是先完成第 1 项和第 2 项的契约缺口，再运行第 3 项验证；随后独立提交文档。尚待产品/所有者确认：Seed 是否允许在非 Demo 末端使用、Seed OHLC 的合法数据来源与固定时间基准、HK 的 AV 支持承诺、`docs/public/demo_seed_positions.csv` 来源，以及 18 个全局 key rename 是否属于另一个开发线。

## v3.14 P1 修复完成记录（2026-07-26）

本审计中确认的 P1 已在 `codex/wealthpilot-v3.14-handover` 收口：新 SQLite 初始化注册执行计划表；K 线 Provider 修复 symbol import、统一 Demo 配置 gate、固定静态 OHLCV fixture 与 metadata 降级契约；重复副本及两份 SQLite 快照已迁至仓库外备份。`docs/public/demo_seed_positions.csv` 因来源未确认仍保留未跟踪，未纳入任何提交。

离线定向验证覆盖数据库建表、Provider fallback、Demo 零网络调用和 FactorSnapshot 元数据。全量测试、静态检查和隔离 Demo 启动结果见本轮交付记录；未运行任何真实行情、LLM、券商或交易路径。

## v3.14 合并前回归基线核对（2026-07-27）

本次以 `/Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python`、同一组空白外部凭证、`PUBLIC_DEMO_MODE=true`、`DEMO_ALLOW_MARKET_DATA=false`、`BROKER_MODE=mock` 与各自独立临时 SQLite，对 `main@11cd2e9` 的 detached worktree 和当前接管分支进行了对照。两侧 `pytest --collect-only -qq` 均为 402 个 nodeid，集合差异为 0；完整 pytest 均为 **352 passed / 43 failed / 7 skipped**，43 个失败 nodeid 完全相同。因此本分支没有 "main 通过、当前失败" 的新增正式测试回归，也没有顺带消除 main 失败。

先前脏工作区的 438 项收集数比 clean baseline 多 36 项，恰好等于三个已验证精确重复副本的现行测试数：`tests/test_legacy_selected_skills_compat 2.py`（5）、`tests/test_skill_manifest 2.py`（18）和 `tests/test_skill_reconcile 2.py`（13）。其余带 ` 2` 的 ExecutionPlan 副本不在 pytest.ini 的正式 testpaths 中，未影响这次 402 项比较。

环境变量迁移未改变测试隔离：本分支当前运行代码只读取 `WEALTHPILOT_OPENAI_API_KEY`，正式 key 依赖测试也使用新变量；本次同时显式置空新旧 Key 与所有已知外部凭证，两侧得到相同失败集合，未发生真实 LLM 请求。archive 中的旧 `OPENAI_API_KEY` 仅为历史材料。

隔离验收使用 `/tmp/wealthpilot-v314-acceptance.db`：启动后 `execution_plans`、`execution_tranches` 均存在；通过 ORM 创建并读取了带 `demo:acceptance` 来源的最小草稿及一个 tranche，未提交订单。Demo registry 为 `[av, seed]`；AV 在网络函数前以“Demo 已禁用外部行情”降级，AAPL 固定 fixture 回退为 `seed`（260 根、截至 2025-12-26、收盘 242.35），重复读取结果一致；metadata 正确包含 `kline_provider:av` 与可读原因，注入 delayed provider 时 `delayed_minutes=15` 透传；非 Demo registry 不注册 Seed。后端 OpenAPI 返回 200，前端开发服务器可由本机 HTTP 访问；本轮浏览器容器无法访问 loopback，故页面交互留给本机人工入口复核。

结论：P1 及本轮回归核对均无合并前阻塞，`codex/wealthpilot-v3.14-handover` 标记为**可合并候选**。仍不执行 merge 或 push；`docs/public/demo_seed_positions.csv` 继续是唯一待产品确认的未跟踪文件。

## Self-use / Private Full Mode 恢复与验收（2026-08-13）

本次已停止隔离 Public Demo，并以现有正式启动路径恢复私有模式：`PUBLIC_DEMO_MODE=false`，后端 `127.0.0.1:8000`，前端 `127.0.0.1:5173`。私有模式不加载 Demo Seed、没有 Demo 密码门，非 Demo K 线 registry 为 `[broker, av]`；运行时使用的 canonical self-use DB 是 `data/wealthpilot.db`，不是临时数据库。启动前已将该主库以 SQLite 安全备份方式保存至仓库外 `~/Documents/WealthPilot-local-backups/self-use-restore-20260813-201117/`，并记录校验和；主库完整性检查为 `ok`，核心业务表及 Execution Plan 表均可读，未执行 destructive migration。

本地 ignored 配置已有新的 `WEALTHPILOT_OPENAI_API_KEY`，无需迁移旧变量。本轮补强并复核交易安全配置：`ENABLE_IBKR_LIVE_TRADING=false`、`ENABLE_TIGER_LIVE_TRADING=false`，IBKR/Tiger/Futu/Snowball 均保持 read-only。没有调用 place、submit、cancel、modify 或 replace，也没有创建真实订单。

验收结果：核心只读 API（Dashboard/Position/Profile/Discipline/Research/Conversation/Action history）均返回正常；真实持仓上下文的单次 Decision SSE 完成 Planning、Executing、Expressing 与 Reviewing，且会话写回成功；Execution Plan 使用真实 broker K 线（260 根、无降级）生成并重新读取了一个本地 `draft`（两个 tranche），未 confirm、未转为 Action/Order。该记录只用于本次只读验收。

发现并最小处理了一项 Private Mode 运行时阻塞：`data/checkpoints.db` 是 LangGraph 的 ignored runtime checkpoint，不是个人持仓主库；其 SQLite `integrity_check` 报损坏，导致 Planning 阶段返回 `database disk image is malformed`。在停止后端后，已把原文件和校验和完整移至上述仓库外备份目录，再让应用创建空 checkpoint；随后 Planning 与真实 LLM 验收恢复。未修改 `wealthpilot.db` 中的持仓、资产、订单或 schema。

IBKR 的 v3.10 PRD 目标架构为 IB Gateway（paper 4002 / live 4001），但历史 probe 实际使用 TWS（paper 7497 / live 7496）。当前私有配置保持既有 TWS Paper `127.0.0.1:7497`、`clientId=10` 与 paper 账户类型；本机所有相关端口当时均未监听。因此本轮没有修改连接架构，也没有运行 IBKR adapter；待用户手动启动并登录对应 TWS/Gateway、开启本地 API 后，才可进行连接、账户、持仓与已有订单的只读 smoke。

当前限制：IBKR 的实际只读连接因本机 TWS/Gateway 未运行而未完成；未主动执行账户同步。真实 Market K 线与 LLM/Execution Plan 已通过，服务保持在线，供所有者在浏览器中确认 Dashboard 为本人真实数据。无 push、无 merge；本节为文档记录，未包含任何密钥、完整账号或资产明细。

### IBKR Live Gateway 只读复验（2026-08-13）

所有者启动并登录 IB Gateway Live 后，`127.0.0.1:4001` 实际监听。ignored 本地配置已从历史 TWS Paper 切换为该 Gateway 的 host/port/clientId 与经 `managedAccounts()` 验证匹配的 Live 账户；不记录完整账号。Gateway Read-Only API 保持开启且仅允许 localhost；WealthPilot 侧 `IBKR_READ_ONLY_MODE=true`、`ENABLE_IBKR_LIVE_TRADING=false`。

以该配置和 `clientId=10` 执行的 IB API 只读 smoke 已成功读取账户、账户摘要、可用资金/购买力/净值/现金/持仓市值等摘要标签、真实持仓以及已有订单；未发现活动订单。全过程没有调用 place、submit、cancel、modify 或 replace。v3.10 PRD 明确不建立 `broker_sync/ibkr/`，持仓同步继续由既有通道承担；因此本次没有把 IBKR 读取结果写入主库，也没有改变 Dashboard 数据。

同时发现现有 `IBKRBrokerAdapter` 的跨线程同步读取在 Live Gateway 的账户快照调用中不能及时返回。最小修复仅让 Live 账户在只读模式连接，并在本地硬拒绝下单和撤单；其 69 项定向测试通过。Gateway 原生只读读取可以证明连接与数据权限，但该 adapter 的读取便利方法仍需单独修复和实测，不应被表述为完整产品链路已通过。故本分支继续保持不 push、不 merge，Self-use 的真实 DB、LLM、行情与 Execution Plan 验收有效，但**尚不满足**以“所有 Private Full Mode 链路均通过”为由 fast-forward 合并 main 的条件。
