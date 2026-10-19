# U1 分析前准备与固定资产视图

开发基线 `c63c995d4b7ae1533e08f4f38e071a4c94b15931`。本文件为 U1 PR 的设计/实现/验收及未来发布输入，**未合并、未部署，未访问生产**。U0 已由用户确认正式发布，不能据此推断 U1 已启用。

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
