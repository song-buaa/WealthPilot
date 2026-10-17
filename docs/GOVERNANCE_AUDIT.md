# Phase 0｜Governance Audit 与整合映射

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
