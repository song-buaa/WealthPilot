# U0 统一资产同步底座：实现与发布输入

状态：开发候选，仅开发/隔离测试/PR。基线 `12d9357f1543156db379a94ace8eb359c107c30e`。用户确认 B0 已正式部署；本轮复核其既有发布记录及代码合同，未连接 Mini、生产库、Gateway 或真实券商。生产安装、Schema 迁移和同步验收需单独授权。

架构 owner 见 [当前架构](ARCHITECTURE_CURRENT.md#12-u0-统一同步底座已合入)，复用索引见 [Registry](CAPABILITY_REGISTRY.md#u0-可复用合同已合入)，通用流程仍以 [治理规范](PROJECT_GOVERNANCE.md) 为准。

## 1. 范围与最小实现

| 原模块 | 本批变更 / 不变量 |
|---|---|
| `restricted_sync.SyncExecutor` / `broker_sync` API | 统一完整 backend、手动、原 22:00、restricted 入口；显式 Portfolio 和已解析账户；数据库认领先于源构造/I/O。内存锁只是优化，跨进程正确性不靠锁。未配置源写 failed，manual all 继续已配置源；未新增 scheduler |
| 原 Repository / SnapshotRun | BEGIN IMMEDIATE 短事务提交 token/300 秒租约；发布重新读取并验证 owner/token/租约，快照、业务行、success 同事务提交。失败只更新自己的 running。并发返回 busy，过期后下一请求才创建新 run；不把旧 run 改写为新成功 |
| 原 Upsert | 发布前校验 scope、重复 canonical symbol（含同 symbol 不同币种）；FX 在写事务前冻结。异常空/部分读取/重复/冲突拒绝发布；保留 last-good。现有 name backfill 收窄到当前 Portfolio 的当前接收账户，同事务提交 |
| Tiger | STK、FUND、prime assets 任一缺失都失败；一次取原币 cash_balance，与持仓同 run 持久化，排除 BASE，零/负余额保留。逐条校验 STK/FUND 原始 account 及 prime assets.account；缺失/混合/不匹配均拒绝。空证券还须 SDK 3.5.8 Segment.gross_position_value 有限且为 0 的独立证明；仅覆盖 STK/FUND/CASH，不清理未覆盖旧证券 |
| IBKR / 历史 snowball | 保持 broker=snowball、source_channel=ibkr 和同实际账户；原 adapter 在已等待 accountDownloadEnd 后标明完成。空证券须此证据，缺现金拒绝发布。完整 backend 仅注入原 SDK loop 的只读 facade，不改交易开关 |
| Futu / Guojin | 仍用原适配器；Futu 指定真实 acc_id、保留 HK 覆盖外旧行；Guojin owner 预先由显式配置或已唯一归属的历史证据解析，并核对返回行，不再 I/O 后换 owner。原 QMT 特殊 stale 规则保留，未接入新源 |
| FXService | 结构化 FXQuote；latest TTL 300 秒、失败/降级 15 秒，来源日超过 4 个日历日 stale。失败保留 last-good 原日期；硬编码仅明示 fallback；未知无 1:1。非零估值缺 rate 则全账户保留 last-good；零事实可保留但 rate=NULL，禁止 ORM 默认填 1 |
| Portfolio / 原两个 MCP reader | 移除读时 Tiger SDK 调用和虚拟现金追加，统一读原币 CASH；MCP 仍 mode=ro/query_only/authorizer，七工具权限不变。未同步现金与确认零余额区分，过滤证券查询不误报账户现金缺失 |
| 原迁移/参考校验工具 | 显式 `--stage u0`，不静默改变默认 B0；保留原备份、receipt、digest、安全回滚。新 valuation reference 从同库 CASH 取值；携带外部现金的历史 reference 必须重新采集，不暗中重放 |

只读 wrapper 固有拒绝交易 mutation，不依赖 full backend 的 live flags 为 false。外部来源可能只支持部分证券；发布 success 是持久化成功，quality=partial 仍须保留，不能宣称完整资产已实时可信。

## 2. 合同与失败语义

Scope 固定为 `(portfolio_id, broker, account_id, sync_source)`；完整物理账户仅一个 canonical Portfolio。IBKR 交易 owner 到 snowball 持仓 owner 的 B0 映射不变，NULL owner 不自动推断。同步不从订单成交量推算持仓或扣现金。

`PositionSnapshotRun.context_json` v1 是内部合同：`version/token/lease_until` 只作认领证据；来源输出 `source/coverage/markets/complete/empty_confirmed`，时间为 `fetched_at/positions_as_of/cash_as_of/price_as_of`，结果包括 `currencies/row_count/quality/issues/fx`。FX 项含 `rate/rate_date/fetched_at/source/quality/issues`。缺失来源时间保留 NULL；抓取、同步完成时间不是行情估值时间。JSON 仅由服务器现有适配器/Repository 生成，不接受客户端任意 JSON，也不写 credentials/原始账户响应。

状态仍用原 running/success/failed；没有 queued/retry_wait 或独立任务表。短暂网络错误最多原有 2 次重试，等待和 FX/券商 I/O 期间无 SQLite 写事务。无 heartbeat：超 300 秒任务丧失发布权，后续请求可重试；不把任意迟到结果当新数据。legacy running context=NULL 无法证明过期，返回 busy，须停写后人工核实；禁止自动回放。

原 status 字段继续表示最近尝试；新增 `last_successful_run_id` 与 `data_quality` 是最近成功结果的来源/质量投影，失败尝试与 last-good 分别呈现。API allowlist 不暴露 token/lease 或原始账号。quality 是发布时评估，不是查询时的实时性承诺。MCP 另外检查最近失败、来源老化和存储 FX 日期；缺元数据明确 UNKNOWN/partial。

## 3. Schema 清单与兼容

**1 nullable 列 + 1 partial 唯一索引 + 0 新表。** 不改 WealthSnapshot，不增加交易字段或状态。

| 表 / 变更 | 必要性 |
|---|---|
| `position_snapshot_runs.context_json TEXT NULL` | durable claim/lease/fence、来源覆盖与分时点、冻结 FX/质量证据；复用既有 run，不建另一套同步记录。旧 NULL 不伪造恢复或质量证明 |
| `uq_sync_active_owner(portfolio_id,broker,account_id,sync_source) WHERE status = 'running' AND portfolio_id IS NOT NULL`，UNIQUE | 跨数据库连接唯一活动账户认领；旧 NULL Portfolio 保留但不参与新执行。多个历史 running 同 scope 阻断计划，人工核实后才可继续 |

定义：[app/u0_schema.py](../app/u0_schema.py)、[models.py](../backend/services/broker_sync/models.py)。前提是完整 B0 Schema/两项原订单唯一索引；不删除或改写 B0 receipt。共享 runtime 只 verify，不 DDL、不创建默认 Portfolio、不放宽精确 sqlite_master digest。非共享开发库沿用显式 init_db 路径升级。后端、MCP、restricted 和脚本应使用同一候选版本与更新契约；不混跑旧 writer。

## 4. 审计缺口与安全回归

联合审计 8 项缺陷探针中，本批将 **P03/P04/P05/P06/P07/P09 六项**转为安全断言。P01 跨分析会话固定输入和 P08 财富 payload 属 U1，刻意保留；P02 是既有只读快照可复用证明。按发现编号：G01/G02/G03/G05/G06 本批处理；G04/G07→U1，G08→U2，G09 公司映射另批，G10 QMT 接入另批。

正式入口：[U0 安全测试](../tests/test_u0_sync_safety.py)、[U0 迁移演练](../tests/test_u0_schema_migration.py)。跨连接 WAL/Barrier 认领、旧租约晚到、空/None/partial/offline/mixed/duplicate 保留 last-good、外部 I/O 中第二 writer 可写、跨 Portfolio/账户、原币零/负/BASE、多币种/未知 FX、TTL/实际日期、现金单次读取及 Web/MCP 同额、过滤查询、分区保留、manual all、Guojin 预绑定和 scoped backfill 均有隔离断言。

迁移演练从真实 B0 结构的合成库开始，核对精确 DDL、唯一索引冲突/重复阻断、inode、Backup API、B0 receipt、幂等空计划、DDL 提交后契约中断恢复及新事实后拒绝旧备份恢复。原 B0 订单故障注入与交易确认回归仍运行。

所有本地测试禁 dotenv、Mock/临时库，真实交易关闭；浏览器只用合成 loopback 服务，不启动完整 backend lifespan。最终测试数量/完整 SHA/CI 链接以 PR 和交付证据包记录，不能替代 Mini 的只读账户验收。

## 5. 未来生产迁移 Runbook（本轮未执行）

获得独立生产授权后：

1. 固定通过四项 CI 的候选 SHA，确认 B0 实际 Schema、owner、原 receipt、组件版本/UID/路径/inode/WAL、native SDK。保留现行交易授权与开关，不以 U0 测试配置覆盖生产。
2. 停全部 writer（backend scheduler/poller/Web/restricted/脚本），协调 MCP 读连接；不可只停手动同步。私下核对 legacy running 和重复 scope；来源不明则停止，不自动清记录、合并数据或签新 digest。
3. 在 0700/0600 私有路径进行 Backup API、克隆演练和计划审查。以下变量由操作员显式提供，不把实际账户、库、凭证、备份写入 Git。

```bash
PYTHON_DOTENV_DISABLED=1 "$PYTHON" scripts/migrate_b0_safety.py plan --stage u0 \
  --database "$DATABASE" --contract "$CONTRACT" --plan "$PLAN"
PYTHON_DOTENV_DISABLED=1 "$PYTHON" scripts/migrate_b0_safety.py apply --stage u0 \
  --database "$DATABASE" --contract "$CONTRACT" --plan "$PLAN" \
  --backup "$BEFORE_BACKUP" --receipt "$RECEIPT" --writers-stopped
PYTHON_DOTENV_DISABLED=1 "$PYTHON" scripts/migrate_b0_safety.py verify --stage u0 \
  --database "$DATABASE" --contract "$CONTRACT"
```

4. 工具原地应用并保留 inode，验证数据 inventory、原订单索引与 integrity，原子更新本实例精确 digest 和独立 `u0_migration_receipt`。B0 receipt、历史 reference 均保留。DDL 已提交但契约中断时，保持停写，仅在 receipt/目标 inventory 完全匹配且未启 writer 下运行 `resume-contract --stage u0`，不得手签任意 hash。
5. **任何 writer 启动前**执行 `mark-writers-enabled --stage u0 --database ... --contract ... --receipt ...`。只启动匹配版本；该标记之后即保守禁止数据恢复，哪怕 writer 启动失败。
6. 独立授权的只读券商同步会写本地生产库。先执行一次 Tiger/IBKR 对应 owner 的原同步，核对原币余额、持仓、BASE 排除及完整性；失败必须可见并保留 last-good。在确认 Tiger CASH 已持久化前，不向用户宣称原总资产已完成新口径切换。旧虚拟现金不再追加，人工/截图现金若无唯一映射证据先停下核实，不自动删除。
7. Web/MCP 抽样同额，财富/养老金/负债原去重不变；失败/断网/空结果/FX 过期应显式降级，手动和原 22:00 实际成功另记录。Futu/Guojin 未确认 owner 的来源保持原隔离；不因代码存在而启用。
8. 获取新的库内现金 valuation reference v2；旧包含外部现金的 v1 只能保留历史证据，不能拿来重放比较。新 reference 仍为迁移/对账证据，不是实时估值或 U1 snapshot_id。

## 6. 回滚与后续复用

- 从未重启 writer 且目标 inventory 完全未变：独立恢复授权后用原 `restore-prewrite --stage u0 --database ... --contract ... --receipt ... --backup ... --writers-stopped`；先保存迁移态，再 Backup API 原地还原与旧契约。需保留匹配旧结构的 B0 代码；不得仅回退 MCP 或单个 writer。
- writer 标记已启用、产生新事实或状态不明：禁止旧备份覆盖、反向 DROP 或任意 digest 修改。停 mutation、保留全部订单/资产/审计事实，优先前滚修复或经核实的只读降级。旧 B0 writer 没有 U0 fence/原币现金合同，不作为无条件可写回滚。
- U1 复用本来源合同、run/last-good 和执行器，另做受控 prepare、任务固定输入和 WealthSnapshot payload；当前没有新 MCP 工具或跨工具 snapshot_id。
- U2 复用同 owner 的 claim/publication；仍需订单事务内 reserve、水位/覆盖证明、持久重试与崩溃补扫。不能在原订单观察事务直接调用本批会 commit 的 synchronize，也不能把任意 run success 当成交已覆盖。

本轮不合并、不迁移、不部署、不接 QMT、不启动 U1/U2。


## 7. PR #22 定向修复及现金存量核对

- **R02 来源账户**：Tiger 在适配前逐条验证原 SDK Position.account，Adapter 再验证并保留实际源身份；不得从配置补造缺失账号。prime assets 的实际 account 也必须可证明且相同。错误/缺失身份整次失败，原 Last-good 和历史快照不变。
- **R01 现金碰撞**：原 Upsert 在同 Portfolio 内检查现金余额记录。只有同 owner/api/canonical symbol/currency 的唯一行可更新；未知归属、同 owner 导入现金、重复 API 或币种/身份矛盾均 `CASH_IDENTITY_COLLISION_REQUIRES_REVIEW`。已明确属于其他账户的独立现金不认领；货币基金不是原币现金余额。无 symbol/仅币种代码的旧货币记录保守作为待核实现金，不按平台/名称证明归属。自动历史接管明确排除现金，首次成功 run 也不能成为接管手工现金的依据。
- **I01 合法空仓**：真实 tigeropen 3.5.8 Segment 使用 gross_position_value。合法零证券加非零现金可正常发布；缺字段、NaN/Inf、非零 gross 或 owner 不明仍失败。不能用虚构的 market_value 测试属性替代实际 SDK 合同。

### 显式同笔现金映射

仅在人工核实同一笔现金后，复用原迁移工具 `--stage u0 --corrections "$CASH_CORRECTIONS"`。**本轮只在合成库演练，没有生产订正授权。** 每项请求必须含：

```json
{
  "kind": "cash_owner",
  "position_id": 123,
  "expected_fingerprint": "<通过 row_fingerprint 私下生成的整行 SHA256>",
  "broker": "tiger",
  "account_id": "<人工核实的实际账户，仅私有文件>",
  "currency": "CNY",
  "confirmed_same_cash": true,
  "evidence_ref": "<私有账户核对记录引用>"
}
```

这不是同步客户端参数或自动匹配规则。plan 要求原行确属本 Portfolio 的现金事实、币种一致、旧 owner 为空或已经同一 owner、完整指纹匹配、没有其他 canonical 目标/同账户现金碰撞；同一计划多个源映射同一目标也拒绝。克隆及真实事务再次验证；旧 owner 指向另一账户、缺证明或目标已存在时停止，不自动合并/删除。需要处置已有重复数据时另作人工核对与明确订正授权，不能利用此映射覆盖现有 API 行。

apply 仅更新指定原行的 broker/account/source 与 canonical symbol/ticker，保留 ID、金额、数量、成本、名称及其他事实；原行在后续同步中更新，不再插入第二条。证明及整行 CAS 留在不可变 plan/receipt；不增加 Schema 或现金账本。第一次真实同步的金额变动应按旧手工记录、来源现金、FX 分别核对，不将新增 CASH 行解释为财富增加。独立现金维持原真实归属；归属仍未知则保持阻断，不猜测配置账户。

验证入口：[三项评审回归](../tests/test_u0_review_regressions.py)、[显式映射/CAS/碰撞演练](../tests/test_u0_schema_migration.py)。使用实际 SDK Position/PortfolioAccount/Segment 类型，覆盖账户错误/混合/缺失、首次/二次/重试现金碰撞、独立现金、合法空仓及异常 Last-good。浏览器/受限端原合成 000001 持仓修正为 COMMON_STOCK；不再把普通证券伪造为 CASH 来绕过现金合同。


## 8. CNH 汇率兼容修复与定向发布

原 Frankfurter v1 不支持 CNH，正反向都返回 404；原 USD 交叉链的 CNH 腿同样缺失。沿用原 FrankfurterProvider / FXService，仅涉及 CNH 的报价使用 [Frankfurter v2 具名来源接口](https://frankfurter.dev/providers/bi/)；[Bank Indonesia 原始页面](https://www.bi.go.id/en/statistik/informasi-kurs/transaksi-bi/default.aspx) 分别公布 CNH、CNY。请求 `providers/bi/rates?base=USD&quotes=CNH,CNY`，用同机构、同实际日期的两腿计算 CNY-per-CNH；反向取相反比例，USD 腿按 USD/USD 定义。未对全部币种迁移 Provider，也不依赖未归因的 blended 汇率或无证据的 CNH=CNY。

v2 必须返回所需每条且唯一的 USD base/quote、有限正数及相同有效实际日期；缺腿、混日、未来日期、错误身份、网络错误均进入原不可用/Last-good 分支，不再以另一组 v1/v2 混合腿绕过失败。来源写入原 FXQuote.source / run.context_json；latest 300 秒、降级 15 秒、实际日期超过 4 天 stale、旧日期 Last-good 不变。过期值按原 U0 合同仅能显式 partial，不能宣称实时。回归入口 [test_cnh_fx](../tests/test_cnh_fx.py) 覆盖来源、TTL、异常、反向、历史、USD/HKD 等原路径及 IBKR 原币现金重复同步/失败保留。

无 Schema/索引/数据订正变更，无新服务、MCP 工具或现金模型。合并与 main 四项 CI 通过后，由原 Mac mini Codex 固定 SHA 按正式流程发布；不重复 U0 迁移或 Tiger 验收。切换前保存一致性备份及原组件启动入口，核实共享 digest/owner 不变，保留实际实盘开关。仅显式触发原 IBKR 只读账户同步；来源请求失败或数据异常时停止该同步，保持 Last-good，不人工填汇率。核对 CNH 原币数量、实际汇率日期/质量、BASE 排除和 Web/MCP 同额，分解资产变动为新增/更新原币现金、证券变化及 FX 变化；无未核实平衡项。

回滚优先在不改变 Schema 的情况下恢复已核实兼容的上一 U0 组件入口，保留最新数据库事实。启 writer 后不恢复旧整库或重跑迁移；只读来源同步仍会写本地数据库。本修复不授权测试下单、变更交易开关或启动 U1/U2。正式运行结果另按固定 SHA 的发布证据报告，不以本节候选设计冒充已验收。
