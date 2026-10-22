# U2 成交驱动同步与只读交易状态

> 2026-10-22 状态更新：本模块随4.1 **RELEASED — 已完成生产交付**；完整验收边界及非阻断观察项见[4.1结项记录](releases/v4.1.md)。下文保留原开发/隔离测试/待发布时点，不再作为当前部署状态；不据此推定真实数据已完整或全部自然场景已验证。

开发基线 `e06a6995a2f830aa527cb0a0c506b3153d11dc8d`，独立 `codex/u2-fill-sync`。U1 已合入；生产组件与真实 ChatGPT 是否启用本轮未核实。本文记录本批实现、验收及未来发布输入，不授权生产操作，不开展 B2/B3/QMT。

## 1. 最小 Gap 与权威复用

| 代码勘探结论 | 最小扩展 |
|---|---|
| 首次回包、轮询、Reconcile、取消都归并至原 `apply_broker_observation` | 同一短事务记录成交累计水位；不在此调用同步或提交另一交易 |
| OrderRecord 有累计成交，IBKR 有 fresh executions/execId/commission，但没有持久同步消费进度 | 原 OrderRecord 增加一列保存 pre-send anchor、execId 去重证据及覆盖/重试水位；不是新订单状态机 |
| SyncExecutor/Run 已有 scope claim、lease、fence、Last-good；原 poller 可恢复进程 | 复用原 reserve/execute，原 poller 在交易提交结束后 drain；不建任务表、队列或调度进程 |
| 同步完成时间不能证明成交已进持仓/现金 | 比较交易前成功发布的身份/原数量与账户累计成交净变化；现金另核实际成交额和已取得实际佣金。缺证据保守未证实 |
| real MCP 原七工具没有订单状态只读查询；原 refresh 会写订单 | 增加 `get_trade_and_sync_status`，仅读取原 Order/Run，既不 reconcile 也不联网 |
| U1 固定输入缺少待同步成交提示 | 同一冻结数据库中读取待覆盖水位，新增 issue；不改旧固定视图 |

## 2. 成交、幂等与恢复调用链

```text
原 Web 人工确认 / Batch
  -> OrderManager Tx A：原提交 claim + fill_sync_json 交易前基线 commit
  -> 原 BrokerAdapter 单次提交（无 SQLite 写事务）
首次回包 / OrderPoller / fresh recovery / cancel
  -> apply_broker_observation：原单调累计成交、策略/Leg/Batch + U2 进度，一起 commit
原 OrderPoller 下一轮（所有订单事务已关闭）
  -> 原 SyncExecutor.sync_fills
  -> 原 reserve：BEGIN IMMEDIATE 下重读账户全部待消费水位、绑定同一 Run 并 commit
  -> 原 execute / BrokerSyncService / FX：外部只读 I/O，不持有 SQLite 写事务
  -> 原 Snapshot + PositionUpsert + publication hash + 覆盖水位，同一 fence 事务 commit
```

- 事件身份为原 `order_id + cumulative_filled_quantity`，execId 仅加强真实成交/费用证据，不新增成交账本。重复/低水位/乱序仍由 B0 归并；不以 SUBMITTED、状态名称或 WhatIf 触发。
- 提交前基线来自最近 300 秒内完整成功 Run 的持仓投影，并要求 U1 publication hash 匹配当前事实。记录原快照数量/证券身份/原币现金，不另建持仓真值。并发/连续请求共享最早未完账户 anchor，保存 anchor 之前原订单的数量/累计实际金额/费用水位。
- 第一次正成交的 2 秒 debounce 后可消费，同账户所有待进度合并一次；认领锁内再次检查，另一个进程快速完成后不会因旧内存列表重复发起。记录下一次重试时间，失败退避 10–300 秒，原 poller 两轮间隔 5 秒，不自动重放订单；慢券商/同步 I/O 会延长实际轮次，沿原 SDK 的有界超时，不承诺 5 秒内完成。terminal 待覆盖订单按同一退避节奏对账，避免未知费用或无基线时持续高频查询。
- claim 提交后进程丢失：原 Run lease/fence 恢复；观察提交前崩溃：原稳定 orderRef/UNKNOWN 恢复后记录首次事实。terminal 订单若覆盖未完成仍允许原只读对账，以补齐 execution/实际费用。同步失败不改变成交事实。
- 只消费已有明确 Portfolio/账户配置的 Tiger 或 IBKR→snowball；不按订单自动启用 Futu/国金，不新建 IBKR 持仓通道。配置/归属问题留下待消费事实，原配置核实后重试。

## 3. 覆盖证明与安全边界

原发布事务核对同一 Owner 的原 Snapshot，不根据预计成交额写现金，不向 Portfolio 插入合成交易数量。

