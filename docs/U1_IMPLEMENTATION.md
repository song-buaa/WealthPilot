# U1 分析前准备与固定资产视图

> 2026-10-22 状态更新：本模块随4.1 **RELEASED — 已完成生产交付**；完整验收边界及非阻断观察项见[4.1结项记录](releases/v4.1.md)。下文保留原开发/隔离测试/待发布时点，不再作为当前部署状态；不据此推定真实数据已完整或全部自然场景已验证。

原开发基线 `c63c995d4b7ae1533e08f4f38e071a4c94b15931`。U1 PR #24 已以 `e06a6995a2f830aa527cb0a0c506b3153d11dc8d` 合入 main；本文件保留其设计/实现/隔离验收和发布输入，本轮 U2 未访问生产，不能据此推断 U1 实机已启用。U2 独立扩展见 [U2 实现说明](U2_IMPLEMENTATION.md)。

## 1. 最小边界与权威复用

- 原 [SyncExecutor](../backend/services/restricted_sync.py) → 原 [SnapshotRun Repository](../backend/services/broker_sync/repository.py) → 原 Upsert/FX，继续唯一持仓与现金写入入口。U1 不实现另一套同步、账户、现金或收益计算，不消费成交事件。
- [AssetAnalysisPreparation](../backend/services/asset_analysis.py) 只做任务身份、调用原执行器、冻结输入、保存派生视图。它不是独立服务进程/调度器/通用任务队列。仍用原 Backend BackgroundTasks；手动及 22:00 同步不变。
- 原 [PortfolioService](../backend/services/portfolio_service.py)、[WealthService](../backend/services/wealth_service.py) 与原只读 reader 计算全部金额/去重；复用 [WealthSnapshot](../app/models.py) 的 ID 和既有汇总字段承载结果，`asset-view:v1:<id>` 是其只读引用。它不是另一份可编辑持仓事实。
- 原 [业务 worker](../pocs/chatgpt_skills/worker.py) / [PEER 适配](../pocs/chatgpt_skills/analysis.py) 消费固定输入；没有修改 `decision_engine/`。研究、行情、知识库及专业模型仍属原链路，其独立日期不能冒充资产捕获时点。

## 2. prepare、去重与故障语义

后端 `POST /api/asset-analysis/prepare` 仅接受 `analysis_request_id`（1–64 个字母/数字/下划线/连字符）；账户、Portfolio、来源、路径、SQL、交易动作均不接受客户端指定。账户/Portfolio 从原配置/契约与原 executor 解析。

1. SQLite `BEGIN IMMEDIATE` 内查/插 WealthSnapshot 分析请求。相同 Portfolio/request 及 owner/pipeline 指纹只返回同一 preparing/ready/failed/expired 结果；配置/作用域变化须新任务。每 Portfolio 每分钟最多 6 个新请求，重复 ID 不消耗额外额度。
2. 任务认领提交后才券商 I/O。分析触发在原 U0 账户 claim 同一短事务内检查最近完整成功（300 秒）或失败冷却（15 秒），避免“外部先查新鲜度→另一个连接发布→重复同步”的竞态。原人工/cron 不采用该跳过策略。运行中的其他同步不由本任务重启；捕获时真实仍 busy 则 partial。过期账户租约沿 U0 恢复，不把刚标失败的过期 claim 当冷却而回滚恢复。
3. 原服务拒绝空/部分/FX/身份异常，继续保留 Last-good；异常和未配置/owner 冲突进入任务质量。成功 run 只是发布成功，不能作为证券估值新鲜或成交覆盖证明。
4. 任务租约 300 秒；最终短事务重读 token/owner/期限后才保存 ready。已完成 payload 不改写。崩溃/超时不在同 ID 盲目重放；过期明确返回 expired，新分析需要新请求。U1 不增加持久重试队列或 U2 成交补扫。
5. `GET /api/asset-analysis/requests/{id}` 使用同一 prepare 凭据，仅查状态，不联网。prepare 客户端有界等待约 35 秒，未完成返回 preparing；重试必须复用原 ID，不能因不确定而新建任务。

## 3. 固定输入及数据版本

用 SQLite Backup API 获取一致版本，先释放生产读事务，再计算/等待模型。原 worker 在该副本的 `mode=ro` / `query_only` / authorizer 下运行，包含 DataLoader、AllocationService 和 `asyncio.to_thread`；不在完整 backend 中替换全局 engine，不长时间占用生产 WAL。捕获阶段固有禁止网络连接，不重新请求现金、FX 或模型。

