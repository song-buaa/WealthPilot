# AGENTS.md

WealthPilot 的 Codex 默认规则入口。开发基线为 `origin/main`；4.1 已 RELEASED，交付证据及持续观察边界见[结项记录](docs/releases/v4.1.md)。4.2 PR #39 已合并，用户报告已部署 b6b5a17；首轮真实投研验收未通过，修复沿短期独立分支实施，生产操作仍须单独授权；发布状态不代表每个数据源或自然场景全面验证。详细架构、流程和能力索引各自维护，职责分工见[治理文档第 8 节](docs/PROJECT_GOVERNANCE.md#8-文档权威与仓库卫生)。

## 每个任务的启动顺序

1. 读取本文件、[README](README.md)、[治理与工作流](docs/PROJECT_GOVERNANCE.md)、[当前架构](docs/ARCHITECTURE_CURRENT.md)、[能力索引](docs/CAPABILITY_REGISTRY.md)。检查目标目录及祖先目录的 AGENTS.md；更深规则仅作用于相应目录，不能削弱生产/交易边界。
2. 检查 `git status --short`、branch、HEAD、remote 与已有 worktree。保护用户未提交工作；代码任务先 `git fetch origin main`，从最新 `origin/main` 建立短期 `codex/<topic>` 分支，必要时用独立 worktree。
3. 写出需求/非目标、风险等级、已有能力及证据路径、权威数据与状态 owner、最小变更方案、测试/端到端验收和权限范围。用 `rg` 检索服务、适配器、模型、API、MCP 和测试；不能只读文档就认定缺少能力。
4. 发现文档/代码冲突，报告差异、代码证据及影响，并在同一任务修正文档；无法核实保留 UNKNOWN。历史 PRD、PoC、验收报告仅作追溯；无法核实的部署/关系写 UNKNOWN 或待核实，不用旧报告推定当前生产。

## 实施规则