1. 比较 anchor 数量 + 原订单累计成交相对于 anchor 的 BUY/SELL 净变化，与实际新快照数量精确匹配。IBKR 优先按 conId+币种匹配，ticker/市场仍保留；矛盾/多个身份不通过。
2. 原币现金与 anchor 原币现金 - 实际成交累计金额变化 - **实际**佣金变化对账，允许 0.01 原币舍入误差。实际佣金要求 execId 去重后 shares 覆盖累计量、币种一致、数值有限；不能用 WhatIf 代替。费用未结算/现金滞后单独标记未证实，不把 USD/HKD/CNH/CNY 合并。
3. 还须可信的原始 `positions_as_of/cash_as_of` 在交易前基线与实际成交时间两侧形成水位；每项相关成交都须有真实时间。`fetched_at/finished_at` 不冒充券商时间。数量/现金和原始水位都证实才 `SYNCED`，保存覆盖 run/quantity/basis。没有交易前基线、net-zero 反向交易、缺费用/跨币种费用、execution 矛盾或未对账 UNKNOWN，均 `SYNC_UNVERIFIED`；已发布而证券数量尚未变化为 `SYNC_DELAYED`。取得更多证据后原恢复/同步会重试。
4. 比较只证明本应用已观察的成交在该账户快照中得到数量/现金变化佐证，不宣称完整券商历史窗口或所有外部手工交易覆盖。外部交易/出入金/未知成本口径导致无法匹配时保守未证实；不做 B2 历史归因。成本保持原券商同步值，覆盖计算不重建成本账本。
5. 已独立验证的累计成交保留原 `verified_coverage`；之后用同一 Run 派生的 validation anchor（官方数量/现金、原始来源时间、已有订单/execId 水位）增量核验当前发布。后续已验证卖出不能把原买入按全历史净零重新判为未验证；新目标仍必须独立满足原严格证明，checkpoint 不能给未验证订单签认。真实数量/现金/身份回退、原始时钟倒退或成交证据矛盾仍撤销当前 SYNCED 资格，保留历史证明供恢复追溯；Web 清空仍由 publication hash 降级。所有派生信息在原 OrderRecord JSON 内，不新增持仓事实库。
6. 在 source I/O 后、发布前重读全部订单。目标量在读取期间推进则不认领新量；原观察与发布同 SQLite fence 保护。后续 manual/cron/analysis 发布也重核已跟踪覆盖，旧缓存晚被读到不能保留错误成功；Web 清空/导入导致 hash 不一致时，状态查询撤销当前已覆盖宣称。
7. 原有 Last-good、完整性、FX、现金碰撞及 U0 source-account 校验保留。无法证明即 partial，不能通过放宽空快照、费用或身份检查消除未证实。

此版本没有 broker 提供的原生“持仓已处理至某 execId”水位；可信原始时间加数量/现金变化才可作最低佐证。当前 IBKR 同步的 `positions_as_of` 仍为 UNKNOWN，所以真实 IBKR 可以自动发布最新官方持仓/现金，但默认不会被伪装为已证明覆盖；净零往返和无 pretrade anchor 的遗留订单也不会伪造完成。它们可同步最新官方资产，但覆盖仍未证实，需后续真实只读对账验收；缺基线或缺费用不能在首轮成功响应后直接停止资产刷新：同一激活窗口最多允许 4 次成功账户发布，沿原持久退避等待缓存更新；上限后保留 UNVERIFIED 和安全 stop reason。读取失败保留 Last-good 并继续原失败退避，不消耗成功发布预算。已有基线且数量/现金已匹配、仅永久缺原始水位时可以提前暂停。新成交、实质新证据、manual/prepare 或显式证据能力版本变化重新激活；重复回报、cron 和其他订单触发的同步不重置已暂停窗口。关闭的不可证实历史不成为新交易必须继承的基线。不能据此自动重提/撤单或从预计金额改账。

## 4. 查询与 U1 合同

- 业务权威只读投影：[trade_status](../app/trade_status.py)，复用原 OrderRecord/Run/SAFE_SYNC_CODES。Web `GET /api/action/trade-and-sync-status`，MCP `get_trade_and_sync_status(request)`。
- 参数仅 `order_id` / `batch_id` / canonical `symbol` / `limit`（1–20，默认 10）；只读当前服务器 Portfolio，不接受任意账号、refresh、SQL、路径或交易动作。多候选返回列表，不猜测哪个是用户刚才的交易。
- 返回 `trade_status`（原状态）、filled/ordered quantity、avg price、实际 execution 时间（没有则 NULL）、本地观察时间、券商订单 ID；佣金 `{status: KNOWN/UNKNOWN, amount, currency, amounts_by_currency}`，不导出 WhatIf。已取得的跨币种费用按原币列出，但不冒充交易币种费用参与现金证明。
- Tiger SDK 3.5.8 的 Order 虽有汇总 commission，但当前 Adapter 没有 execId/费用币种限定的佣金合同；显式返回 `UNSUPPORTED_BY_ADAPTER`，不猜测原币或使用估算值。来源持仓/现金时间仍 UNKNOWN，不能把本地 trade_time 当成资产来源时间。
- `asset_sync.status` 为 `NOT_REQUIRED / SYNC_PENDING / SYNC_RUNNING / SYNCED / SYNC_DELAYED / SYNC_FAILED / SYNC_UNVERIFIED`，独立包含 target/covered quantity、position/cash proof、Run/最后成功更新时间、publication 状态和安全 reason/质量；另列成功发布重试计数/上限、retry_stop_reason、evidence_support 及历史覆盖数量/Run，区分当前资产资格与历史证明。不导出原账号、claim token、anchor、raw SDK、执行原始回包。
- 新工具 `readOnlyHint=true`，无券商 I/O或 reconcile，原七工具权限不变；默认生产变为八项只读工具。原可选 prepare 启用后共九项，其中只有 prepare 不是纯读取。旧测试的发现数量随真实能力新增更新，安全断言不放宽。
- 新 U1 prepare 遇 pending 不能只凭最近成功 freshness 跳过；原同步可以尝试覆盖。冻结副本内按同 Owner 检测待进度，增加 `FILL_SYNC_PENDING_OR_UNVERIFIED`、partial、禁止未经说明的精确调仓；旧 fixed payload/ID 不修改。用户交易后重新分析应新建 prepare 任务，不能改写旧 snapshot_id。