- 任务输入是短期私有文件，不是长期账户库：目录 0700、文件 0600、服务器随机文件名，文件 hash 绑定 payload；客户端不能传文件路径。单份 256 MiB、同 UID 最多 8 份，Backup 有界；满额失败可见。
- 输入上下文最多 30 分钟；分析持共享文件锁，清理不删除正在使用的副本。复用原 Backend scheduler 在启用 prepare 时每 5 分钟清理到期副本，并在启动清理；停止进程期间不承诺准点物理删除，访问期限仍严格检查。副本缺失/到期/篡改拒绝专业分析，不能悄悄换当前 DB。后续请求也清理过期文件。
- payload 保存固定 overview、手工项、持仓/账户、Portfolio/Wealth 结果、原汇总数、来源 run 向量、质量、issuer 映射结果/版本；内部 owner/claim/cache 证据不向 MCP/Web 返回。原账号/备注/凭证/原始 SDK 回包不进入外部投影。
- `GET /api/asset-analysis/views/{snapshot_id}` 返回持久 payload；旧副本到期后历史资产核对仍可读。`/api/wealth/summary`、`/api/portfolio/summary`、`/api/portfolio/positions` 增加可选 snapshot_id，提供后严格按同 ID，未提供沿原行为。
- Web `/#/asset-view/<snapshot_id>` 展示本次固定财富、证券、时间/质量和公司敞口；其组合评估调用原单次只读 worker/PEER，传同一 ID。MCP 同 ID 的详情/汇总从 payload 读取，PEER 从 hash 验证的同一副本读取。财富分母、投资持仓分母与 Allocation 正值已分类分母仍不同，不混用。
- 分析请求行可能在准备中具有内部零占位值，绝不能作财富观察。原趋势、月基线及汇总去重明确仅处理 `analysis_request_id IS NULL` 的原观察；分析视图不受金额相同去重吞掉，也不污染趋势。
- 现有消费账户没有投资 Portfolio 绑定，因此固定资产视图不读取全局消费归因，相关 cashflow 字段为 unavailable/NULL；不把另一作用域的消费收入/支出附到本次资产输入。原非快照 summary 默认行为不变，消费归属及历史归因不在 U1 扩展。

## 4. MCP 权限与自动准备交互

原七项工具的 readOnlyHint、数据库只读、最终 Web 人工交易确认均保留。提供 snapshot_id 时固定读取；未提供时仍读存储数据，**不暗中自动刷新**。

为满足 ChatGPT 场景，增加 **1 项显式、可选启用的 `prepare_asset_analysis`**：`readOnlyHint=false`、`destructiveHint=false`、`idempotentHint=true`；它承认会造成同步及本地快照写入，不伪装纯读取。工具仅通过薄 HTTP 适配调用受保护原 Backend，不赋予 MCP 数据库写权限或交易方法。启用后的服务器指引：新组合分析先 prepare 一次，ready 后在财富/持仓/专业分析传同一 snapshot_id；pending 重试同 ID；失败/过期明确解释。没有从相似问题、历史或全局时间缓存猜测同一会话。

**默认仍为原七工具**。未来生产显式启用时：

- Backend 使用 `WEALTHPILOT_ANALYSIS_PREPARE_TOKEN_FILE` 指向本机私有 0600、当前 UID 文件；内容为独立最小权限随机 Token（32–128 个 URL-safe 字符），不是交易凭据。还必须显式配置原 `WEALTHPILOT_ENABLED_SYNC_BROKERS` 来源 allowlist；没有 allowlist 不继承旧手动入口的 all 默认。无配置返回 503；不匹配 403；Demo 默认拒绝真实 prepare。
- 原 MCP 启动增加 `--prepare-config /private/path/prepare.json`；同 UID 0600 文件仅含 `base_url`（固定 `http://127.0.0.1:<port>`，无 path/query/账号）和 `token_file`。禁止代理和重定向；请求路径写死，无任意 URL/SQL/账户动作。未配置该选项时不注册第八项工具。
- Tunnel/主机网络可达不替代 prepare 独立凭据；原 Web 读取沿现有私人访问边界。不得把 Token 发给 ChatGPT、浏览器、Git 或日志。
- 本批不新增统一订单/workflow 状态工具；prepare 状态由本幂等工具/内部只读状态接口提供。订单状态与成交触发留 U2，避免借 U1 扩大范围。

