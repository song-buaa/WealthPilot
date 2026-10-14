# Web & Data Sync · Batch 2：受限 Web 与手动同步

2026-10-14；基线 `dff749d3fddca756b49a386fb5e8d6c52ac98bfe`，短期分支 `codex/web-sync-batch2`。本轮全部操作在 MacBook，使用合成临时数据库及 Mock 来源；没有连接 Mini、读取真实库/账户、调用生产 MCP、修改生产权限或启动交易。设备真实验收与代码发布分开。

## 1. 实现与明确边界

- `backend/restricted_web.py` 复用原 FastAPI handler 与 React 构建物，只注册下表的方法和路径；不 import `backend.main`，不执行 init_db/startup、默认 Portfolio 创建、孤儿订单扫描、OrderPoller、计划评估、PEER 或 scheduler。
- Web 固定 `127.0.0.1:8790`，单 worker；loopback Host、同源 Origin 检查及 cross-site 拒绝，不信任代理头，不配置 CORS 或公网监听。访问边界是现有可信 SSH/本机用户，不是新增的多用户登录体系。MCP/Tunnel 的 8788/8789 和两个 label/凭证不变。
- 继续使用原 Dashboard、WealthOverview、BrokerSyncTab、图表和计算服务。生产 HTML 在普通 bundle 加载前设置 restricted 标志；未开放页面给出“此功能尚未开放”，侧栏标注暂不可用；导入/服务端导出/编辑/删除不可操作。财富页原有客户端 CSV 导出不调用新增 API。Dev 页面能力不变。
- 数据库完全使用 Batch 1 的 shared WAL：明确配置路径/Portfolio/receipt，受控 writer + NullPool + busy_timeout，MCP 每请求只读事务；不存在、身份/schema/权限不符即拒绝。历史 migration-reference 不重写，查询日期不冒充来源更新时间。
- 此模式只有一个非 root Web 进程：启动时持有文件锁，进程内每 broker 锁覆盖排队与执行。Web 和 MCP 必须分进程，不能在 Web 线程内调用会临时替换 session binding 的 PoC reader。

| Method | 实际生产 API | 用途与副作用 |
|---|---|---|
| GET | `/api/demo/status` | 返回真实模式/受限模式，兼容已有密码门；不伪装成 Demo。 |
| GET | `/api/web/status` | 版本、shared_wal、启用列表、scheduler=false；不输出金额/账号/凭证。 |
| GET | `/api/portfolio/summary`, `/positions`, `/liabilities`, `/alerts`（后三项同 `/api/portfolio` 前缀） | 原投资账户查询 handler。 |
| GET | `/api/wealth/summary`, `/api/wealth/items` | 原财富查询；summary 保留原 aggregate snapshot 提交行为。 |
| GET | `/api/allocation/targets` | Dashboard 现金目标区间所需的原只读配置，不注册 allocation 的计算/修改路由。 |
| GET | `/api/broker-sync/status` | 原最近尝试/最近成功信息，加 enabled、running、last_attempt_id；错误只返回固定脱敏码。 |
| POST | `/api/broker-sync/trigger` | 202 仅代表预约，返回 job/run ID；完成以 status 的对应 ID 和 success/failed 判断。 |

另有 `/` 和 `/assets` 静态托管；无 OpenAPI/docs、未知 API 的 SPA 回退或其他业务方法。受限 UI 是提示层，真正的拒绝由服务器路由和方法执行。

## 2. 手动同步复用与修补

调用链：`BrokerSyncTab → trigger → SyncExecutor.reserve/execute → 原 broker_sync._run_sync（注入同一 session/factory）→ 原 sync_and_persist → Repository/PositionUpsertService`。未来 cron 使用同一个 reserve/execute，不能改回完整 backend.main 的旧 scheduler 入口。本批没有创建 22:00 job。

默认 `enabled_brokers=[]`，手动/all/未来 cron 共用该列表。仅支持本阶段具备接线的 tiger、snowball（原 IBKR 存储键）、guojin；Futu 默认不开放。真实 Gateway 未确认前不启用；Mock 只存在测试注入点，不在生产配置提供“模拟同步写真实库”的选项。

最小修补：

