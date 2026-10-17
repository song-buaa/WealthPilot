# WealthPilot 项目治理基线

> 更新日期：2026-10-17。本文只维护长期工程流程、风险、质量与发布边界。开发基线为当前 `origin/main`；历史版本/生产快照不是实时状态或新的操作授权。

## 1. 当前产品与架构

产品入口见 [README](../README.md)，系统分层、模块职责和数据权威统一见 [ARCHITECTURE_CURRENT](ARCHITECTURE_CURRENT.md)，已有能力及复用入口见 [CAPABILITY_REGISTRY](CAPABILITY_REGISTRY.md)。本文不重复维护这些实现说明。四份核心文档的职责与引用以第 8 节为准。

## 2. 运行模式

- **MacBook Dev / 隔离测试**：临时 SQLite、`PYTHON_DOTENV_DISABLED=1`、Mock broker、冻结或禁用外部 Provider；不借用生产库、凭证、Tunnel、配置。命令见 [dev_setup](dev_setup.md)。
- **Public Demo**：固定 Seed / 隔离库、`PUBLIC_DEMO_MODE=true`、`BROKER_MODE=mock`、默认禁用外部行情；Provider gate 阻断真实 LLM/行情/券商/交易；Demo 密码缺失 fail closed。
- **Mac Mini Prod / Private Full Mode**：代码与用户本地 DB、配置、凭证、日志、备份分离；真实 Provider、只读调查、同步写入、安装/启停/切换与恢复按当前任务分别授权。生产不直接修改业务代码，修复回 Dev / PR。
- 非 Demo 完整 backend 的 lifespan 包含原调度/同步、孤儿扫描、订单轮询与触发评估；审计/测试不能为图方便启动它。CI 或文档发布均不暗含生产启停授权。

## 3. 分支与提交策略

- `main` 是唯一长期集成分支。新任务 fetch 后从最新 `origin/main` 建短期 `codex/<topic>` 分支；继续已有 PR 时复用其分支并核对 base，不创建重复 PR。
- 保护其他分支/worktree 和未提交工作；未知历史分支、用户文件和 worktree 未确认用途/授权前不删除。不长期维护 develop/release/个人 handover 分支。
- 按可独立验证、可回退的最小职责分步提交，提交信息采用 Conventional Commits 风格（如 `docs:` / `fix:` / `feat:`）；精确路径暂存。禁止 `git add .` / `git add -A`、force push、squash 既有历史、force add ignored 本地配置。
- push/PR、合并、Tag/Release、部署分别授权。用户已明确授权的下一步不重复询问；仅 PR 的任务停止在 PR/CI，明确授权合并的任务按第 6 节完成集成，均不默认部署。

## 4. 版本、Tag 与发布

