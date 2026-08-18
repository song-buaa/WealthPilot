# WealthPilot 项目治理基线

> 更新日期：2026-08-18
> Current development baseline：当前 `main`
> Latest stable release：`v3.15.0`
> Previous stable release：`v3.14.2`
> Immutable historical release：`v3.14.0` → `4314e031ca653ea87a346c198d3a6dc466017ab6`

## 1. 当前产品与架构

WealthPilot 是本地优先的个人投资决策工作台。当前前端为 React 19 + Vite 8 + TypeScript，后端为 FastAPI + SQLAlchemy + SQLite，决策链路为 PEER 四 Agent + 13 个 Skills。核心入口为 `frontend/src`、`backend/main.py`、`backend/agents`、`backend/services`、`skills` 与 `app`。

`main` 是唯一长期开发与集成分支，也是持续演进的 Current development baseline。`v3.15.0` 是 Latest stable release，`v3.14.2` 是 Previous stable release；`v3.14.0` 保留为 immutable historical release，原 Tag 不移动、不重建。

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
- 新工作从最新 `main` 创建短期 `codex/<topic>` 分支，完成验收后使用 fast-forward 或经审查的普通合并回到 `main`，随后删除短期分支。
- 不长期维护 release、develop 或个人 handover 分支；未知历史分支在确认所有者与用途前不删除。
- 每个提交职责单一，不 squash 已有历史；使用精确路径暂存，禁止 `git add .` 和 `git add -A`。
- 未经明确授权，不 push、不合并、不创建或移动 Tag。

## 4. 版本、Tag 与发布

- 版本遵循 SemVer：破坏兼容为 major，向后兼容功能为 minor，修复与文档治理为 patch 或后续 main 提交。
- 稳定 Tag 使用带 `v` 的完整版本号，例如 `v3.14.1`，Tag 必须指向已验收的不可变提交。
- 发布前同步 `README.md`、`AGENTS.md` 与 `CHANGELOG.md`，确认工作区干净、敏感文件未跟踪，并执行相称的后端测试、前端 lint/build 和人工 smoke。
- 发布后验证 local main、origin/main 与预期提交一致；Tag 只在明确发布动作中创建，不因普通文档提交重打或移动。

## 5. 安全边界

- `.env*`、本地 SQLite、券商凭证、私钥、完整账户号和仓库外备份不得提交。
- Public Demo 必须与 self-use 数据库、凭证和网络能力物理/配置隔离。
- IBKR 默认为只读：Gateway Read-Only API 开启，`IBKR_READ_ONLY_MODE=true`，`ENABLE_IBKR_LIVE_TRADING=false`。
- 未经用户逐次明确授权，禁止 place/submit/cancel/modify/replace order 及任何等价 mutation。
- 连接、超时和查询失败必须显式失败，不得伪装成空账户、空持仓或空订单。

## 6. 质量门与当前基线

每次业务代码修改至少运行任务相关定向测试。合并候选应使用同一 Python 环境、环境变量和隔离数据库与 clean `main` 对照，只有“main 通过、候选失败”才属于新增回归。

Mandatory Merge Gates：

- 所有业务代码：`python -m pytest`、frontend `npm run lint`、frontend `npm run build`，以及任务相关定向测试。
- 影响决策主链路：在上述 Gate 之外，必须运行默认 Offline M5：`python scripts/m5_e2e_18_cases.py`，并达到 18/18。
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

GitHub Actions 的 `Quality Gates` workflow 自动执行三个并行检查：

- `backend-tests`：Python 3.11、compileall、全量 pytest；
- `frontend-checks`：Node 22、`npm ci`、lint、build；
- `offline-m5`：确定性 Offline M5 18/18。

Workflow 在面向 `main` 的 Pull Request、`main` 与 `codex/**` 分支 push，以及手动 `workflow_dispatch` 时运行；同一分支或 PR 的新提交会取消旧 Run。权限限定为 `contents: read`，每个 Job 有独立 timeout。

CI 只做自动质量验证，不自动修复、提交、合并、发布或部署；不配置真实 Secret，不访问个人数据库，不调用真实 LLM、行情或券商，也不执行任何订单 mutation。Live M5 继续仅限人工 opt-in，不进入 CI。

### Main branch protection

GitHub Repository Ruleset `Protect main with Quality Gates` 仅保护 `main`：要求 GitHub Actions 的 `Backend tests`、`Frontend checks`、`Offline M5` 三项检查通过，并采用 strict up-to-date 策略；同时要求 linear history，禁止 force push 和删除 `main`。短期开发分支不受该 Ruleset 约束。

当前不强制 Pull Request、Review、Signed Commits、Deployment 或 Merge Queue。标准流程仍为短期分支 push、等待 CI 全绿、确认基于最新 `main` 后 fast-forward 更新 `main`。仓库 Owner 保留显式紧急 bypass；该能力只用于恢复，日常合并仍须遵循 Mandatory Merge Gates。

## 7. 精简技术债 Backlog

1. 审核并归档未知历史分支 `master`、`origin/master` 与 `feat/v3.14-kline-provider`；确认所有者前不删除。
2. 对前端大 bundle 做按路由/重组件拆分。

## 8. 文档权威与仓库卫生

- 当前事实优先级：运行代码与测试证据 → 本文 → `README.md` / `AGENTS.md` / `CHANGELOG.md` → 当前 PRD。
- `docs/CODEX_WEALTHPILOT_HANDOVER_AUDIT.md` 已封存，只用于追溯接管过程；`docs/archive/` 不是当前运行说明。
- 重复副本、导出物和临时报告不得留在仓库。删除前先确认无运行时引用，并在仓库外备份需要保留的材料。
- 收尾目标是 `git status` 完全干净，tracked 文件中不含凭证、真实账户标识或数据库。
