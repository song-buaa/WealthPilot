# WealthPilot 项目治理基线

> 更新日期：2026-10-17（Phase 0 工程规范整合；生产观察仍为原日期）
> 当前产品：WealthPilot 4.0 五大核心场景已收口；Current development baseline：当前 `main`
> Historical latest SemVer stable release：`v3.15.1`（不代表当前 main）
> Previous stable release：`v3.15.0`
> Immutable historical release：`v3.14.0` → `4314e031ca653ea87a346c198d3a6dc466017ab6`

## 1. 当前产品与架构

WealthPilot 4.0 是个人 ChatGPT Pro + 七项生产只读 MCP + 原完整 Core + React Web 的私人财富管理工作台。P1 资产负债、P2 交易预填链接、P3 单标决策、P4 组合评估、P5 当前持仓收益分析均已完成功能范围收口。Web/iOS 接入和 Mac Mini 部署已验收；P2 新入口首次真实提交/成交及 P5 完整历史归因不在 DONE 事实中。

ChatGPT 负责交互、上下文和最终回答组织；原 FastAPI、PEER 四 Agent、13 内部 Skills、规则/信号、专业 LLM/Expressing 和模板继续生成专业分析。Mini 运行完整 Web/后端与 MCP/Tunnel、生产数据库；受限 Web 及单工具 PoC 是历史路径。组件、工具和数据质量边界见 [当前架构](ARCHITECTURE_CURRENT.md)。

`main` 是唯一长期集成分支；功能完成、代码发布、组件部署、Tag 和具体运行验收分开记录。历史 PoC/Batch 文档只反映当时事实，不再作为默认产品/任务约束。文档不得将未来优化写成已实现，也不能因旧阶段限制否认已部署的完整系统。

## 2. 运行模式

### Public Demo

- 使用隔离的临时 SQLite 数据库与提交到仓库的固定 Seed。
- 设置 `PUBLIC_DEMO_MODE=true`、`BROKER_MODE=mock`，默认关闭外部行情。
- Provider gate 必须阻断真实 LLM、行情、券商和交易路径；Demo 密码缺失时 fail-closed。
- Demo 数据、草稿和测试结果不得写入真实 self-use 数据库。

### Self-use / Private Full Mode

- 使用用户本地、Git ignored 的数据库和环境配置。
- 可按用户明确授权连接真实 LLM、行情和券商只读接口。
- 凭证、完整账户号、数据库、备份及 `.env` 永远不得写入 tracked 文件或日志。
- 券商连接不等于交易授权。真实交易权限必须按任务单独、明确放行。

## 3. 分支与提交策略

- `main` 是唯一长期活跃分支，也是 GitHub 默认分支。
- 新工作 fetch 后从最新 `origin/main` 创建短期 `codex/<topic>` 分支，完成验收后通过 PR 和实际分支保护允许的合并方式回到 `main`；清理分支/工作树须有用户授权，不删除未确认历史或未提交工作。
- 不长期维护 release、develop 或个人 handover 分支；未知历史分支在确认所有者与用途前不删除。
- 每个提交职责单一，不 squash 已有历史；使用精确路径暂存，禁止 `git add .` 和 `git add -A`。
- 未经明确授权，不 push、不合并、不创建或移动 Tag。

## 4. 版本、Tag 与发布

### 2026-10-16 版本观察表