## 5. 最小 Schema 与生产发布输入

仅 **1 列、0 索引、0 新表**：`order_records.fill_sync_json TEXT NULL`。旧 NULL 不证明已消费，也不回填虚构 anchor；旧历史 terminal 不批量回放，原 poller 再发现的真实成交才以无基线进度保守处理。原 OrderRecord 主键和 U0 账户唯一 claim 已足够防重，不增加通用任务索引/状态表。

字段必要性：成交已提交后崩溃、Run claim 前崩溃、失败等待和后续 execId/实际费用不能只留内存；原 broker raw 回包会被覆盖，不适合作为可靠消费进度；原 SnapshotRun 在首次成交时尚不存在，也不能承担订单提交前基线。字段不改不可变 submission 授权 payload。

- [u2_schema](../app/u2_schema.py) 先验证 U1/B0/U0；完整 Backend 共享模式仅验证，不自动迁移。原 [迁移 CLI](../scripts/migrate_b0_safety.py) 明确 `--stage u2`；默认仍 B0，U2 禁止 corrections。
- 另获发布授权后：固定 main ancestor SHA/CI → 原 Mini install-github → 停所有 writer/协调读者 → Backup API、克隆 plan 与 inventory/integrity → 审核仅 1 ADD COLUMN → 原地 apply/verify → 独立 u2 receipt/精确 Digest，旧 B0/U0/U1 receipt 保留 → 启写前 mark-writers-enabled → 配套 Backend/Web/MCP。
- 生产不能混跑不理解 U2 的 writer；旧组件不知道填充/重核覆盖进度，不能假设回退代码仍保持本批合同。启写后不得旧备份覆盖、反向 DROP 或随意重签 Digest。只有未启写且 inventory 未变时沿原 restore-prewrite；中断按 receipt resume-contract，其他前滚修复/只读降级。
- 沿用原 prepare 鉴权/cache/Owner allowlist，不改交易开关。只读验收：已有真实订单的恢复/账户抽样对账、Gateway 离线/现金延迟/未知费用、同 ID Web/MCP 和新 U1 partial。真实新交易测试、撤单和权限改变另行逐次授权，不包含本 PR。

## 6. 隔离验收入口

- [U2 链路/故障注入](../tests/test_u2_fill_sync.py)：真实原提交 claim/确认/观察与原 Repository/Executor，用合成 WAL 和 Mock SDK；覆盖累计成交、同账户并发、崩溃恢复、缓存滞后、清仓/反向交易、未知费用、账号隔离及 U1。
- [PR #25 定向安全回归](../tests/test_u2_review_regressions.py)：五个评审探针改为安全断言，补缓存迟到/跨进程预算、Tiger 能力缺口与恢复、历史买卖窗口、U1 pending 清除及真实回退负例。
- [迁移与恢复](../tests/test_u2_schema.py)：真实 U1 shape → 一列、receipt/inode/Digest、幂等、禁止订正及启写后回滚。
- [MCP 协议](../pocs/chatgpt_skills/test_protocol.py)：独立运行时调用真实只读 worker，交易/同步状态分别返回、拒绝 refresh、无原始账号/SDK，DB 不变。
- [固定视图浏览器](../frontend/tests/e2e/asset-view.spec.ts)：新视图披露未证实成交，旧视图不变；原 restricted/trade-link 全部继续跑。完整 PEER 通过既有 U1/Offline M5 验证，不在浏览器接付费模型。
- 全量 pytest、同步专项目录、原部署/shared/restricted、frontend lint/build、Offline M5、文档 checker 和 exact HEAD 四项 CI。精确数量及运行链接以 PR/交付报告为准；本文件不把代码存在或测试数当生产验收。

本轮仅开发、隔离测试、PR 和定向自查；不合并、不部署、不访问真实账户、不启动 B2/B3/QMT。
