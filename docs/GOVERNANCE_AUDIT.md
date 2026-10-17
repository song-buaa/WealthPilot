# Phase 0｜Governance Audit 与整合映射

> 第 1–4 节记录初轮审计/仅 PR 授权，第 5 节保留历史摘录；最终修订、独立验证和后续授权范围见第 6 节，不用历史阶段限制覆盖当前用户指令。
> 2026-10-17；审计基线：fetch 后 `origin/main = bc19b66f724767b5fb36e1c74a2378942a537f81`。任务：规范审计、文档整合、模拟验证、PR/CI；不进入 4.1 业务审计或开发。

## 1. 审计范围与方法

读取根 AGENTS、README、PROJECT_GOVERNANCE 后检查原工作区/分支/remote。原工作区为 `codex/phase1-poc01-private-mcp`，有未提交资料；未覆盖、搬移或暂存这些文件。本任务另建 managed worktree，分支 `codex/phase0-engineering-governance`，起点为上述最新远端 main。

`git ls-files '*AGENTS.md'` 在基线仅发现根 AGENTS，没有子目录规则；基线 tracked docs 下有 160 个 Markdown 文件。对目录、标题、引用、架构/设计/工作流/发布关键字做清点，精读现行总览/治理、交易及 MCP/同步实现；版本化设计与历史报告按专题和引用检查，不声称逐行复审所有历史财务/验收资料。未发现独立 ADR 目录/ADR 文件、CONTRIBUTING 或 PR 模板；设计决策实际保存在现有专项 PRD/报告和 PR。保留这种轻量方式，重要变更补决策证据即可。

检查依据是代码文件/符号、CI 配置和现有文档，不读取真实 DB、.env、Keychain、私钥或原始财务 payload；不连接 Mini、券商或生产 MCP。生产记录只引用原文日期，不当作本次 live proof。

## 2. 主要发现与处理

以下“原文件”均指基线版本，可用 `git show bc19b66:<path>` 追溯；修改后链接指向整合结果。