1. 预约先在原 Run 表留下 running，服务复用此 run ID，构造/凭证/连接失败也可记录 failed；同 broker 手动/cron 重复预约返回 already_running。UI 用 run ID，避免一秒内多次尝试的时间字符串相同；约30秒等待超时仍提示运行中，5秒状态轮询继续，未完成不释放锁或宣称成功。
2. 既有 sync_and_persist 原样计算/转换；会话显式绑定实际 Portfolio，upsert/legacy claim/stale 查询受其范围约束，不再把受限同步的新行写死到 Portfolio 1。restricted 路径不调用全库裸名称 backfill。
3. 国金 snapshot、业务 upsert、成功标记合并到同一次最终提交；中途失败整体回滚，last-good 保留。其 restricted stale 清理仅允许相同 Portfolio/broker/account/source 的 QMT 管辖行，保留其他来源/账户/组合和手工行。
4. 实际来源 factory 拒绝空结果或混合 broker/account 结果，需人工核实真实清仓而非自动清空；在写快照前复用原 FX 服务预热本次币种，不在批量 upsert 中首次发 FX 请求。不重写金融统计。
5. IBKR 只注入既有 adapter 的只读 positions/account_info 方法，不通过 action router 取得连接；同 Web 进程缓存连接，退出调用原 shutdown，不调用订单方法。Tiger/QMT 仍用原服务。无真实连接在本轮发生。
6. 启动后仅把本入口 `restricted:*` 的遗留 running 标为失败中断；不重放、不补跑、不改最后成功值，不碰其他入口任务。数据错误输出和生命周期日志不带 SDK 原始消息、财务 payload 或凭证。

SQLite 按短事务序列化写入；遇 busy/连接/未知错误显式失败，不建设队列或无限重试系统。不能把202、健康接口200或历史成功当本次同步成功。

## 3. 发布和独立部署资产

`install-github` 保留 main ancestor、exact SHA 四项 CI、固定 hash/binary-only ARM 安装。新增独立 `envs/web`（business 既有 pins + FastAPI/Starlette/Uvicorn/multipart 和 IBKR 最小客户端依赖），MCP/business 锁不变。额外9个 wheel 为 py3-none-any，已按 ARM target 下载并记录 SHA；这只证明资产可用，不代表 Mini 原生执行通过。

`web_deploy.py build` 要求设备原生 ARM Node22，复用 npm ci/package-lock 和普通 npm run build，将产物复制到该 release 的 `web-dist`，生成绑定 revision/逐文件 hash 的 `web-assets.json`；不提交 dist/node_modules，不改业务代码。`web_runner.py` 启动时核对代码、安装环境和静态摘要，再以净化环境启动单 worker。临时启动故障记录在有限大小 `logs/web.lifecycle.jsonl`；原始子进程输出由 launchd 丢弃。

独立 job：`com.wealthpilot.restricted-web`；模板见 `pocs/poc03_mac_mini/launchd/`。它指向指定 release 的 Web Python/runner，不改 MCP/Tunnel `current` 或 label。`config/web.json` 保存独立 revision 与 allowlist；`config/web-brokers.json` 为可选0600来源配置，仅允许 IBKR_HOST/PORT/CLIENT_ID/ACCOUNT、GUOJIN_GATEWAY_URL/SECRET。Tiger 现金/凭证复用既有 private wealth.json，保证与 MCP 来源配置一致；不复制整份 .env，不继承 Tunnel/旧 PoC Key 或 OpenAI Key。

## 4. Mini 一体化执行顺序（仅交接，本轮不执行）

由 Mini Codex 先读取本发布 SHA 的 AGENTS/治理/本文件与 Batch 1 文档，核实当前真实路径、owner、运行 SHA、进程和数据库模式。下面占位符由设备端核实；所有命令只在后续设备阶段授权后运行。现役 real MCP 不得因阅读交接而停止。