| 对象 | 已核实事实 | 如何解释 |
|---|---|---|
| 本次文档整理起点的 remote main | `8b648db4cf7c714cd43eef0d4e437f22d061eae6`，PR #10；在 P3/P4/P5 的 `62f2f8168727acfea159f3f57858e6e109b20610` 之后，包含 IB01 交易链接兼容修复；main 四项 CI SUCCESS | 这是起点快照；最新 main 用 fetch / rev-parse 重新读取 |
| Mini MCP 业务入口 | 启动包装器固定 `8b648db4cf7c714cd43eef0d4e437f22d061eae6`，job 运行 | 不等于其复用的 SDK Python 环境 SHA |
| Mini 完整后端 / Web | 两个启动入口均引用 `8b648db4cf7c714cd43eef0d4e437f22d061eae6`，jobs 运行，health HTTP 200 | 本次只读核对启动引用与健康，不新增交易或 IB01 成交验收 |
| Mini 复用环境 / 资产 / current | MCP SDK 环境为 `4ebf2cb05cb26c77dfd04ab41ef81e69592ddfcb`；分析工作目录资产锚点 `36ef2cb40791e421ab76833cb1dead383b8c540d`；全局 current 为 `d5c4291ed0202a460be1f51056fd69748065fda6` | 都不能替代组件业务 SHA；Tunnel 保持独立原身份，不由 main/current 推定版本 |
| 本次 docs-only 提交 | 基于上述 main，准确 SHA 见该文档 PR / `git log -1 -- README.md AGENTS.md docs/ARCHITECTURE_CURRENT.md docs/PROJECT_GOVERNANCE.md CHANGELOG.md` | PR 未合并前不是 main；即使合并也不自动部署。避免在文档中循环嵌入自己的提交 SHA |
| 已存在 SemVer Tag | 历史最新 `v3.15.1` → `5492b1dbead99677e0e84d8a177a78e6e7157036`（peeled commit）；另有历史功能里程碑 Tag | 不代表当前 main 或生产版本；Tag 不移动/重建 |
| `v4.0.0` | remote 尚无此 Tag，4.0 是用户确认的产品功能收口 | 不因文档收口自动创建 Tag / GitHub Release |

观察时间为 2026-10-16 08:27–08:28 UTC（Mini 启动文件/launchd/health 只读检查；不读财务数据、不重启服务）。完整验收与数据质量见当前架构；后续设备变更必须新增时间与组件版本记录，不能把本表当永久实时状态。前端 package 0.0.0 / FastAPI 1.0.0 不是产品发布版本。

- 版本遵循 SemVer：破坏兼容为 major，向后兼容功能为 minor，修复与文档治理为 patch 或后续 main 提交。
- 稳定 Tag 使用带 `v` 的完整版本号，必须绑定操作者批准的不可变提交及其验收范围；功能 DONE 不代表所有实盘、数据质量或恢复项目 PASS。
- 发布前同步本次相关核心文档，检查工作区与敏感文件，执行适用质量 Gate。文档-only 改动检查链接/格式/一致性并走现有全部 CI，不通过文档发布触发真实运行或重新验收交易。
- 发布后核对 local main、origin/main、remote main；组件部署 SHA 单列。未经授权不创建/移动 Tag 或 Release。

## 5. 安全边界

- 当前生产使用共享 SQLite WAL，Web/原业务服务受控写、MCP 只读；身份/属主/schema/Portfolio 校验与 SQLite Backup API 备份恢复保留。历史 migration-reference 不作为运行期资产快照；合法更新不再比固定迁移 SHA。不得因旧 PoC 的 0400 固定副本说明盲目改权限/覆盖库；回滚前核对模式与兼容性，历史步骤见 [Batch 1](WealthPilot_4x_Web_Data_Sync_Batch1.md)。

- `.env*`、本地 SQLite、券商凭证、私钥、完整账户号和仓库外备份不得提交。
- Public Demo 必须与 self-use 数据库、凭证和网络能力物理/配置隔离。
- IBKR 默认为只读：Gateway Read-Only API 开启，`IBKR_READ_ONLY_MODE=true`，`ENABLE_IBKR_LIVE_TRADING=false`。
- 未经用户逐次明确授权，禁止 place/submit/cancel/modify/replace order 及任何等价 mutation。
- 连接、超时和查询失败必须显式失败，不得伪装成空账户、空持仓或空订单。
- 生产七工具只读；P2 仅 URL，最终确认/委托在原 Web。用户已授权个人范围内必要财富、持仓和分析读取，不授权其他工作区、凭证外传或自动交易。
- 完整 backend.main 已用于 Mini，原 lifespan 的调度/同步、扫描/轮询/触发评估仍存在；后续重启须核对真实任务/券商配置。文档更新不启停任何服务，不扩大交易授权。

## 6. 质量门与当前基线

每次业务代码修改至少运行任务相关定向测试。合并候选应使用同一 Python 环境、环境变量和隔离数据库与 clean `main` 对照，只有“main 通过、候选失败”才属于新增回归。

Mandatory Merge Gates：

