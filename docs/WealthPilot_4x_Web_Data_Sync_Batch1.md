# Web & Data Sync · Batch 1：共享数据库契约

2026-10-14。开发基线 `62fffb48a9db8b12dfc6b241ca2aee98e0280af5`，分支 `codex/web-sync-batch1`。本轮仅 MacBook Dev、合成临时库；没有连接 Mini、调用生产 MCP、访问或迁移真实库。用户确认 Mini real MCP / Web / iOS 已运行，本轮不复验设备状态，也不宣称 Web/同步已迁入生产。

勘探输入为仓库外 `WealthPilot_4x_Web_Data_Sync_Exploration.md`（2026-10-14）；下文把其中 Batch 1 候选收口为代码契约，Batch 2/3 仅定义边界。

## 1. 改动与不变项

| 入口 | 实现及影响 |
|---|---|
| `app/shared_sqlite.py` | stdlib 私有契约：固定 canonical 路径、设备/inode、schema SHA、Portfolio；检查 owner、0600 DB、0700 父目录及私有 WAL/SHM。按快照读 schema/Portfolio，启动另做 integrity_check。文件内容 SHA 不作活库不变式。 |
| `app/database.py` | 只有设置 `WEALTHPILOT_SQLITE_CONTRACT` 时采用 `mode=rw`、NullPool、5 秒 busy_timeout、synchronous=FULL；已有文件不存在即失败，不自动建库。不设置时保留原 Dev 行为。 |
| PoC02 reader / worker | 配置增加可选 `database_contract_path`，每次请求在同一只读事务内检查共享契约；仍为 `mode=ro`、query_only、authorizer，禁止写 SQL、DDL、ATTACH 和 mutating PRAGMA。不使用 immutable，不调用会写快照的 get_summary。 |
| PoC03 runner | 默认 `fixed_snapshot` 沿用迁移 SHA；仅显式 `shared_wal` 改用活库身份/schema/Portfolio/完整性，并核对历史迁移来源与现金配置。原 Git、hostname、独立生产凭证、私有范围等 Gate 不变。 |
| PoC03 `shared_database.py` | 默认只读计划；显式停服确认 + `--apply` 才备份、0400→0600、启用 WAL、写私有 receipt 和两个原配置中的新增字段。不启服务、不修改业务行、不改历史参考、不改 Key。 |
| PoC03 validate | 新增 `--runtime-data`，只验实际 MCP 的契约和质量状态，不将运行金额与历史迁移金额比较；与 `--reference` 互斥。PASS 不表示 freshness 完整、金额已对账或设备恢复已验收。 |

原 Portfolio / Wealth 计算、养老金调整、现金/FX、MCP schema `poc02.wealth-overview.v1` 均保留。`partial`、日期未知/过期、最近同步失败、现金失败 unavailable 保持；queried_at 不充当数据更新时间。

这不是新权限平台：Web、同步和 MCP 运行于同一非 root 用户，0600 允许该用户写库。MCP 的防写由现有 reader 强制执行；不能声称防御该用户恶意代码或绕过 reader 的 sqlite3 访问。inode 检查用于阻止无意换库；合法 restore 需明确重绑定，不能把 receipt 当密码学数据来源证明。业务更新的授权靠后续受限路由/启用列表，现阶段不开放任何新写入网络入口。

## 2. 活库、WAL 与历史参考