## 5. 时间、质量和建议边界

`account_synced_at`、`positions_as_of`、`cash_as_of`、`price_as_of`、`fx.rate_date/fetched_at`、手工 `value_as_of/last_verified_at`、`captured_at` 分别保留。时间缺失为 unknown，不填今天；同步完成不充当估值日期。

- 账户读取按 300 秒；持仓/现金实际时间缺失、无时区、过期均提示；证券价格按最多 24 小时（纯日期按 1 个日历日，未接交易日历，节假日保守 partial）。未来日期不算新鲜。FX 沿原 4 日及 TTL/Last-good 合同，手工估值按原 30 日建议核实，绝不标为实时 API 资产。
- 固定视图超过 300 秒即撤销精确调仓资格，30 分钟内仍可一般分析；历史 payload 金额不修改。关键未知/失败/过期、分类或 issuer 缺口、养老金未核实链接均 `can_propose_rebalance=false`。这也不赋予任何交易授权。
- 专业分析输入明确加降级限制，响应/conclusion/quality 明确披露，不能将 partial 专业输出当可直接执行的数量、价格或目标方案。原单次模型失败/研究 fallback/市场证据警告照常保留。
- 手工资产、贷款、养老金及原币现金金额仍由原 Wealth/Portfolio 规则处理；`included_in_net_worth`、`already_investment_accounted` 和金额级养老金链接限制不改，不靠名称制造持仓链接。

## 6. 发行人与经济敞口