- **Reuse First / Minimal Diff**：优先扩展能力索引中的权威实现；只改需求必需路径。PR 写明复用了什么、为什么已有入口不足。
- **Single Source of Truth / Clear Ownership**：按当前架构的数据权威表选来源，写明币种、单位、账户归属、时间、去重和状态 owner；前端、MCP 不复制业务计算。
- **Thin MCP**：仅做协议、参数校验、权限约束与结果适配，专业分析调用原 PEER/业务服务。资产/交易读取不获得隐式写权限；既有显式prepare及E1独立研究预览确认写遵守各自授权，不能扩大到交易。任何写工具变更均走高风险审查。
- **No Parallel System**：新增平行交易、持仓、收益服务/模型/流程前，必须在 PR 或现有设计文档中记录无法复用的原因、权威归属、迁移/退出方案，并取得人工明确批准后才实施。不得以新入口需求绕开原确认、风控或券商适配器。
- PEER 是唯一决策主链路；禁止恢复 `USE_V3_AGENTS` 等双轨开关。不得直接修改 `decision_engine/` 核心引擎；新增能力沿用 Skill + Tool / `invoke_skill()`，不在 ExecutingAgent 堆业务 if-else。不改内部 SKILL.md 的 `type` / `entry_point` / `tool_name` 契约。
- 五层投资决策架构（政策约束、共享证据、分析Skills、综合决策、授权执行与复盘）是业务职责视图，继续由原PEER跨层编排，不新增Agent Runtime；当前实现与目标缺口分别见[当前架构](docs/ARCHITECTURE_CURRENT.md)和[统一架构](docs/v4.3/UNIFIED_INVESTMENT_DECISION_ARCHITECTURE.md)。
- Skill封装可复用能力及其输入/输出、证据、适用范围与成熟度；Strategy组合能力与研究方法，不直接等于Skill。优先复用原Tool/Service和数据Owner；投资理念、投资纪律、长期资产配置三类政策由用户批准的理念/投资政策两份私有Markdown承载。内容批准不等于系统ACTIVE；[Policy Bundle P1](docs/v4.3/INVESTMENT_POLICY_P1.md)仅为未接入候选，JSON/索引不得成为第三套人工权威，不擅改批准原文或激活政策。
- 研究结果、工程接通、生产验收及策略获批资格分别留证；未经验证的策略仅作研究参考。综合决策须解释冲突和未知，不能用投票或分数相加替代判断；建议、E1保存确认与具体交易授权分开，执行必须经过原用户确认和风控。
- 外部依赖 optional：分析缺源应显式降级；交易安全校验缺合约、报价、费用或资金证据时 fail closed。失败不得伪装空账户/空订单/零费用；不得把所有业务失败一律吞掉。
- **Documentation as Code**：先更新原文档，避免第二份权威说明；Registry 的新增/入口或职责变更/废弃迁移合并检查及无变化豁免统一按[治理规则](docs/PROJECT_GOVERNANCE.md#registry-维护检查)执行。

## 风险、权限与隔离

风险工作流和质量矩阵以[治理文档](docs/PROJECT_GOVERNANCE.md#9-标准开发流程与风险分级)为唯一详细规则：L0 文档、L1 局部修复、L2 跨模块/口径、L3 生产/交易/迁移。简单 Bug 不需要独立架构评审；不能据此豁免原有代码 Gate。

- MacBook = Dev，Mac Mini = Prod。开发/测试使用临时 SQLite、显式禁用 dotenv、Mock broker 和冻结/禁用外部 Provider，不借用生产库、凭证、Tunnel 或配置。不要为审计/测试启动完整生产 backend；lifespan 会启动原调度和订单任务。
- 生产只读调查、真实 Provider/付费调用、同步写入、服务安装/重启/切换、备份恢复分别按任务授权；代码/文档 PR 不包含这些授权。未获授权仅做离线准备。
- 券商连接不等于交易授权。IBKR 默认 Gateway Read-Only API、`IBKR_READ_ONLY_MODE=true`、`ENABLE_IBKR_LIVE_TRADING=false`；禁止未经逐次明确授权的 place/submit/cancel/modify/replace 及等价 mutation。最终人工确认必须保留在原 Web，打开预填链接或 Mock 成交不是实盘验收。
- 生产账户数据只在用户授权的私人范围处理；不上传完整持仓/交易数据到 Git、公开报告或未经授权的外部服务。不得提交 `.env*`、数据库、备份、私钥、Token、完整账户号及本地配置；不 force add ignored 文件。
- push/PR、合并、Tag/Release、部署分别授权。用户已明确授权的操作不重复询问；没有授权时先完成可审查准备。要求“仅 PR”的任务在 PR/CI 交付后停止。

## 提交与交付

- 沟通直接、结构清楚，说明结论、证据、影响和可简化方案；以产品体验与长期可维护性判断取舍，避免为短期省事引入过度设计。
- 精确路径暂存，每提交职责单一，禁用 `git add .` / `git add -A`；不 force push、不 squash 既有历史、不删除未知分支/用户工作。
- 测试与验收只维护在[治理文档第 6 节](docs/PROJECT_GOVERNANCE.md#6-质量门与当前基线)：L0 文档检查/全部 CI，代码变更保持原全量 Gate，关键业务另做完整场景；不依据本文摘要降低要求。离线命令与隔离见[dev_setup](docs/dev_setup.md)。
- PR 使用[模板](.github/pull_request_template.md)，列出风险、复用证据、owner、检查、端到端场景、文档和未验收项。CI 查看 exact HEAD 的全部四项结果；CI 不自动合并或部署。
- 结果区分已确认、推断、UNKNOWN、运行待验收；记录完整 SHA、PR/CI 链接、失败原因与范围。不得用测试数量、单接口 PASS 或健康检查替代实际业务验收。历史测试债务需相同环境/隔离数据库与 clean main 对照。