- SemVer：破坏兼容 major、兼容功能 minor、修复/治理可作 patch 或后续 main 提交。Tag 使用完整 `vX.Y.Z`，只在明确发布授权下绑定已验收的不可变提交，不移动/重建历史 Tag。
- 功能完成、代码合入、组件安装、激活、Tag 和具体业务验收分开记录；package/FastAPI 包版本不代表产品发布版本，main 或全局 current 不证明某个生产组件版本。
- 版本历史见 [CHANGELOG](../CHANGELOG.md)、[release 记录](releases)；2026-10-16 组件观察及旧测试基线保留在[审计报告历史记录](GOVERNANCE_AUDIT.md#5-原治理文档历史记录)。后续状态按时间/组件/完整 SHA 新增证据，不把旧表当实时事实。
- 发布候选须同步相关文档、检查敏感文件、通过适用 Gate；文档 PR 默认不部署。集成/部署步骤统一见第 6 节。

## 5. 安全边界

- `.env*`、本地配置、SQLite、备份、私钥、Token、完整账户号和真实财务 payload 不进 Git / 公开报告 / 未授权外部范围；公开验收仅留脱敏时间、状态和比较结论。
- 券商连接不等于交易授权。IBKR 默认 Gateway Read-Only API 开启、`IBKR_READ_ONLY_MODE=true`、`ENABLE_IBKR_LIVE_TRADING=false`；未经逐次明确授权，禁止 place/submit/cancel/modify/replace 及任何等价 mutation。
- 生产 MCP 只读，最终人工交易确认在原 Web。打开链接、预览、Mock 成交和参数 `confirmed=true` 均不替代真实人工授权；历史私人读取/PoC 授权不能泛化为当前任务或其他 workspace 的权限。
- 查询/连接/超时失败须显式报告，不伪装空账户、空订单或零费用；现有缺源降级例外及质量限制见架构，规范要求不冒充代码已全面满足。
- 共享 SQLite WAL 的 Web/原服务写入和 MCP 只读边界不得放宽。按现有契约核对路径/属主/文件身份/schema/Portfolio/WAL；合法业务更新不与固定迁移 SHA 比较。使用 SQLite Backup API 一致性备份，不直接复制活动库、不因旧 0400 副本说明覆盖权限/数据。实现/恢复证据见 [Batch 1](WealthPilot_4x_Web_Data_Sync_Batch1.md)。
- 凭证默认最小权限。既有个人宽权限例外仅按[原部署记录第 9 节](WealthPilot_4x_Phase1_PoC03_MacMini_Deployment.md#9-tunnel-v0016-修复后继续执行已有配置数据和-key-保留)的单成员/本人空间、普通独立 Key、明确接受及时间/Key 绑定适用，`minimal_permissions_verified=false` 不得改写，其余 Gate 不豁免；不授权共享访问/交易，也不消除历史凭证事故。未核实条件不扩大例外。

## 6. 质量门与当前基线

### 本地与端到端验证

- 任何代码修改：任务相关定向测试、`python -m pytest`、frontend `npm run lint` / `npm run build`、`python scripts/m5_e2e_18_cases.py --mode offline` **18/18**。保留原严格代码 Gate，不因风险分级豁免。
- L0 仅文档：本地执行 `python scripts/check_engineering_docs.py`、`git diff --check`、语义/引用走查；不要求重复本地全部业务测试，但现有四项 CI 仍全跑。必要文档检查配置/脚本的 L0 变更也必须通过完整 CI。
- 测试使用同一 Python 环境、临时库及关闭/冻结 Provider，不以个人 .env/DB 或真实服务可用为前提。怀疑回归须同条件与 clean main 对照；历史测试数量不是当前基线，更不能代替完整业务验收。
- Offline M5 自带环境白名单、冻结 LLM/Search/Knowledge/Clock、固定 Seed、Mock 与公网 socket guard；默认报告到系统临时 artifact，仅显式 `--update-report` 更新 tracked 报告。
- Live M5 仅在独立人工授权下使用 `--mode live` 与 `M5_ALLOW_LIVE_PROVIDER=1`，仍临时 Eval DB、禁止券商/订单 mutation；不进入 CI、不替代离线 Gate。
- 涉及关键业务时验收完整场景：输入/身份/来源 → 规则/数据/状态 → Web/MCP 呈现 → 人工确认边界 → 过期/失败/重试/一致性；Web/iOS 分别记录。测试数量、接口 PASS、健康检查或 synthetic=false 不代表生产业务验收。

### Automated CI

[Quality Gates](../.github/workflows/ci.yml) 保持四项：`Backend tests`（compileall、文档 checker、全量 pytest）、`Frontend checks`（lint/build）、`Offline M5`、`MCP PoC tests`（原隔离协议/共享库/部署 guards 与合成 E2E）。专项环境/命令以 workflow 为准；根 pytest 不覆盖全部专项目录，不能冒充全覆盖。

CI 对 main / codex 分支 push、面向 main 的 PR 及手动 dispatch 运行，contents:read、独立 timeout、同一分支或 PR 新提交取消旧 Run。不配置真实 Secret、不访问个人 DB/真实业务 Provider、不执行订单 mutation；不自动修复、提交、合并或部署。候选 exact HEAD 四项全绿才可集成；Linux CI 不替代 ARM/账号/设备实际验收。

### Main branch protection

2026-10-17 核对的 ruleset `Protect main with Quality Gates` 对 main 要求 linear history、禁止删除/force push、三个 required checks（Backend tests / Frontend checks / Offline M5）及 strict up-to-date；项目额外要求 MCP PoC tests 同样通过。平台未强制 PR/review 不构成绕过授权；项目仍须 PR、人工评审和明确合并授权。每次合并核对实际规则，不为方便关闭规则或使用 owner bypass。

### GitHub 集成与 Mac Mini 部署

1. 检查全部 worktree，fetch 最新 main / PR head，保护无关工作；按授权 push 候选和更新 PR，检查 exact HEAD 的全部四项 CI。失败或实质性问题未解决不能合并。
2. 取得明确合并授权、核对最新 base 与候选 ancestry 后，按 linear-history 允许方式集成；可 fast-forward 推送已验证的相同 HEAD 到 main（不 force / bypass、不 squash 既有历史）。base 变化则形成可审查新候选并重跑 Gate。
3. 核对 PR MERGED、remote main / origin/main / local main 与预期 SHA，检查 main CI，记录 PR/Run/完整 revision。更新 local main 只在对应 worktree 干净且可 fast-forward 时进行，不覆盖其他工作。合并不创建 Tag/Release、不代表生产验收。
4. **另有部署授权时**，先列生产影响、备份/预检/切换/验收/回滚与停止条件；Mini 沿用原 `install-github`，安装确认的 main ancestor 完整 SHA 和 exact-commit 四项 CI 成功。复用原组件环境、共享库、配置和凭证，不重复迁移、不复制 Dev .env。
5. 安装不等于激活；必要一致性数据备份和旧启动入口备份完成后，经授权切换原组件，验收健康/契约/原功能及必要设备场景；失败恢复入口并核对健康。组件 SHA、依赖/资产、时间分别记录；离线包仅应急。
6. 代码回滚前核对数据/schema兼容性，不盲目覆盖共享 WAL。数据库回滚不能撤销券商已接订单；撤单/重试等 mutation 另行授权。常驻恢复、代理网络、每个数据源和真实成交逐项留证，不从 CI/health 推导全部 PASS。

## 7. 当前已知限制与技术债

业务范围、数据质量及待验收项统一维护在[当前架构第 7 节](ARCHITECTURE_CURRENT.md#7-数据质量与验收边界)和能力索引，治理文档不维护第二份能力状态表。养老金金额级去重/刷新验收的原记录保留在[审计报告](GOVERNANCE_AUDIT.md#5-原治理文档历史记录)。这些记录不能扩大下一任务范围。

工程债务：未知历史分支确认所有者前不删除；前端大 bundle 拆分仍待独立任务，不在治理整理中顺带重构。

## 8. 文档权威与仓库卫生

| 文件 | 唯一维护职责 | 引用关系 |
|---|---|---|
| [AGENTS.md](../AGENTS.md) | Codex 默认行为硬规则、启动必读入口 | 引用其余三份核心文档和 README；不复制详细架构/工作流 |
| [ARCHITECTURE_CURRENT.md](ARCHITECTURE_CURRENT.md) | 当前分层/模块边界、数据与状态权威 | 引用 Registry 的代码/测试入口、治理的变更流程；不定义第二套发布规则 |
| [PROJECT_GOVERNANCE.md](PROJECT_GOVERNANCE.md) | 风险、标准开发/复用检查、测试、PR/CI、发布和生产安全 | 引用架构事实、Registry 索引、PR 模板及历史证据 |
| [CAPABILITY_REGISTRY.md](CAPABILITY_REGISTRY.md) | 已有能力、权威入口、复用方式、测试/限制索引 | 具体口径回链架构，维护条件由第 9 节定义；不创建业务权威或独立工作流 |

实际代码和相应运行/测试证据优先于过时实现文档；出现冲突必须报告证据及影响，并在同一任务修正文档；无法核实写 UNKNOWN，不自行编造。代码事实不豁免权限，安全冲突保留更严格边界。README 是产品/上手入口，CHANGELOG 是历史，PRD 是目标；均不自动证明当前实现/生产状态。

历史报告/PoC/归档保留当时事实，不作为长期阶段规则或当前授权；不删除仍有引用的历史材料。重复副本/导出物删除前核对引用，需保留者先在仓库外备份。收尾检查任务 worktree 干净且无敏感 tracked 文件，不要求清理其他用户工作区。

## 9. 标准开发流程与风险分级

开发前 **需求/非目标 → 能力检索 → 架构影响 → 最小变更方案**：用 Registry 和代码检索已有服务、adapter、模型、API/MCP 和测试；记录 owner、来源/账户/币种/单位/时间/去重、缺口、复用路径、权限及验收。索引没有列出不代表能力不存在。

| 等级 | 示例与默认工作流 | 验证/人工边界 |
|---|---|---|
| L0 文档/规范 | 原文档整合、检查配置，无业务行为改变 | 文档检查/走查与四项 CI；不启动业务/生产；按任务授权 PR 或集成 |
| L1 局部 Bug/UI | 复现、定位原 owner、最小修复，无需独立架构评审 | 代码 Gate + 对应 smoke/E2E；不因局部修复获得生产权限 |
| L2 跨模块/口径/接口 | 列边界、状态/账户、兼容与最小扩展，复用原 owner | 代码 Gate + 合约/完整用户场景；新增平行体系先有架构依据/迁移退出及人工批准 |
| L3 交易/生产/权限/迁移 | L2 加权限、必要备份、预检/恢复及停止条件 | 原安全/幂等/partial/unknown/恢复验收；真实调用/同步写/启停/恢复/交易分别授权，未做标 runtime-pending |

风险取最敏感路径。开发中执行 AGENTS 的 Reuse First / Minimal Diff / Single Source of Truth / Thin MCP 等硬规则；架构影响按原专项设计或 PR 留可追溯决策，不引入新的大型治理框架。

### Registry 维护检查

每个 PR 必须给出能力变化判断，按以下触发条件处理：

- **新增业务能力**：检查 Registry 是否需新增条目或扩展已有条目；需要则同 PR 更新，不需要则说明已有覆盖及理由。
- **修改权威代码入口或职责边界**：必须同 PR 更新对应条目/链接，口径或 owner 关系改变同时更新架构。
- **废弃、迁移、合并能力**：必须同 PR 更新状态、替代入口、复用建议与限制，避免继续指向旧 owner。
- **无能力变化（如局部 Bug）**：PR 写“Registry 无需更新”及原因；不要求机械修改索引或无关架构文档。

### 提交与验收

出现失败先停止依赖该结果的后续步骤，报告证据与影响、定位原因，再做定点修复和重新验证；不盲试、不跳过失败继续发布。

1. 复核最小 diff/敏感性并精确暂存；执行第 6 节适用 Gate 与完整场景，CI-only 和本地证据分开。
2. 按上面的 Registry 条件同步文档；其他架构/接口/数据口径变化更新对应现行文档/契约，无变化写 N/A + 原因。
3. 填[PR 模板](../.github/pull_request_template.md)：问题/结果、复用证据、owner、风险/权限、检查/E2E、文档和未验收项。已确认、推断、UNKNOWN、runtime-pending 分开；无证据不勾选 PASS。
4. 等最新 HEAD 四项 CI，通过且获合并授权后按第 6 节集成。生产部署必须另有明确授权，不跟随文档/代码合入自动执行。

### 文档检查与会话验证

[现有 checker](../scripts/check_engineering_docs.py) 检查活入口/PR 模板的本地文件链接、标题锚点和必读引用，不请求远端 URL、不导入业务、不验证全部历史档案或文字语义。新增活入口按需加入原检查清单，不扩大成复杂治理工具。

新会话从根 AGENTS 发现必读引用并检索代码；不支持自动加载的客户端需显式加载。验证应记录独立会话、目标 revision/文档快照、读取与检索证据、最小方案和权限判断；跨设备未完成如实列明，不冒充全客户端保证。实际验证记录见[现有报告](GOVERNANCE_VALIDATION_REPORT.md)。