| 编号 / 结论 | 原证据与代码核对 | 本轮处理 |
|---|---|---|
| G01 入口过重且缺强制能力检索 | 根 AGENTS 385 行，混有 v3 结构/路线图；启动清单未强制架构及能力索引。现行总览已存在于 [ARCHITECTURE_CURRENT](ARCHITECTURE_CURRENT.md) | AGENTS 精简为可执行规则，强制读取现行架构/能力/治理；架构细节不再复制 |
| G02 架构有主线但 owner/数据权威不够显式 | [原架构](ARCHITECTURE_CURRENT.md) 已说明完整 Core、共享 WAL、七 MCP、Mini 完整 Web；[PortfolioService](../backend/services/portfolio_service.py)、[WealthService](../backend/services/wealth_service.py)、[OrderManager](../backend/services/action/order_manager.py) 的职责需跨文件拼接 | 在现有架构增加分层/数据权威表、交易对象/同步 owner 与扩展原则，不新建第二份 ARCHITECTURE.md |
| G03 缺已有能力入口索引 | 原 README/AGENTS 按历史版本重复大量内部说明，没有模块/接口/测试/限制的一体检索入口 | 新增 [CAPABILITY_REGISTRY](CAPABILITY_REGISTRY.md)，覆盖本轮要求的全部能力，并含单标专业分析；不是完整逐函数目录 |
| G04 默认 M5 开发说明过时 | 原 [dev_setup](dev_setup.md) 只讲代理、在线 backend timeout；[M5 脚本](../scripts/m5_e2e_18_cases.py) 与 CI 已使用 deterministic offline | 更新为隔离临时库、dotenv disabled / Mock / Provider 边界；保留 NO_PROXY 仅用于授权的本地 HTTP 调查，不写全局 shell 配置 |
| G05 流程与 PR 留证不完整 | [原治理](PROJECT_GOVERNANCE.md) 已有分支/发布/安全/质量门；[CI](../.github/workflows/ci.yml) 有四项 Job，但基线无 PR 模板和文档 checker | 将风险分级与提交前流程整合进原治理；新增 PR 模板；原 Backend tests Job 加入离线文档检查，保持四 Job、权限和部署行为 |
| G06 代码测试规则冲突 | 原 AGENTS 要求所有代码改动 M5，原治理仅决策链路必跑 | 保留更严格要求：任何代码变更均定向+pytest+frontend lint/build+Offline M5；L0 文档本地只需结构检查，仍全部 CI。没有因文档整理降低原代码 Gate |
| G07 平台合并能力与任务授权须区分 | 2026-10-17 `gh api repos/song-buaa/WealthPilot/rulesets/20829961`：active、main、linear history/禁止删除和 force push、三个 required checks 且 strict；无强制 PR/review 规则。传统 branch-protection API 404，不能由此推定无保护 | 项目工作流明确 PR/人工评审/明确合并授权；四项 CI 的项目要求保留。未修改 GitHub ruleset；本任务只 PR，不 fast-forward main |
| G08 投资与完整财富、现金及分类不可混算 | [wealth_service._current_totals / _manual_totals](../backend/services/wealth_service.py) 复用 Portfolio 且金额级去重；[PortfolioService](../backend/services/portfolio_service.py) 注入 Tiger 现金；[classification](../backend/services/instruments/classification.py) canonical | 架构及索引列权威、分母、时间/单位、养老金限制和 UNKNOWN；不新增 wealth 持仓或收益数据模型 |
| G09 IBKR 同步沿用 snowball 历史身份 | [SnowballSyncService](../backend/services/broker_sync/snowball/sync_service.py) 的 IBKR 分支读取持仓/现金；[IBKRPortfolioAdapter](../backend/services/broker_sync/snowball/adapter.py) BROKER_NAME=snowball、source_channel=ibkr；[upsert](../backend/services/broker_sync/position_upsert_service.py) 按账户/来源对账 | 显式告知复用入口及标识，防止后续新增第二个 ibkr 同步通道或现金表 |
| G10 成交状态不等于持仓已刷新 | [poller._poll_once](../backend/services/action/order_poller.py) → [sync_order_status](../backend/services/action/order_manager.py) 更新 OrderRecord、累计量和 Batch linkage；链路未接持仓同步。[broker_sync](../backend/api/broker_sync.py) / [main](../backend/main.py) 提供手动/定时同步 | 标记触发/重试闭环为缺口；只输出复用优先方案，未实现。完整模拟见[验证报告](GOVERNANCE_VALIDATION_REPORT.md) |
| G11 生产 MCP 不提供订单状态工具 | [server.create_server](../pocs/chatgpt_skills/server.py) 在 real_config 分支注册七项后返回；Mock get_trade_order_status 会写订单/审计 | 不把 Mock 工具移到 real；未来查询需评审只读投影；薄 MCP 保留原业务 owner |
| G12 失败语义存在遗留例外 | [PortfolioService._get_tiger_account_cash](../backend/services/portfolio_service.py) 缺配置/异常时返回 0；[MCP SourceLogProbe/read_overview](../pocs/poc02_wealth_mcp/reader.py) 记录失败/partial | 不编造“所有现金缺源均已 fail closed”；架构明确已有行为与规范要求之间的差异，业务修复不在本轮 |
| G13 历史设计/部署结论易被当现行事实 | [旧 3.x 审计](WealthPilot_3x_Current_State_Audit.md)、[旧交易代码审计](TRADE_EXECUTION_BATCH_CURRENT_CODE_AUDIT.md)、[PoC03 部署](WealthPilot_4x_Phase1_PoC03_MacMini_Deployment.md) 含当时缺口/受限部署方案；当前 [架构](ARCHITECTURE_CURRENT.md) 已有完整系统 | 保留文件及引用，不删除或搬移；旧报告加适用范围提示。现行生产观察注明时间，本轮不扩大验收 |

## 3. Documentation Consolidation Map

| 旧文档/资产 | 处置 | 现行职责 / 去向 |
|---|---|---|
| 根 AGENTS.md | 更新、精简 | 默认任务入口、必读、硬规则、权限及交付；详细架构归原架构文档 |
| README.md 架构长说明 | 更新、去重复 | 保留产品/快速开始/目录；详细 PEER/Skills/模型枚举改链接至现行架构/能力索引，历史 SVG 标记保留 |
| docs/ARCHITECTURE_CURRENT.md | 原地扩展 | 唯一架构总览，增加 owner、数据权威、交易/同步边界及待核实关系 |
| docs/PROJECT_GOVERNANCE.md | 原地扩展、修冲突 | 唯一详细工程工作流、风险矩阵、质量/发布/安全、文档权威；保留历史版本观察日期 |
| docs/dev_setup.md | 原地重写过时命令 | 当前隔离开发/文档检查命令，不另建 CONTRIBUTING/开发流程文件 |
| .github/workflows/ci.yml | 最小更新 | 在 Backend tests 增加 checker，原四项检查与权限/隔离配置保留，无部署步骤 |
| 原无 PR 模板、能力索引、文档检查 | 新增必要缺口 | .github/pull_request_template.md、CAPABILITY_REGISTRY.md、scripts/check_engineering_docs.py；不新增业务服务 |
| 原 3.x 现状审计 / 交易代码审计 / PoC03 部署文档 | 原路径保留，增加历史适用范围提示 | 历史证据/可复用步骤，不覆盖当前架构、代码或新的授权 |
| ASSET_CLASSIFICATION_CONTRACT、PERSONAL_FINANCE_INTEGRATION_V1、4x P2 / P345 / Web_Data_Sync_Batch1/2 | 保留，不复制全文 | 专项契约/实施与验收；由现行架构和索引引用，阶段记录不自动等于最新 Prod |
| docs/v3.*、releases、Pattern 专项及消费专题 | 保留 | 原专项设计/历史证据；重要架构决策在原设计/PR 留痕，无需新增平行 ADR 体系 |
| docs/CODEX_WEALTHPILOT_HANDOVER_AUDIT.md、docs/archive/、docs/archived/ | 已封存/历史状态保持 | 不改写历史结论，不删除仍有引用的文件 |
| 本次审计/模拟结果 | 新增两份交付报告 | GOVERNANCE_AUDIT.md（含映射）及 GOVERNANCE_VALIDATION_REPORT.md；不是新的规则权威 |