原 instruments 域新增小型 [issuer 证据映射](../backend/services/instruments/issuers.py)，初始明确覆盖 Li Auto 的 LI:US/USD、2015:HK/HKD 及正式 HK 代码 02015。依据 [公司官方投资者问答](https://ir.lixiang.com/investor-faqs) 及 [2025 年报公告](https://ir.lixiang.com/news-releases/news-release-details/li-auto-inc-files-its-annual-report-form-20-f-3)。中国经济归属指经营风险，不是上市地、交易币种或注册地推测。

只合并直接权益证券的 CNY 风险市值和投资分母权重。每个成员的证券身份、账户引用、数量、成本、币种均单独保留；不合股数、不换算 ADR、不穿透 ETF。无映射、币种/市场矛盾不猜测。国金总额仍沿原存储，不造理想/美团明细或比例，不开发 QMT。

## 7. Schema 与未来生产发布输入

仅 **2 nullable TEXT 列 + 1 partial unique 索引，0 新表**，定义 [u1_schema](../app/u1_schema.py)：

| 现有表变更 | 必要性 |
|---|---|
| `wealth_snapshots.payload_json TEXT NULL` | 版本化派生输入/结果/来源向量及准备状态；旧 NULL 仍只代表汇总观察，不冒充固定视图 |
| `wealth_snapshots.analysis_request_id TEXT NULL` | 跨连接同任务可靠重读/认领，不依赖内存、相似文本或 JSON 全表扫描；Portfolio 参与唯一键 |
| `uq_asset_analysis_request(portfolio_id,analysis_request_id) WHERE analysis_request_id IS NOT NULL` UNIQUE | 在 SQLite 数据库中保证幂等身份；旧观察均 NULL，不新增状态/任务表，不改 U0 claim 索引 |

相比联合审计最初的单 payload 候选，多一列的理由是实际请求并发去重和可索引身份；不依赖 JSON1 表达式索引或全表模糊 JSON 匹配。B0/U0 所有列、索引、receipt 和 owner 合同保留。共享启动只验证 U1 及精确 sqlite_master digest，不自动 DDL/默认 Portfolio。

未来另获生产授权后，原 `scripts/migrate_b0_safety.py --stage u1` 的 plan/apply/verify/resume-contract/mark-writers-enabled/restore-prewrite 流程继续使用。默认仍 B0；U1 明确禁止任何存量数据 corrections。必须停全部 writer、协调读进程、Backup API/克隆演练、原地 inode、inventory/integrity/原索引验证；独立 u1 receipt 更新精确契约，B0/U0 receipt 不改写。

匹配 Backend、MCP、Web 一起切换；旧版本不了解分析行的趋势排除及新字段，不能混跑旧 writer。任何 writer 启动前 mark-writers-enabled。启写后禁止旧备份覆盖、反向 DROP 或随意签 digest；前滚修复或核实的只读降级。仅从未启写且 inventory 完全未变才允许原 prewrite 恢复。原 22:00/手动、实际账户、Gateway 离线、私有 Token/第八工具发现、同 ID Web/MCP/PEER、源日期/质量/金额需 Mini 单独验收，CI 不代替。

## 8. 可重复隔离验证

- [固定视图测试](../tests/test_u1_asset_views.py)：同 ID/跨连接、不同任务账户 race、快速发布重读、失败冷却、过期 U0 claim、Last-good、只读线程、clone 到期/锁/篡改、Portfolio、养老金、issuer 和真实 PEER/Skills（模型 Mock）固定输入。
- [API/权限](../tests/test_u1_api.py)：无 Token/额外字段/跨 Portfolio 拒绝、原 Web 端点按 ID；[Schema 演练](../tests/test_u1_schema.py)：实际 U0 结构、精确 DDL、幂等、旧 receipt、契约中断及新事实后禁止旧备份。
- [MCP 协议](../pocs/chatgpt_skills/test_u1_protocol.py)：独立 MCP 环境经真实 loopback 受保护 HTTP prepare，原工具同 ID/同金额、原七只读权限和重复不刷新。
- [浏览器](../frontend/tests/e2e/asset-view.spec.ts)：固定财富、持仓/公司明细、质量提示、实时表变化不改旧视图、固定 ID 专业分析边界、缺失/手机界面。使用只含合成数据的最小 app，不启动生产 lifespan；真实 PEER 在上述业务测试中验证，浏览器模型结果为合成边界桩。
- 原全量 pytest、原同步/共享库/只读协议、浏览器、frontend lint/build、Offline M5 18/18 与四项 CI 保持；精确数量、SHA/Run 链接以 PR/交付证据为准。未做生产、真实券商、真实付费模型或订单验收。

本轮仅 PR，不合并、不部署、不启动 U2/B2/B3/QMT。

## PR #24 定向修复合同

- **R01 发布事实一致性**：原 Repository `synchronize` 在原 Snapshot/Upsert 发布事务中 flush 后，将实际 scoped Position 投影的 `version/count/sha256` 写入同一 Run 的 `context_json.published_positions`，与 success 一起 commit。没有新列/索引/表，没有第二份金额计算。指纹覆盖账户/来源、证券身份、数量、成本、原币/CNY 金额、FX、分类和 segment；不覆盖展示名称、行 ID 或创建时间。
- 分析 freshness 在原账户 claim 短事务内还要求该发布指纹匹配当前行。缺失证据或行变化（包括同数量内容不同）须走原同步；不伪造历史指纹。旧 U0 run 没有该证据时，首次分析可能多做一次已授权只读同步。冻结后在同一副本上再核对，覆盖 claim 检查与 Backup 之间的其他连接写入；不一致/不能刷新则 `SYNC_PUBLICATION_DIVERGED/UNVERIFIED`、partial、不可精确调仓。合法且有发布证明的空仓指纹可复用；清空/CSV/平台导入功能保持，未知导入现金的原碰撞保护不得放宽。
- 旧固定 payload 金额/ID 不改写；没有新发布校验证据的历史视图仍可核对，但读取时撤销精确资格。原手动及 22:00 发布自然产生新证据，未引入重试队列或额外同步进程。
- **I01 输出时效**：原 PEER 输出前，对同一不可变视图再执行 `public_view`；不查活动持仓、不刷新、不换 ID。来源/视图过期则更新 issues/资格并提示一般分析；运行中超过 30 分钟上下文则返回 unavailable、`ANALYSIS_CONTEXT_EXPIRED`，不提供原专业结构化方案。响应 `data_quality.asset_view` 和 `context_current` 是最终状态，Web 使用它更新提示和按钮。
- **I02 最近尝试与 Last-good**：原 broker_sync API 共用 `safe_sync_reason/public_sync_attempt`，投影 `last_attempt_time/status/reason_code`；last-good run、成功时间、`last_good_quality`、FX/证券时间保持独立。只有原 `SAFE_SYNC_CODES` 可直接返回，原始 SDK/账户/连接异常统一为 `SYNC_SOURCE_FAILED`；外部投影不含发布 hash 或原始异常。
- 正式安全回归：[评审回归](../tests/test_u1_review_regressions.py) 覆盖 Web clear、CSV/平台替换、同条数的身份/数量/金额变化、跨连接冻结竞态、旧 run/旧 view、合法空仓、模型运行跨期、失败原因及敏感文本；[浏览器](../frontend/tests/e2e/asset-view.spec.ts) 验证最终质量替换初始资格，金额与 ID 保持固定。真实 ChatGPT 自然语言工具规划仍须发布后单独验收，测试客户端显式传 ID 不替代它。

## 4.4 A-min合格分母纯投影（本地候选）

入口为`backend/services/investable_assets.py:project_investable_assets`。这是原Portfolio/Wealth对已冻结U1事实的纯投影，不读取数据库、不刷新券商、不解析/激活政策、不写入原payload。未接入prepare、Web、PEER、MCP或PolicySession；不实现B2。PolicyAssets合同及既有资格门不变。

调用者提供原Owner的view、portfolio_id、snapshot_id、snapshot_sha256及显式时区时钟at。hash复用Policy canonical/digest，绑定完整payload；hash只能证明内容绑定，不能认证来源或证明事实正确。调用者必须来自可信原Owner，不能接受用户上传的scope evidence作真实资产资格。

新的内部`data_quality.investable_scope_evidence`与来源同被冻结，schema为`wealthpilot.investable-scope.v1`，包含：

- portfolio_id、snapshot_id、as_of；与原view匹配。
- dispositions：以`position:<id>`或`wealth:<id>`引用原行，无新金额副本。state仅INCLUDED/EXCLUDED/UNKNOWN；reason为FREE_INVESTMENT、LIVING_CASH、EMERGENCY_RESERVE、DEBT_RESERVE、RESTRICTED、PENSION、DUPLICATE、UNKNOWN，须有原Owner evidence_refs。现金用途排除不能直接套到股票上。
- coverage：accounts、manual_assets、pensions、gold、btc、deduplication，各含PRESENT/ABSENT/UNKNOWN、as_of、evidence_refs、asset_refs。账号/手工资产/去重清单与源行逐一对照；ABSENT是有证据的无持有，缺平台/缺证据保持UNKNOWN。
- pension_links：已登记already_investment_accounted的`wealth:<id>`到实际`position:<id>`映射。金额级扣除不证明持仓身份；缺失/冲突映射阻止正式D。

养老金排除还须逐行核对原Owner用途证据：独立手工养老金为EXCLUDED/PENSION；已在投资账户记账的养老金为EXCLUDED/DUPLICATE，且每个链接持仓均为有据EXCLUDED/PENSION。链接存在不代表已排除；仍标为FREE_INVESTMENT或用途冲突时，养老金资格及分母保持UNKNOWN，不自动改写用途。

原来源仍来自security_inputs、原get_manual_wealth_items及wealth totals。金额为CNY major Decimal；保留源精度、不先舍入，JSON为十进制字符串。投资资金D不减全部贷款；原总资产/负债与派生净资产单列。过期债务令净资产UNKNOWN，不单独否决已完整证实的投资分母。用途、账号publication/同步、原持仓/现金/价格时点及FX按原U1纯质量函数重新核验，不采信can_propose_rebalance或客户端合格标志；原5分钟门和manual/FX规则未放宽。

输出为`wealthpilot.investable-projection.v1`，包含来源hash、逐行状态/理由/问题及原PolicyAssets。证据不足时denominator_cny=null、denominator_qualified=false，保留观测金额及UNKNOWN原因；不填0，不输出目标配置或可执行金额。养老金映射生产维护、共享DDL、外部来源接入及真实验收均不在本候选范围。

测试入口：`tests/test_investable_assets.py`，合成隔离、I/O阻断、hash/scope/时钟、负债与分母、FX/来源、用途排除和养老金负面边界。

## 4.4 B2 Owner凭据与ACTIVE配置接线

B2沿原`AssetAnalysisPreparation.run`，在原只读副本capture返回并取得snapshot_id之后、原WealthSnapshot JSON发布之前，由原Wealth Owner调用`freeze_investable_scope`。原Portfolio的全部Position进入可投资范围输入`data_quality.investable_security_inputs`，原账户发布证明进入`investable_accounts`；原`security_inputs/accounts`的投资分析范围保留。均来自同一冻结库，没有第二份持仓权威或共享DDL。A-min优先消费这份完整范围扩展；旧视图没有扩展时保守处理，已知Portfolio行若不在输入内则`INVESTABLE_POSITION_SCOPE_INCOMPLETE`，不能靠用途文件隐藏。

原字段不足以证明生活现金/应急或偿债预留、外部黄金/BTC确实无持有、跨账户养老金链接。最小补充是一份**原资产Owner维护的私有事实证据文件**，由服务端环境`WEALTHPILOT_INVESTABLE_EVIDENCE_FILE`指定；不新建账本、审批平台或公开写接口。本轮不创建任何真实证据文件、不修改真实账户/资产记录。

该文件复用原`app.shared_sqlite.private_file`的同OS用户、私有权限、禁止路径symlink规范，大小不超过256KiB；不存金额、比例、政策或合格布尔值。它不是客户端请求参数，也不能由LLM上传。文件内容为：

| 字段 | 合同 |
|---|---|
| schema_version / portfolio_id | `wealthpilot.owner-scope.v1`及原组合身份 |
| inventory_sha256 | `wealth_service.investable_inventory_identity(view)`；绑定实际全部Position ID、账户引用、证券/币种/用途segment身份及手工资产身份，新增/更换资产使旧证据失效；不复制动态金额或负债 |
| confirmed_at / valid_until | 带时区的Owner事实确认及有效期，最多30天；这是用途/覆盖声明的有效窗口，不是资产/FX价格时点 |
| dispositions / coverage / pension_links | 复用A-min合同；所有覆盖须明确PRESENT/ABSENT/UNKNOWN及证据引用，不能从空库自动推导ABSENT；不提供denominator_qualified等标志 |
| manual_classes（可选） | `wealth:<id>`→`economic_asset_class`及非空白`evidence_refs`；仅已知手工资产可用canonical分类，无金额，不覆盖Position已有canonical分类 |

原Wealth结构化退休产品类型可证明该手工项目的限制用途，缺用途时补EXCLUDED/PENSION；不会覆盖冲突声明，也不能由此推断全部养老金覆盖。已经在投资账户记账的养老金仍要求明确持仓链接及逐项排除，金额去重不能替代身份核对。银行现金可沿原类型归为货币，其他未获分类证据的手工资产保持UNKNOWN。

五类资格只接受原Position原始canonical分类、当前classification_version及VERIFIED/EXPLICIT/DETERMINISTIC来源证明；完整范围输入保留这些原字段，不能使用序列化器为了旧界面兼容生成的legacy asset_class回退。旧记录缺分类证据或CONFLICT/UNVERIFIED时保持分类UNKNOWN，不能由Owner用途文件覆盖。

Owner有效窗口先验证，再生成绑定同snapshot_id/frozen_at的内部scope receipt，保留Owner文件hash、确认时点及原截止时点。账户同步、publication、现金/持仓/价格时点、FX及原手工值30天门仍由A-min独立验证；文件不能补齐或刷新这些事实。GET再次检查Owner截止时间，精确显示有效期为`min(captured_at+5分钟, Owner valid_until)`。hash只绑定内容，不能认证事实本身；Owner必须从实际账户/用途凭据确认，不得为通过测试编造记录。

原`GET /api/allocation/snapshot`支持可选`snapshot_id`，无ID只读最新原U1尝试；不会prepare、刷新券商、取得行情或写库，也不会绕过最新failed/preparing回退旧快照。真实链为RO load_view → A-min → 原PolicyAssets/请求级PolicySession → 原Allocation消费者。请求快照、portfolio、hash、来源时点及ACTIVE政策版本一起固定，响应前再次核对政策版本；PolicyService仍是唯一政策权威。

合格成功保留原AllocationSnapshot字段，附加`policy_allocation`：Decimal字符串分母/五类金额、当前/目标权重、百分点差距、金额差距、主要偏差方向、PolicyReference、来源ID/hash/时点/截止时间。五类目标仅由ACTIVE neutral聚合，未写另一份目标；源CNY金额不舍入，比例除法余数只修正最大类别的最后Decimal位以闭合，金额差距按分ROUND_HALF_EVEN显示。无PolicyAssets公共字段、共享Schema、MCP工具变化。

生产受限Web目录仅追加原Snapshot GET，委派同一handler，保留固定组合/共享库/loopback及同源边界；不挂载整个Allocation router，不开放任何新写方法。该GET未知异常保留B1的sanitized500，其他受限路由的原503处理不变。对应catalog、200/409/503/500、POST拒绝及攻击Host零handler调用由`pocs/poc03_mac_mini/test_restricted_web.py`验证。

缺分母/覆盖/养老金/FX/时点仍B1的409，缺分类409；已知资产库不可读取也受控拒绝，Policy unavailable503，未知程序错误500。`detail`继续为字符串；B2可附加`qualification` UNKNOWN原因及来源引用，不能返回零资产/全绿或交易金额。旧LEGACY、已绑定原P2会话成功结构保留。ACTIVE页面在原政策/组合界面展示差距，到期自动撤销精确金额并显示UNKNOWN，不自动同步；组合穿透风险仍`NOT_ASSESSED_IN_B2`，金额差距只是信息，不是订单建议。

本轮验证入口：`tests/test_b2_allocation.py`、原U1/养老金/B1/Policy/Wealth回归及`frontend/tests/e2e/policy-boundary.spec.ts`。生产事实未连接核验：是否已有完整账户发布证据、用途/外部覆盖确认、养老金映射与当前FX仍UNKNOWN。发布须另行授权，并在原ACTIVE政策和真实Owner确认后取得新的合格U1视图；旧视图不会回填或刷新。缺证据应验收409及明确原因，不能为了200改真实数据；交易资格另行授权。

### 4.4 B3 ACTIVE分层配置差距

原 Allocation GET 在合格 B2 响应中追加 `policy_allocation.hierarchy`。快照 ID、哈希、时点、D 和 ACTIVE Bundle 引用沿用同一父对象。五类资产与 LEGACY 200 兼容，原 409/503/500 语义继续适用。当前范围按同一视图内 A-min 的 INCLUDED CNY 金额分桶，保留逐项引用及分类证据哈希，原凭据引用留在私有冻结视图。UNKNOWN/PARTIAL 不提供分层实际权重、偏差或金额差距，仍披露未分类的金额与引用供查证。

一级与八桶以 D 为分母。目标权重由已固定的 ACTIVE 快照读取。一级规则用未舍入 CNY 差额比较 `abs(当前金额 − D × 目标权重) > D × 政策阈值`，相等时不触发，触发时仅要求人工专项复核。ETF 产品分别展示占子组合和占 D 的口径，D 口径目标为 ACTIVE ETF 桶目标乘产品内部目标。已证实的空 ETF 子组合内部权重未定义。API 保留 Decimal 字符串，浏览器仅在展示时舍入。

ETF 产品只接受官方身份与冻结 canonical 分类相符的 US 上市 SPY/QQQ/SPMO；IB01/CBU3 复用原已验证产品身份。美国直接个股复用 ACTIVE 指定股票的合格 US 直接持仓；中国主动精选可用原已核验中国发行人及合格沪深直接股票。其他可核验的股票及美国股票 ETF 可通过原 `coverage.accounts.evidence_refs` 的逐行 `<asset_ref>:US_DIRECT_EQUITY`、`<asset_ref>:CN_DIRECT_EQUITY` 或 `<asset_ref>:US_EQUITY_ETF` 和独立支持引用补证，同时验证原证券类型，冲突保持 UNKNOWN。其他已核验美国股票 ETF 纳入 ETF 桶及其实际分母，不分配 SPY/QQQ/SPMO 的产品目标。现金按原现金类型及 A-min 可投资用途，计入稳健池。原 Owner 黄金/BTC 覆盖证据必须指向具体记录并与经济分类一致。B3 还要求原 `coverage.<gold|btc>.evidence_refs` 中有对应行的形态确认 `<asset_ref>:GOLD_EXPOSURE` 或 `<asset_ref>:BTC_SPOT_UNLEVERED` 和同一行及事实绑定的支持引用；BTC 当前仅接受如此核验的手工直接持有。此为对既有引用字段的资格检查，不改变 Owner 文件结构或 B2 分母门槛。未证实的地区、基金、产品或现货形态仍为 UNKNOWN。分层结果不包含 ETF 成分、发行人集中度或任何交易授权。

回归入口为 [B3 隔离端到端](../tests/test_b3_allocation.py)及 [Web 政策边界](../frontend/tests/e2e/policy-boundary.spec.ts)。真实 A1 资格和生产验收另行留证。