- 所有业务代码：`python -m pytest`、frontend `npm run lint`、frontend `npm run build`，以及任务相关定向测试。
- 任何代码修改（保留原 AGENTS 更严格要求）：在上述 Gate 之外，必须运行默认 Offline M5：`python scripts/m5_e2e_18_cases.py`，并达到 18/18。
- Offline M5 使用显式环境白名单、临时 SQLite、固定 Demo Seed、冻结 LLM/Search/Knowledge/Clock fixture、Mock broker 与公网 socket guard；本地 `.env`、个人数据库和真实 Provider 不属于测试前提。
- 默认 M5 报告写入系统临时 artifact；只有显式 `--update-report` 才能更新 tracked `docs/m5_e2e_report.md`。

Live M5 仅用于人工、授权型 Provider 观察：必须同时使用 `--mode live` 与 `M5_ALLOW_LIVE_PROVIDER=1`，仍使用临时 Eval DB 且禁止券商和订单 mutation。Live M5 具有模型与外部服务波动，不是 deterministic merge gate，也不替代 Offline M5。

2026-08-14 在 `/Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python`、临时 SQLite、空外部密钥、Public Demo/Mock broker 安全配置下建立的新基线：

- 后端全量 pytest：收集 423 项，`416 passed, 7 skipped, 0 failed, 0 warnings`。
- IBKR adapter 定向测试：`86 passed`；仅使用 mock runtime，未连接 Gateway 或执行订单 mutation。
- Execution Plan 定向测试：`98 passed, 0 warnings`。
- Offline M5：连续三次 `18/18`，报告哈希一致；使用冻结 fixture，公网连接尝试为 `0`。
- Python compileall：通过。
- 前端 lint：`0 errors, 0 warnings`。
- 前端 build：通过；仍有单 chunk 超过 500 kB 的非阻断提示。

从该基线起，上述 mandatory merge gates 持续适用。测试必须使用临时数据库和显式关闭或冻结的外部 Provider，不能以本地 `.env`、个人数据库或真实服务可用性作为通过条件。

### Automated CI

文档规则检查 `python scripts/check_engineering_docs.py` 集成在原 `backend-tests` Job；仍保留原四项 Job 和权限，不新增部署步骤。该检查只读仓库，不导入业务模块、不连接网络/数据库。

GitHub Actions 的 `Quality Gates` workflow 当前执行四个并行检查，前三项原有 Gate 保持：

- `backend-tests`：Python 3.11、compileall、全量 pytest；
- `frontend-checks`：Node 22、`npm ci`、lint、build；
- `offline-m5`：确定性 Offline M5 18/18。

PoC 集成后增加第四项 `mcp-poc-tests` / `MCP PoC tests`：临时库的只读/部署/GitHub guards 和独立 MCP 环境 HTTP 测试。PoC 01/02 的同名 test_http.py 需在两个 pytest 进程分别执行，避免收集冲突。全部四项须通过才发布当前业务或文档候选；Linux CI 不代替 Mac Mini ARM 和账号实测。

Workflow 在面向 `main` 的 Pull Request、`main` 与 `codex/**` 分支 push，以及手动 `workflow_dispatch` 时运行；同一分支或 PR 的新提交会取消旧 Run。权限限定为 `contents: read`，每个 Job 有独立 timeout。

CI 只做自动质量验证，不自动修复、提交、合并、发布或部署；不配置真实 Secret，不访问个人数据库，不调用真实 LLM、行情或券商，也不执行任何订单 mutation。Live M5 继续仅限人工 opt-in，不进入 CI。

### Main branch protection

GitHub Repository Ruleset `Protect main with Quality Gates` 仅保护 `main`：要求 GitHub Actions 的 `Backend tests`、`Frontend checks`、`Offline M5` 三项检查通过，并采用 strict up-to-date 策略；同时要求 linear history，禁止 force push 和删除 `main`。短期开发分支不受该 Ruleset 约束。

2026-10-17 通过 GitHub rulesets API 核实：规则集未强制 Pull Request、Review、Signed Commits、Deployment 或 Merge Queue；这描述平台配置，不构成绕过审查授权。项目标准流程必须短期分支 / PR / 全部四项 CI / 人工评审及明确合并授权；本轮仅 PR，禁止合并。确认基于最新 `main` 后才按实际 linear-history 允许的方式集成。仓库 Owner 保留显式紧急 bypass；该能力只用于恢复，日常合并仍须遵循 Mandatory Merge Gates。