本轮删除文件 **0**，移动/物理归档 **0**；“历史”提示仅限定证据适用范围。没有因去重复删除历史文件或代码。未来整理未检查的旧链接，应另立范围，不扩大本 PR。

## 4. 验证与交付定位

验证规则：活入口相对链接/标题/必读引用机器检查 + 代码入口人工核对 + 模拟需求逐项走查；不把结构检查当语义或生产验收。checker 的破坏性输入测试只在临时副本运行，检查缺目标、坏标题和缺必读引用均被拒绝，源码工作区无这些故障。

本地验证记录（2026-10-17）：

| 检查 | 结果与证据边界 |
|---|---|
| 活文档结构检查 | PASS：9 份入口文档，文件/标题链接、必读引用；本机系统 Python 3.7.1 及项目 Python 3.11.13 均可执行 |
| checker 正/反向控制 | PASS：1 个有效临时样例；缺目标、坏标题、缺必读引用、越出仓库 4 类无效样例均拒绝；不修改业务数据 |
| 模拟任务走查 | 已执行根入口重读、订单/同步/MCP/测试检索；六项结果及独立会话验证限制见验证报告 |
| Offline M5 | PASS：18/18，provider=offline_fixture，public_network_attempts=0；临时 Eval DB/报告，无真实调用 |
| 变更边界及 diff | `git diff --check` 通过；backend/app/decision_engine/intent_engine/frontend/skills/pocs 无业务 diff；最终精确文件见 PR |
| 全量 pytest / 前端 / MCP 集成 | 由原四项 GitHub CI 在 exact HEAD 执行；最终状态和 Run 链接以 PR/交付记录为准，不用本地 M5 代替 |

本任务是 L0；不修改业务代码、Schema、数据库/财务数据、生产配置或凭证。文档 checker/CI 是唯一代码与配置变更。未运行真实 Provider、生产同步、服务重启或交易，不创建 Tag/Release。精确修改文件、最终 commit、PR 和 exact-HEAD 四项 CI 结果记录在 PR 与交付消息中，避免把文档自己的 SHA 循环写入内容。CI 失败/未完成须如实报告，不能以旧 main PASS 代替候选验证。

剩余规范边界：机器检查不保证任意客户端自动加载/服从；历史档案不做全量链接清理；生产四组件版本及运行状态未在本轮复核。以上不阻塞本轮文档整合，不自动启动 4.1 工作。


## 5. 原治理文档历史记录

以下从 PR #19 初稿 `bf6701c` 的治理文档原样保留，分别对应当时观察、测试基线和阶段验收；不是本轮实时核验或长期任务授权。当前流程回到 [PROJECT_GOVERNANCE](PROJECT_GOVERNANCE.md)，部署/数据事实见现行架构及其带日期证据。将原治理中的历史记录移至本报告，不删除历史文件或撤销有效安全约束。

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

2026-08-14 在 `/Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python`、临时 SQLite、空外部密钥、Public Demo/Mock broker 安全配置下建立的新基线：

- 后端全量 pytest：收集 423 项，`416 passed, 7 skipped, 0 failed, 0 warnings`。
- IBKR adapter 定向测试：`86 passed`；仅使用 mock runtime，未连接 Gateway 或执行订单 mutation。
- Execution Plan 定向测试：`98 passed, 0 warnings`。
- Offline M5：连续三次 `18/18`，报告哈希一致；使用冻结 fixture，公网连接尝试为 `0`。
- Python compileall：通过。
- 前端 lint：`0 errors, 0 warnings`。
- 前端 build：通过；仍有单 chunk 超过 500 kB 的非阻断提示。