1. **保留当前证据**：记录当前 MCP/Tunnel SHA、模式、两个 label；使用原 snapshot Backup API 导出并 verify 一致性备份。运行配置/契约另存私有备份，不在聊天/Git 输出真实金额、账号、Key。
2. **安装候选，不激活**：原 `deploy.py install-github --revision <完整发布SHA> --python <native-arm64-python3.11> ...`。安装三个环境但不切 current、不启服务。核对 web 环境 pip check；若 GitHub匿名限流则记录 BLOCKED，不能绕过 CI。确认原生 ARM Node22，执行：

   ```bash
   "$NEW_RELEASE/envs/business/bin/python" "$NEW_CODE/pocs/poc03_mac_mini/web_deploy.py" \
     --root "$PROD_ROOT" --expected-hostname "$MINI_HOSTNAME" build --revision "$RELEASE_SHA"
   "$NEW_RELEASE/envs/business/bin/python" "$NEW_CODE/pocs/poc03_mac_mini/web_deploy.py" \
     --root "$PROD_ROOT" --expected-hostname "$MINI_HOSTNAME" configure --revision "$RELEASE_SHA"
   ```

   configure 默认列表为空、拒绝覆盖已有 Web 配置。后续更新用 select，不重置原 MCP/Tunnel 配置或凭证；构建/配置均不启动 Web。
3. **共享模式分支**：若已是经验证的 Batch 1 shared_wal，核对同一库/Portfolio/receipt，无需再次迁移/改权限；MCP须已使用包含本批侧文件并发修复的兼容release；若仍是旧Batch 1检查，先经批准切到本发布SHA，再启Web。若仍是0400固定副本，先由设备操作者批准短暂停服窗口，严格按 Batch 1 第3节：先切到支持共享契约的新 MCP release并验证固定模式，再停两个 jobs，确认全部客户端已关闭，执行 shared_database 的计划→`--services-stopped --apply`，保留精确原库和历史参考，再启动 MCP/Tunnel并用 `validate --expect real --runtime-data` 核实。**不能只 chmod，也不能在旧 runner 仍使用迁移 SHA 时先写库。** 本轮 Dev 不执行此窗口。
4. **只启 Web、broker仍为空**：原有 MCP 健康已确认后，`web_deploy ... services install` 先生成 plist 计划；检查独立 label/UserName/固定 release 路径，再由设备操作者以系统 launchd 所需权限加 `--apply`。这一步只处理 Web job，不停止现有两个 jobs。
5. **健康与私网浏览**：确认只监听127.0.0.1:8790，GET `/api/web/status` 显示正确 SHA/shared_wal/empty allowlist/scheduler=false。从 MacBook 使用已信任的 SSH 别名：`ssh -N -L 127.0.0.1:8790:127.0.0.1:8790 <trusted-mini-alias>`，浏览 localhost:8790；不关闭主机密钥校验，不开公网端口。确认财富/组合可见、未开放功能明确提示、同步按钮未启用；同一时点/既有来源口径下与 MCP 比较三金额，报告只记录布尔值。来源/现金/FX变化产生差异不能自动PASS；partial/unknown/stale保留。
6. **真实 broker 单独验收后才启用**：查实 Gateway、账户只读护栏、唯一clientId、QMT登录/私网/secret、实际Portfolio。必要参数由操作者保存在0600 web-brokers.json；不得迁移整份 .env。等任务结束，仅停止 Web job，执行 `web_deploy ... brokers --enabled-brokers <已验收列表> --gateways-verified --services-stopped` 的计划，确认后加 `--apply`，再只重启 Web。此确认参数仅记录操作者验收，不是脚本自动网关证明；未经真实验收不得勾选。初次 Dev Mock PASS 不能填入设备 PASS。
7. **设备实测收据**：源成功/失败/重复请求、Web→MCP新值、Portfolio范围、重启恢复、WAL备份/恢复、无交易任务/路由/订单方法、监听和SSH范围分别记录；真实资产摘要/账户信息不回传聊天。22:00 scheduler 必须仍为 false。

## 5. 失败回滚