- 同一台 Mini 本地磁盘上的唯一业务权威库；不把 SQLite 放网络共享/iCloud。一个受限 Web worker 持有调度器，手动同步和 cron 共用业务入口。SQLite 协调短写事务，超时明确失败；不在写事务内等待券商网络，不无条件重试 mutation。
- `data/migration-reference.json` 与原导入 manifest 永久作为历史验收材料。运行金额可变，不覆盖旧参考、不要求未来金额仍相等。WAL 中已提交更新可能尚未进入主文件，禁止用 `.db` SHA 或直接复制活库判断最新数据。
- MCP 每次请求新建只读连接/事务；同一次查询稳定，下次查询看到新提交。并发现金/FX 是既有外部读取，不属于 SQLite 原子快照；未新增“所有来源同一时点”的承诺。只读查询可能让 SQLite 维护共享内存侧文件，不等于业务写入。
- 使用原 `snapshot.py backup → verify`，Backup API 把已提交 WAL 纳入独立 DELETE-journal 备份，integrity_check + 所有表摘要验证。不要删除 WAL/SHM“解锁”。需要 checkpoint/切 journal 模式时先停止所有读写客户端，检查 busy=0。
- schema 改动不是普通数据更新：本批不自动 init_db、建默认 Portfolio 或补列。未来 schema migration 必须先在副本验收，再经停服/备份流程更新契约；不自动重绑定未知 schema。

## 3. 生产切换计划（本轮不执行）

前提：四项 CI 通过且经 PR 发布 main；Mini 操作者明确批准短暂停止 MCP/Tunnel。生产路径和 hostname 必须由设备端核实。以下占位符由 Mini Codex替换，不要整段盲执行；脚本本身不替操作者验证所有客户端已停止。

1. 原 `deploy.py install-github --revision <完整SHA>` 安装新 release；不触碰 data/config/credentials/current。依赖未新增，仍使用原 ARM 锁文件。
2. 用原 switch 流程切到新 release 的 real 模式，此时未设置 database_mode，仍验证固定迁移 SHA。先确认本地 MCP 可用，再 `services stop --apply`；确认两个 jobs 和所有数据库写入进程均已退出。Tunnel 配置、生产 Key、Tiger 路径原样保留。
3. 使用该 release 的业务 Python，以生产非 root 用户运行下列计划，先不带 --apply：

   ```bash
   "$BUSINESS_PYTHON" "$CODE/pocs/poc03_mac_mini/shared_database.py" \
     --root "$PROD_ROOT" --expected-hostname "$MINI_HOSTNAME"
   ```

   初次切换要求原库与历史迁移 SHA 相符、没有 WAL/SHM/journal、private access Gate 有效。若库已改变，停止并核实来源，不重写历史 SHA 以绕过。
4. 审核计划后加 `--services-stopped --apply`。工具先保存 `backups/pre-shared-<id>/`：一致性备份及 manifest、原 runtime/wealth JSON、精确原库 `fixed-snapshot.db`（0400）及 manifest；然后把运行库置 0600、启 WAL、绑定 inode/schema/Portfolio，写 `config/database-contract.json`（0600）。两份原 JSON 只增加 `database_mode=shared_wal` 和 `database_contract_path`。0700 数据目录与现有 umask=077 保留。工具失败后不重启，按归档配置/原库恢复并重新核对；不视为原子多文件切换。
5. 原 services start；本地健康检查：

   ```bash
   "$MCP_PYTHON" "$CODE/pocs/poc03_mac_mini/validate.py" \
     --expect real --runtime-data --output "$PROD_ROOT/evidence/shared-runtime-smoke.json"
   ```

   核对 real 标志与 partial/issues；Web/iOS、退出/设备重启仍由 Mini 实测。Batch 1 不启动 Web/同步，因此不在生产制造测试写入。Batch 2 的独立 Web 进程必须显式设置同一 `WEALTHPILOT_DB_PATH`、`WEALTHPILOT_SQLITE_CONTRACT`，再复用原 service，不启动 backend.main。

## 4. 回滚与恢复（发布前必须审阅）

**代码回滚与数据回滚分开**。旧 release 不理解共享模式，不能只切 current；即使 WAL 尚未 checkpoint、主 SHA 暂未变化，也不能依赖旧 Gate 碰巧放行。