### GitHub 发布与 Mac Mini 部署标准流程

1. MacBook Dev fetch 最新 origin/main，检查全部工作树，保护未提交文件，从最新 main 建立干净短期分支；只整合已验证且属于任务的成果。不 force push、不 squash 已有历史。
2. 检查拟新增提交的全部文件/blob 历史：不含真实数据库、Key/Token/私钥、私人账户或财务 payload；保留用户授权的仓库外备份。公开仓库中的真实验收证据只留时间/状态/金额比较布尔结论。
3. 业务变更本机运行相称的全量 pytest、frontend lint/build、Offline M5 和专项；文档-only 检查格式、链接、代码/部署事实及一致性。精确暂存提交，push 开发分支，创建 PR。检查 exact HEAD 的四项 Actions 结果，任何失败/未解决代码发布阻塞都不能合并。
4. 取得明确合并授权后重新核对 origin/main 和 base；按现有 linear-history/strict Gates 集成。PR/CI 全绿本身不提供合并授权；经授权可 fast-forward 推送相同 HEAD 到 main（无 bypass，GitHub 自动识别 PR 已合入）；不制造违反 linear-history 的 merge commit。base 变化时重新形成可审查候选并重跑 Gate，不强推历史。
5. 验证 GitHub PR 状态、remote main、local main、origin/main 和预期 SHA 一致，记录 CI/PR/完整 revision。普通 main 发布不自动创建稳定 Tag 或 GitHub Release，不代表生产验收。
6. 需要部署时，Mini 操作者以确认的完整 SHA 使用现有 `install-github`；仅接受 main ancestor 和 exact-commit 四项 CI 成功。安装不等于激活；复用原组件环境、共享数据及个人凭证，不重复迁移数据库或配置全部生产参数。文档-only PR 默认不部署。
7. 安装后按实际受影响组件进行独立预检、备份旧入口、经授权切换原 launchd job、健康/契约/原功能回归及必要 Web/iOS 验收；失败恢复入口并验证健康，不盲目覆盖业务数据库。记录组件 SHA、依赖/资产版本、时间和结果。常驻、MacBook 关闭、退出/重启、备份恢复、每个数据源及真实订单均按单项证据验收，不能由 CI 或健康检查推定通过。Prod 禁止直接改业务代码；修复回 Dev 发布新的 SHA。

MacBook 为 Dev，Mac Mini 为 Prod；代码/文档发布不暗含设备变更授权。离线包为应急备份。历史 Stage 1/2 说明保留可复用的安装/回滚步骤，但其“尚未上线”或“只部署 MCP”等计划不再覆盖当前完整生产架构。

PoC02 凭证展示事故保持历史 FAIL。后续旧 Key 撤销后的权限检查独立记录，不能抹除事故或把未检查事项写成补救通过。当前功能范围收口不增加凭证、数据迁移或交易授权。

## 7. 当前已知限制与技术债

4.0 功能范围已收口，以下属于后续优化或单项待验收，不作为“4.0 尚未部署”的理由：

- 资产/分析数据仍可能 PARTIAL：来源失败、过期、日期未知、研究无法验证、原则索引缺失或演示 fallback。synthetic=false 不保证实时证据质量；默认目标/画像不当作个人已确认设定。
- P2 新链接入口首次真实订单提交/成交未验收；不得用历史其他入口的实盘 UAT 代替。
- P5 当前持仓账面盈亏已用，完整历史归因、TWR/MWR、已实现交易/因果归因未建设。
- 部分券商 Gateway / 数据源未配置或不可用；真实同步、常驻恢复、代理/跨网访问等逐项保留 PASS/BLOCKED/待验收，未验证不标 PASS。
- PoC02 历史凭证事故仍 FAIL；后续撤销检查单列。

技术债继续保留：

1. 审核并归档未知历史分支 `master`、`origin/master` 与 `feat/v3.14-kline-provider`；确认所有者前不删除。
2. 对前端大 bundle 做按路由/重组件拆分。

### 财富总览 v0.1 已知限制与验收范围（2026-09-07）