从该基线起，上述 mandatory merge gates 持续适用。测试必须使用临时数据库和显式关闭或冻结的外部 Provider，不能以本地 `.env`、个人数据库或真实服务可用性作为通过条件。

### 财富总览 v0.1 已知限制与验收范围（2026-09-07）

- 状态：**刷新一致性问题已完成**。按用户本轮确认的范围收口；不是对养老金账户/持仓级精确去重的验收。
- 个人养老金的 `already_investment_accounted` 当前属于金额级去重：从实时 Portfolio 汇总中扣除已登记金额，不代表已建立账户或持仓身份映射。
- 底层养老金持仓净值变化时，登记金额与实际 Portfolio 养老金资产可能产生小额偏差。用户已明确接受此限制，不阻塞本版本收口。
- 后续建设账户级 / holding-level mapping 时统一解决精确去重；本轮不新增账户映射、持仓映射或其他数据模型。
- “刷新一致性问题”与精确去重分别验收：同步完成后自动获取最新 Portfolio 数据、两页各自 API/UI 一致、SPA 进入及 focus / visibility / 同步通知触发刷新，无须手工刷新浏览器，也不增加财富页投资同步入口。
- 刷新验收证据由 `frontend/tests/e2e/wealth-portfolio-consistency.spec.ts`（临时 SQLite、真实 Portfolio/Wealth service）与 `wealth-refresh.spec.ts` 覆盖；真实金额对账证据保留在仓库外私有审计报告，不提交个人财务数据。
- 本轮复验：两个浏览器测试通过，真实服务测试逐项验证 focus、visibilitychange、portfolio-updated 都产生新的 summary 请求并显示正确金额；frontend lint/build 通过。未修改后端业务代码，沿用上一轮后端全量 892 passed、7 skipped 及 Offline M5 18/18 结果。

### PoC 03 个人宽权限运行凭证例外

默认最小权限 Read + Use；用户可明确接受独立普通生产 Key 的宽权限风险，仅适用单成员个人组织及本人个人 workspace。使用既有 deploy.py 的 `accept-runtime-key-risk` 记录时间和当前 Key 绑定，`minimal_permissions_verified=false` 不得改写；其余七项生产 Gate 不可豁免。例外不解决 PoC 02 旧凭证事故（仍 FAIL），不豁免旧 Key 对新范围的隔离核对，不授权交易或共享访问。执行步骤见 [部署说明第 9 节](WealthPilot_4x_Phase1_PoC03_MacMini_Deployment.md#9-tunnel-v0016-修复后继续执行已有配置数据和-key-保留)。


## 6. 最终修订与收口记录

本轮在 PR #19 原分支上追加，不新建规范文件、重命名核心文件或引入检查框架。治理文档由 210 行缩至 121 行：旧组件版本表、测试数量、养老金阶段验收和 PoC 细节保留到第 5 节/原历史说明，长期规则只引用；移除初轮“本轮仅 PR”作为长期限制，按当前任务分别授权 PR、合并与部署。

四份核心职责以治理第 8 节为唯一分工表；AGENTS 保留默认行为和必读引用，架构维护模块/数据权威，Registry 维护复用索引，治理维护详细流程/质量/发布。文档与代码/运行证据冲突须报告并修正；未知项保留 UNKNOWN，安全约束不因代码事实豁免。

Registry 的新增能力检查、权威入口/职责变更、废弃/迁移/合并强制同步，以及无能力变化注明理由的豁免，统一定义于治理第 9 节，PR 模板显式检查。未要求小 Bug 机械修改索引。

本轮仅修改八份已有 Markdown：四份核心、PR 模板、两份报告、CLAUDE 指针。CLAUDE 的 v3.2 介绍由独立新会话发现并改为现行入口引用，质量 Gate 回链治理；无业务代码、Schema、业务数据、CI/检查器变更，也未新增文件。

[独立新会话验证结果](GOVERNANCE_VALIDATION_REPORT.md#7-最终修订后的独立新会话验证)包含读取顺序、稳定文档快照、能力检索、最小方案和权限边界。本次 Registry 业务条目无需更新：没有新增/迁移业务能力或改变权威入口。跨设备未完成、客户端自动注入普适性未证明，为非阻塞验证限制。模拟需求的触发可靠性、账户关联和源数据时序仍属未来设计/验收，不在本轮展开。

本地文档 checker 和 diff 检查通过；最终 exact-HEAD 四项 CI、授权合并和 main CI 以 PR #19/交付记录为准，不在提交前虚写 PASS。用户本轮已授权在无新实质问题且 CI 全绿后合并；未授权生产部署、业务审计或功能开发，合并/核对完成后停止。