- 回到旧固定副本：停止 Web/cron/MCP/Tunnel 及所有客户端，先用 Backup API 另存当前最新库并 verify（保留新增业务数据）；在所有连接关闭后通过 SQLite checkpoint（busy=0）及 journal_mode=DELETE 收口侧文件，禁止手动 unlink。用原 snapshot restore 将初次 `pre-shared-<id>/fixed-snapshot.db` 和对应 manifest 恢复为运行库，恢复到 0400。恢复该目录原 wealth/runtime 配置并保留 owner/0600，原共享 receipt 移到私有归档，最后用原 switch 流程回到旧 SHA并重新核对固定参考。**这会回到迁移时点，不能丢弃刚保存的运行期备份或声称仍有最新业务数据。**
- 保留最新数据恢复：停止全部客户端并收口 WAL，使用某份已 verify 的运行期备份 `snapshot.py restore`；0400、inode 变化会让共享启动检查拒绝，这是预期。再执行新工具的只读计划并加 `--restore-manifest <该备份manifest>`，审核后 `--services-stopped --apply`。它核对恢复库精确 SHA/所有表数据、原 scope/schema/Portfolio，保留历史迁移来源，只重绑定新文件并恢复 WAL/0600。重启、验证 runtime contract，并用同一时点、同一现金/FX 依据核对恢复前后金额；不用旧迁移金额冒充运行期参考。
- 若未来回退 schema 或升级 SQLite，先在副本验证。本批不自动删除数据、sidecar、旧 release、备份或凭证，不执行生产回滚。

## 5. Batch 2/3 的最小接口与运行边界（仅设计）

| 批次 | 明确边界与后续验收 |
|---|---|
| Batch 2 受限 Web | 新薄 FastAPI app + 单 worker，复用 React dist，同源 loopback API，独立 label/端口。白名单候选固定为 GET `/api/demo/status`（真实模式兼容响应）、GET `/api/portfolio/{summary,positions,liabilities,alerts}`、GET `/api/wealth/{summary,items}`，以及 Batch 3 的 GET `/api/broker-sync/status`、POST `/api/broker-sync/trigger`。按 method+path 注册实际 handler，不整包 include 含导入/删除功能的 portfolio router；未列出的 API 默认拒绝。财富 summary 仅允许原 aggregate snapshot 的既有副作用，手工资产编辑/导入/删除、消费、PEER、action、execution、订单及策略路由均不在本轮白名单。 |
| Web 生命周期 | 不调用 backend.main lifespan/init_db/startup、不扫描孤儿订单、不启 OrderPoller、15分钟触发评估或真实交易。使用已核实 Portfolio，不默认写死1；Web/MCP 分进程，避免 reader 临时 session binding 与 Web 线程共享。首次调用 get_summary 的快照提交、读写并发和错误恢复必须实测。 |
| Batch 3 共用同步 | Web trigger 与唯一 22:00 job 共用现有 `backend.api.broker_sync._run_sync` → 各 `sync_and_persist`，只补必要统一 dispatch/配置/状态，不另造同步统计。CronTrigger 显式 `timezone=Asia/Shanghai`，单 scheduler、replace_existing；错过调度先人工补同步，不默认补跑历史。 |
| 启用清单 | 使用同一份外置 allowlist 控制 UI/trigger/all/cron；候选 tiger、snowball（现有 IBKR 存储键）、guojin，各自设备验收后显式加入。Futu 无已部署 OpenD 证据，默认禁用；未启用 broker 不创建网络任务。启用清单不等于交易授权。 |
| 失败/防重入 | 同 broker 手动/cron 共享进程锁，重复请求返回 already_running；锁在 finally 释放；不同 broker 短写事务由 SQLite 序列化。构造/连接/超时失败均留下原 Run 的 failed 状态，最近尝试与最后成功分开；保留 last-good 数据。成功须在业务 upsert 完成后记录；重启遗留 running 显式中断，不伪造 success。30秒 UI 轮询超时不取消任务；先核对现有服务能否安全超时，不能假定线程已停止后释放锁。 |
| IBKR | 原生 ARM IB Gateway、用户登录/认证、Read-Only API、唯一 clientId 和已核实 live/paper 端口；只读 adapter 不通过注册 action router 取得。增量锁定 ib_async/FastAPI/APScheduler 等必要依赖，ARM 安装/连接/断线需设备验证，不自动启订单链。 |
| 国金 | 现役 Windows VM + QMT 极简登录 + xtquant + 既有 HTTP 网关；需找回真实脚本和 Windows Python 依赖，不能用 PRD 当现役实现。Mini→网关私网可达、`X-WP-Secret` 外置、GET positions 和 QMT 登录分别验收；health 200 不代表券商已登录。Parallels/VM 兼容性和独立于 MacBook 常驻仍待核实。 |
| 私网/发布 | 优先核实既有私网；未查清前用已有可信 SSH 的电脑端本地转发，不开公网无认证 API。iOS Web 私网另验，ChatGPT Tunnel 不等于浏览器可访问 Web。沿用 GitHub PR/四项 CI/main完整 SHA/install-github；后续只补 Web dist、最小 ARM 依赖及独立 launchd，不覆盖现有 MCP label/config/key/data，不自动激活。 |