- 状态：**刷新一致性问题已完成**。按用户本轮确认的范围收口；不是对养老金账户/持仓级精确去重的验收。
- 个人养老金的 `already_investment_accounted` 当前属于金额级去重：从实时 Portfolio 汇总中扣除已登记金额，不代表已建立账户或持仓身份映射。
- 底层养老金持仓净值变化时，登记金额与实际 Portfolio 养老金资产可能产生小额偏差。用户已明确接受此限制，不阻塞本版本收口。
- 后续建设账户级 / holding-level mapping 时统一解决精确去重；本轮不新增账户映射、持仓映射或其他数据模型。
- “刷新一致性问题”与精确去重分别验收：同步完成后自动获取最新 Portfolio 数据、两页各自 API/UI 一致、SPA 进入及 focus / visibility / 同步通知触发刷新，无须手工刷新浏览器，也不增加财富页投资同步入口。
- 刷新验收证据由 `frontend/tests/e2e/wealth-portfolio-consistency.spec.ts`（临时 SQLite、真实 Portfolio/Wealth service）与 `wealth-refresh.spec.ts` 覆盖；真实金额对账证据保留在仓库外私有审计报告，不提交个人财务数据。
- 本轮复验：两个浏览器测试通过，真实服务测试逐项验证 focus、visibilitychange、portfolio-updated 都产生新的 summary 请求并显示正确金额；frontend lint/build 通过。未修改后端业务代码，沿用上一轮后端全量 892 passed、7 skipped 及 Offline M5 18/18 结果。

## 8. 文档权威与仓库卫生

- 当前实现事实以代码和相应测试证据为准；治理规则由本文和根 AGENTS 规定，架构 owner/依赖见 [当前架构](ARCHITECTURE_CURRENT.md)，复用入口见[能力索引](CAPABILITY_REGISTRY.md)。README 是产品/入口，CHANGELOG 是变更历史，PRD 是目标设计，均不代替运行证据。代码事实不豁免生产/交易权限；冲突保留更严格安全边界并报告，未核实项标 UNKNOWN。
- `docs/CODEX_WEALTHPILOT_HANDOVER_AUDIT.md` 已封存，只用于追溯接管过程；`docs/archive/` 不是当前运行说明。
- 重复副本、导出物和临时报告不得留在仓库。删除前先确认无运行时引用，并在仓库外备份需要保留的材料。
- 收尾目标是 `git status` 完全干净，tracked 文件中不含凭证、真实账户标识或数据库。

### PoC 03 个人宽权限运行凭证例外