- **只撤 Web**：默认安全退路是 `web_deploy ... services stop --apply`，保留备份/配置/现有数据、MCP/Tunnel与共享契约；不会把数据库倒回迁移时点。
- **Web代码回退**：等任务完成、停 Web，以非 root 运行 `web_deploy ... select --revision <已安装的Batch2兼容SHA> --services-stopped` 审核计划，确认后加 `--apply`。工具归档原 web.json；再 `services restart --apply` 重写本 job 的固定路径并启动，健康验证。start 拒绝继续使用与新 revision 不符的旧 plist。早于 Batch 2 的 release 没有 Web 环境/产物，不能当 Web 回滚目标；首次发布失败就只停 Web。
- **业务数据恢复**：先保留最新 Backup API 备份并 verify。需要恢复数据时，必须另获全部客户端停服窗口，使用 Batch 1 的 verify→restore→显式receipt重绑定；不 cp 活库、不 unlink WAL/SHM、不覆盖 migration-reference。先验 MCP，再启 Web。回到旧固定模式还必须恢复配套0400精确副本/配置，不能只回滚代码。
- 备份/restore/权限变化与 launchd 都不在本轮实际执行；安装失败、schema缺失、权限/身份错误或静态摘要错误应记录 BLOCKED/FAIL，不调用 init_db 或改业务代码“修好生产”。

## 6. Dev 验收与 Batch 3 遗留

自动化：`test_restricted_web.py` 的真实 handler / 原 Guojin sync_and_persist + 合成输入；独立 MCP HTTP 请求验证 Web提交后读取新值；原 Batch 1 读写/并发/异常退出/备份恢复回归；Playwright 在真正受限生产托管下覆盖成功同步、失败保留last-good、只开放页面和按钮。另建最小 Web Python环境，pip check及同一浏览器流程通过。所有来源 Mock、网络只用 loopback，日志在仓库外保存。

本地最终结果：受限 Web **39 passed**（含独立 MCP HTTP）；原同步/归属/持久化 **56 passed / 2 integration deselected**；后端全量 **896 passed / 7 skipped**；Batch 1共享库 **34 passed**；既有 reader/deploy/GitHub guards **73 passed**；两套MCP HTTP **11 + 9 passed**；Offline M5 **18/18**、公网尝试0；前端 lint/build 与两个真实浏览器用例 PASS。ARM额外9个wheel为纯Python资产可用性证据，最小Web环境为Dev x86_64，不是Mini执行证明。现有未改package-lock的npm advisories与bundle大小提示保留，未自动升级依赖。

首次Linux CI在并发刷新时发现503，本地90次并发请求复现 FileNotFoundError：SQLite正常关闭会清理临时WAL/SHM/journal，侧文件可在存在检查与stat之间消失。共享契约仅允许这一临时缺失窗口；主库/契约缺失、公开权限、dangling symlink仍拒绝。新增确定性清理竞态/不安全侧文件及并发Web回归，未放宽浏览器“零失败API”断言。失败CI日志保留在仓库外交付记录。

实际 Mini ARM、真实 broker/现金/FX、SSH访问与设备恢复 **NOT RUN**。发布前的最终计数和 GitHub SHA/PR/CI 见本轮交付收据；不能把代码/Dev PASS 当设备生产验收。

Batch 3 必要依赖/未决项：

- 本批没有22:00调度。后续只在同一 Web 单进程创建一个 job，CronTrigger 显式 Asia/Shanghai，复用同一 enabled list和 reserve/execute；错过任务/超时/停机先明确规则，不能默认补跑历史。
- IB Gateway 的 Mini原生安装、登录/重登录、API只读、clientId与断线恢复；Windows QMT 现役脚本/xtquant/Python、Parallels/宿主独立性和 Mini→VM 可达尚待设备验证。health=200不等于券商登录，默认Futu不开放。
- 原 FXService 的 latest 缓存没有TTL；本批保留原业务口径并在写前预热，不声称长期缓存就是实时估值。启用日同步或验收长期非CNY来源前需冻结缓存更新/数据日期规则；MCP短worker和Web长进程的实际现金/FX对账必须实测。不得用queried_at充当估值日期或覆盖历史参考。
- SQLite单机WAL、短事务、5秒busy边界已复用；不同broker/API争用与真实SDK超时还须设备验证。暂停/超时UI不杀线程，实际中断后下一次启动才标failed，不假定SDK任务已结束。
- 远程Web先使用可信SSH；iOS浏览器私网如有需求另验，ChatGPT iOS MCP可用不表示浏览器能访问Web。

PoC02原凭证展示事故保留历史 FAIL，撤销后的权限检查另记；本批不改写、不重新处理凭证事故，不开展其他架构重构。