这些是后续实现约束，不是已实现的路由、调度器或网关状态。本批没有修改 broker_sync、财富计算、交易、OrderPoller、PEER 或前端业务代码。

## 6. 验证记录

专项自动化使用真实 Wealth/Portfolio Service + 合成 SQLite，测试位于 `pocs/poc03_mac_mini/test_shared_database.py`，并接入既有 MCP PoC CI 独立 pytest 进程；HTTP 跨环境用 `MCP_TEST_PYTHON`，未提供时明确 skip，不能计作 PASS。

| 检查 | Dev 证据 / 状态 |
|---|---|
| 已提交变化可见、未提交/回滚不可见 | 实际 SQLAlchemy writer → 原 reader，PASS |
| 同次财富计算不混快照 | 在 freshness 查询与 _current_totals 之间提交更新，同次旧值、下次新值，PASS |
| 0600 库的 MCP 防写 | INSERT/UPDATE/DELETE/DDL/ATTACH/query_only/journal/checkpoint 均拒绝，PASS |
| 原 Web summary | 真实 get_summary 提交财富快照，MCP 三金额同口径且不另写快照，PASS |
| WAL/并发/异常退出 | 竞争写连接等待后均提交；子进程 commit 后异常退出，保留 commit、丢弃未提交，PASS |
| 重启与边界拒绝 | 合法写入改变 .db SHA 后 runner 可重启；换文件/公开权限/symlink/schema/Portfolio/journal/损坏均拒绝，原 access Gate 不放宽，PASS |
| 备份恢复 | 活 WAL Backup API → verify → restore → 显式重绑定，原参考不变、三项运行金额相等，PASS |
| 数据契约与状态 | 独立 worker、HTTP 工具发现/调用/错误恢复及提交后新值；unknown/stale/failed/unavailable，PASS |
| Mini / 真实 Web/iOS / 持续同步 | 本批未操作，NOT RUN，不能用 Dev PASS 替代生产验收 |

本地 Gate：Python 3.11.13 全量后端 **896 passed / 7 skipped**；原 reader/deploy/GitHub guards **73 passed**；共享库专项 **31 passed**（含独立 MCP 环境 HTTP 与运行期 validate）；PoC01 HTTP **11 passed**、PoC02 HTTP **9 passed**；Offline M5 **18/18**、公网尝试 **0**；compileall、Node 22.19.0 frontend lint/build PASS。既有 bundle 大小提示及 npm ci 对未改锁文件报告的依赖 advisories 保留，未执行自动 dependency fix。

测试使用临时 SQLite、显式安全环境和空外部凭证；本地完整日志在仓库外 `2026-10-14-web-sync-batch1/`，无真实库/金额/账户。GitHub exact HEAD 四项 CI 与 PR 状态在最终交付记录核实，Linux CI 不能替代 Mini ARM/真实数据验收。PoC 02 原凭证展示事故继续历史 FAIL；撤销后的权限检查单独记录，不因本批代码改变其历史结论。