默认最小权限 Read + Use；用户可明确接受独立普通生产 Key 的宽权限风险，仅适用单成员个人组织及本人个人 workspace。使用既有 deploy.py 的 `accept-runtime-key-risk` 记录时间和当前 Key 绑定，`minimal_permissions_verified=false` 不得改写；其余七项生产 Gate 不可豁免。例外不解决 PoC 02 旧凭证事故（仍 FAIL），不豁免旧 Key 对新范围的隔离核对，不授权交易或共享访问。执行步骤见 [部署说明第 9 节](WealthPilot_4x_Phase1_PoC03_MacMini_Deployment.md#9-tunnel-v0016-修复后继续执行已有配置数据和-key-保留)。


## 9. 标准开发流程与风险分级

任务先完成 **需求/非目标 → 能力检索 → 架构影响 → 最小变更方案**，再实施。工作记录或 PR 至少说明：已有入口/测试路径、选定 owner、读写和数据口径、现有缺口、复用方式、验收场景及权限。未发现能力时继续查服务、adapter、模型、API 和测试，不根据文件名直接新建体系。

| 等级 | 示例与默认工作流 | 本地验证和验收 | 人工确认边界 |
|---|---|---|---|
| L0 文档/规范 | 无业务行为变化；审计现状、更新原文档、检查链接/一致性 | docs checker、diff check；原四项 CI 全跑。无需启动业务/生产或重新做实盘 UAT | push/PR 按任务授权；仅 PR 则到 CI 报告停止 |
| L1 局部 Bug/UI | 复现 → 定位原 owner → 最小修复；无需独立架构评审 | 代码 Gate + 相关定向测试；影响用户行为时对应 smoke/E2E，说明未触及场景 | 原范围内实施；不能把局部修复当生产变更授权 |
| L2 跨模块/口径/接口 | 列影响服务、状态、账户/币种/单位/时间/去重和兼容方案；复用原 owner | 代码 Gate + 合约/回归 + 完整用户场景；涉及 Web/MCP 时分别验证；记录失败/缺源语义 | 新平行服务/模型/流程须先记录架构依据和人工批准；不必为常规扩展另建 ADR 体系 |
| L3 交易/生产/权限/Schema迁移 | 在 L2 基础上，先离线准备可审查方案，列权限、备份、预检、回滚和停止条件 | 原安全 Gate + 幂等/部分成交/unknown/失败与恢复；按授权做生产或交易验收，未做则 runtime-pending | 真实调用、生产写/同步、服务启停、数据恢复、交易 mutation、合并、部署分别明确授权 |

风险按最敏感影响路径取最高级；L0 中的文档检查脚本/CI 配置修改仍须通过完整 CI。此次 Phase 0 为 L0，不修改业务代码、Schema 或数据，不执行模拟需求的实现。

### 开发中

- 先扩展原服务/适配器/模型，维持数据和状态 owner；MCP/前端不复制投资、资金、收益计算。跨模块 owner 见当前架构第 8–9 节。
- Reuse First 的证据是“检索路径 + 现有入口/测试 + 缺口”；Minimal Diff 的证据是限定文件和非目标；Single Source of Truth 的证据是字段来源、单位、账户/来源范围及更新时机。
- 若必须新增平行体系，先在现有专项设计或 PR 留决策记录：问题、检索结果、为何不能扩展原 owner、权威/迁移/退出、兼容和测试；人工明确批准后实施。不能默默创建另一个 Portfolio、收益账本或交易流程。
- 修改架构/能力/接口/口径同步更新当前架构、能力索引及原专项契约；仅局部实现无契约变化可标 N/A 并解释。不因“未来 4.1”将目标当现行事实。

### 提交前

1. 查看 diff/新增文件与数据敏感性；精确暂存，排除用户无关工作。
2. 执行适用本地 Gate：任何代码改动保留定向测试、全量 pytest、frontend lint/build、Offline M5 18/18。仅文档不要求本地重复整套业务测试，但仍走现有全部 CI；CI-only 覆盖与本地证据分别写明。
3. 完整用户场景按风险验证，不能只记接口 PASS：输入/身份与来源 → 业务计算/状态 → Web/MCP 呈现 → 最终确认边界 → 错误/过期/重试/一致性。用临时库和 Mock；真实账户及订单需要独立授权。
4. 历史失败仅在相同 Python/环境/临时 DB 的 clean main 对照后归因；不能因旧基线数字较小就报回归。
5. 运行 `python scripts/check_engineering_docs.py` 和 `git diff --check`，填 [PR 模板](../.github/pull_request_template.md)。提交 SHA 和 CI 结果留 PR/交付记录，避免将文档自己的 SHA 循环嵌入。

### PR 与发布前

- PR 描述问题、结果、复用/owner、风险、检查、端到端证据、文档、未验收项和生产影响；N/A 必须有理由，不虚勾选。四项 CI 查看 exact HEAD，失败/未完成必须如实标记。
- 仅有 PR/CI 不自动获得合并/部署权。发布前另做生产影响、必要一致性备份、已确认代码 SHA、授权切换、组件/原功能/设备验收及回滚准备；沿用第 6 节，不复制 Dev .env，不在 Prod 修业务代码。
- 代码回滚不能盲目覆盖共享 WAL 业务数据；资金/订单不能用数据库回滚撤销；回滚与任何券商 mutation 各自授权。

### 文档维护与机器检查范围

[check_engineering_docs.py](../scripts/check_engineering_docs.py) 检查本轮活入口文档/PR 模板的相对链接、文件/标题锚点和必读入口引用；不请求远端 URL、不验证整份历史档案，也不能证明语义正确、部署状态或真实用户体验。新增活入口应加入检查清单。结构检查通过后仍需审查代码证据和权限边界。

不同 Codex 会话/设备从仓库根 AGENTS 自动发现规则；若客户端不支持 AGENTS 自动加载，操作者必须在任务启动显式加载根文件及必读引用。文档无法证明所有客户端都自动服从，模拟验证只验证本次会话的可发现性与执行结果，见[验证报告](GOVERNANCE_VALIDATION_REPORT.md)。
