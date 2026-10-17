# WealthPilot 4.1 B0 实现与隔离验收

开发基线：`origin/main` / `6a7927009a9d0364e57c1b14ef4ee1a7a52bd25c`。范围是已授权的交易安全、状态收敛与数据隔离底座；B1 成交后同步消费者、收益账本、MCP 新工具与生产发布均不在本轮。

技术依据为用户确认的《WealthPilot 4.1 B0 Technical Review》。本文记录最终实现，不替代 [当前架构](ARCHITECTURE_CURRENT.md)、[能力索引](CAPABILITY_REGISTRY.md) 或 [工程治理](PROJECT_GOVERNANCE.md)。生产操作的条件和步骤只在 [B0 Runbook](B0_PRODUCTION_RUNBOOK.md) 维护。

## 1. 唯一订单提交与恢复路径

权威仍是 [OrderManager](../backend/services/action/order_manager.py)、原 OrderRecord/ExecutionLeg、[现有状态机](../backend/services/action/state_machine.py) 和 BrokerAdapter。没有 OrderAttempt 表、第二个交易服务或平行持仓体系。

1. 原 Web 风控审阅产生现有 AuditLog 中的 `order_reviewed` nonce。它不是权限密钥，也不替代原 Web 人工授权。记录固定 Portfolio/账户、量价/TIF、风险、订单 epoch、持仓风险事实 epoch、期限；preview/MCP 链接不写库。
2. 调用方显式提交自己的准备工作，OrderManager 拒绝带写事务进入发送。独立 Tx A 使用 BEGIN IMMEDIATE 重验 owner、授权、剩余目标/活动预留和版本，原子保存 UNKNOWN OrderRecord、不可变 payload、稳定 key、策略和 Leg 关联后提交。
3. **仅插入并确认提交成功的本次调用**获得一次发送资格；`OrderRecord.id` 为稳定 orderRef。相同 key 返回原记录，参数冲突拒绝；新 nonce/新 Batch 也不能绕过账户 UNKNOWN、执行 epoch 或已预留数量。
4. 券商 I/O 在 Tx A/B 之间。IBKR/Tiger 在实际 SDK send 前再次核对 TIF、账户和发送期限；无 HTTP/SDK 自动补单。IBKR 一旦进入 SDK mutation，SDK 回包、ack/snapshot 的异常均保持 UNKNOWN，保留稳定 ref 和可得身份，不伪造 REJECTED；确定未发送的本地拒绝仍可拒单。commit 异常会丢弃连接，不把不确定提交当发送资格。
5. 独立 Tx B 调用 `apply_broker_observation`，原子归并订单、策略累计成交、Leg、Batch 与审计。所有其他观察写入也必须在 broker I/O 后取得短写事务、清除旧 identity map 并重读当前事实，再归并、立即提交；已有 deferred snapshot 无法升级时 fail closed，不能回写旧值。调用方后续 rollback 不会擦掉已认领记录。

这保证同一认领最多由一个调用发送，**不承诺券商 exactly-once**。Tx A 后、真实 send 前崩溃会留下可能尚未发送的 UNKNOWN；重启只做只读核实，不能牺牲资金安全换取自动继续发送。

幂等键：Batch 为 owner + batch/leg（不含版本）；普通 Web link 为 owner + 原 draft/strategy；普通手动/计划策略为 owner + strategy/review nonce，保留 1:N 合法分次执行。历史 NULL key/owner 不猜填；没有 B0 策略创建证据的旧真实策略不直接重放，旧 Batch 无 owner/epoch 同样阻断。

`existing_submission` 与发送认领复用同一 key 计算及完整不可变参数/owner 校验。只有精确相同认领可在风险变化后只读重试；同策略任意历史订单不能豁免新 review 的实时风险与必要文字确认。

IBKR 恢复使用新鲜 Open、Executions、Completed；实际返回账户先核对再脱敏。支持稳定 ref 或已观察到的 permId，不用裸 orderId、ticker/金额/时间模糊匹配。Completed 的 SDK 默认 filled=0 不能覆盖真实 filledQuantity/cumQty；同量执行证据补充真实均价，默认 CommissionReport 不证明零费用。缺源、空结果、超出保留窗口保持 UNKNOWN；冲突/更正安全暂停，B0 不自动重算历史 financial bust。

## 2. 状态、确认与风控

- 首次 FILLED/PARTIAL、轮询、对账、取消及恢复共用 OrderManager 投影；累计成交按已知各 OrderRecord 重算，重复与乱序不重复累加，不以默认 0/None 覆盖已知事实。
- 终态不被旧 pending/缺源回包逆转；已撤余量的迟到部分成交更新成交事实，不推定订单重开；完整可信迟到成交可收敛 FILLED。明确更正/矛盾保留原事实并进入 UNKNOWN/paused/ATTENTION_REQUIRED，后续普通回包不解除争议。
- 全部 Leg 可信 FILLED 时 Batch 立即 COMPLETED。stop_remaining 仅停止未认领腿，不把仍活动的已提交订单显示为“券商已撤单”。
- 无 broker ID 不代表未发送。只有未认领的本地 Mock created 记录可本地取消；真实不明记录只恢复。取消受理后读取实际状态，再写审计，避免查询时 autoflush 开启写事务。撤单/查询网络异常也进入同一观察投影，按锁后最新数量保持事实、更新 Leg/Batch，不能只写 Order.status。
- 真实撤单在 mutation 前核对持久化 owner；IBKR 使用新鲜账户/ref/permId 和原 API client 匹配，不凭会话级数字 orderId 接管别的订单。旧 owner 缺失只恢复，不自动撤单；更换 client 后不自动绑定或修改 Gateway 权限。
- 原受控 Retry 也要求历史 broker ID 和券商 Cancelled/ApiCancelled 证据；旧本地误标 CANCELLED 或缺 ID 不能成为替代订单授权，仍须券商核实。
- REMAINDER 不再在发送时静默修改量价。变化时要求 `review-remaining`，外部计算仅生成临时结果；写入前短事务校验版本、linked/状态、经济参数、账本与 Portfolio 事实未变。并发提交/停止/重审则拒绝旧结果。只更新未认领腿，显示差异、撤销旧 hash/风险确认、重新人工确认；已提交腿、OrderRecord、旧确认版本保持原关联。整批 refresh 仍拒绝已有订单。
- 普通单标和 Batch 均调用原 RiskEngine 的 5% / 40% / 纪律检查；同 Portfolio 投资口径。纪律复用原 rule_engine 和已加载持仓，不调用完整 data_loader 的研究/行情/LLM 链路。缺估值、可信 FX 或纪律数据明确警告，不能伪装 PASS；IB01 没有警告豁免。
- 保留资金、合约、LIVE quote/30 秒 TTL、WhatIf/费用和原最终人工实盘确认文字。风险文字确认不能豁免这些硬门。原普通 GTC 与 Batch DAY 按实际已确认参数传到 SDK，unsupported TIF 拒绝。

## 3. Portfolio、同步与身份

[ownership](../backend/services/action/ownership.py) 是纯 owner 解析工具，不建账户映射表。使用 server session/已验证 shared contract/启动确定的 Portfolio 和明确 adapter 账户；不得按 Portfolio 1、最近 run、U 前缀猜测。一个完整物理账户跨 Portfolio 多义时 fail closed。

非共享模式存在多个 Portfolio 时，必须由 server session 或私有 `WEALTHPILOT_PORTFOLIO_ID` 明确选择，不能沿用旧 startup 的“第一个 Portfolio”。单 Portfolio 的已启动应用上下文只在库内唯一性得到验证时可用；shared 模式始终核对原契约。

原 Repository 从 reservation 到 source read 后绑定、persist、success/failed、latest 查询均保留 Portfolio 作用域；run 不可改绑到另一账户/Portfolio。PositionUpsert 的所有查询、legacy claim 和 stale 删除按同一 owner；完整空快照还必须提供匹配的 run。旧 owner=NULL 不成为新归属证据。Guojin 保留 QMT 管辖范围与截图保护，也不再跨 Portfolio 清理。手动、cron、restricted、已有运维脚本均显式传递 scope。

IBKR 下单 broker=ibkr，持仓沿用 broker=snowball / source_channel=ibkr，不新增 ibkr Position 通道。MCP 现有只读 reader 的 freshness 按 Portfolio/账户查询，未增加 real 写工具或订单工具。

LSE 身份依据既有 trusted conId/币种/ISIN/上市行，SMART 是执行路由，USD 不是 US 市场证据；CBU3 的 primaryExchange=EBS 不影响已验证 LSE 身份。证券身份不明阻断整份快照，不能丢弃未知行后清仓。原 raw_symbol 保留，canonical symbol 使用已验证本地上市别名。当前 Position 的历史错误只由私有 CAS 清单订正，碰撞或未订正旧行阻断同步；聚合键保留完整 symbol/币种，避免改正后再按裸 ticker 合并。

## 4. 模块与 Schema 范围

| 模块 | 修改内容 |
|---|---|
| action models / OrderManager / state_machine / poller | 原模型增加 durable claim，统一观察归并、短事务、按实际券商恢复；原 AuditLog 复用 |
| IBKR / Tiger / Mock adapter | 新鲜账户过滤恢复、SDK 证据与 TIF/deadline；Mock 使用显式合成账户，走同一认领协议 |
| execution_batch / link_handoff / 原 REST API | owner/风险/hash/epoch、未提交腿重审、短 CAS 防并发重复 review，保留原确认体验 |
| broker_sync / restricted_sync / main / 既有同步脚本 | 显式 Portfolio/run/账户，IBKR 原通道映射及空快照/legacy/stale 隔离 |
| symbol / position_aggregator / 现有 MCP reader | LSE canonical 身份、避免跨市场/币种合并、同 scope 的同步质量 |
| app.database / state / b0_schema / migration CLI | shared startup verify-only，5 列/1 新 unique/0 表，精确 digest 迁移与 prewrite 回滚 |
| 原 Web 组件与 API 类型 | nonce 稳定重试、实际 TIF/owner、风险警告、UNKNOWN 恢复显示、未提交腿重新审阅 |
| 现有测试与 B0 专项测试 | 适配显式 scope/授权/事务，新增故障、SDK、迁移及数据隔离验收 |
| 历史交易验收 CLI | 缺少 durable claim/当前 Web 授权的入口默认停止，不能继续直接发单；原文件保留历史证据 |

每个字段/索引的必要性、历史 nullable 策略及既有 Batch 索引的兼容定义统一见 Runbook 第 2 节。不改 decision_engine、业务数据、生产配置；SDK 仅约束候选依赖，不升级生产。

## 5. 可重放隔离验收入口

| 故障 / 用户场景 | 证据入口 |
|---|---|
| 券商接单后本地崩溃/超时、跨重启成交恢复、重复/并发重试、commit 失败 | [B0 order safety](../tests/test_b0_order_safety.py) |
| 无 ID 的 SUBMITTED/UNKNOWN、取消不误判、首次 FILLED/PARTIAL、乱序/重复/更正 | [B0 order safety](../tests/test_b0_order_safety.py)、[原 cancel tests](../backend/services/action/tests/test_cancel_order.py) |
| Batch 全成交收敛、REMAINDER 重审续办、已提交腿不重发、I/O 不持写事务、风险警告无豁免 | [Batch service tests](../tests/test_execution_batch_service.py) |
| 新鲜 Completed/Executions、实际账户过滤、permId、缺佣金、歧义与 clientId=0、IBKR/Tiger TIF/过期发送 | [SDK recovery tests](../tests/test_b0_ibkr_recovery.py)、[原 IBKR tests](../backend/services/action/tests/test_ibkr_adapter.py) |
| 空快照、跨 Portfolio、多义账户、run 改绑、旧 NULL owner、LSE/EBS/SMART、聚合隔离 | [data isolation](../tests/test_b0_data_isolation.py)、[原同步纠错](../tests/test_portfolio_sync_corrections.py)、[broker sync tests](../backend/services/broker_sync/tests) |
| 原/目标 digest、错索引、部分 Schema、DDL 后中断、LSE CAS 保留原事实、prewrite restore 与写入后拒绝恢复 | [migration rehearsal](../tests/test_b0_schema_migration.py) |
| 原 Web 编辑→风险→人工确认→Mock 下单/成交/历史/不重放；IB01 资金/费用/TTL/改价/桌面与移动视口 | [trade-link browser tests](../frontend/tests/e2e/trade-link.spec.ts)、[restricted Web](../frontend/tests/e2e/restricted-web.spec.ts) |

完整 Gate：文档 checker、diff check、compileall、全量 pytest、独立 MCP/共享库/同步测试、frontend lint/build、Offline M5 18/18，以及 PR exact HEAD 四项 CI。最终命令、退出码、完整 SHA、PR/Run 链接在 PR 与交付证据包记录；测试数不能替代上述场景。

### PR #21 定向评审回归

| 意见 | 正式安全断言 / 原测试入口 |
|---|---|
| R02 | [SDK recovery](../tests/test_b0_ibkr_recovery.py)：`test_r02_accepted_sdk_then_local_error_stays_unknown_and_cannot_resend`；SDK/本地 snapshot 故障后原认领 UNKNOWN、新 review 不二次发送、重启恢复原 ref |
| R01 | [order safety](../tests/test_b0_order_safety.py)：`test_r01_old_connection_observation_cannot_erase_committed_fill`；[Batch tests](../tests/test_execution_batch_service.py)：`test_r01_old_connection_cannot_reopen_filled_batch`；独立 WAL 连接与旧 ORM 对象不能回退订单/累计量/Leg/Batch |
| I02 | [Batch tests](../tests/test_execution_batch_service.py)：`test_i02_network_unknown_updates_order_leg_and_batch_without_writer_io`；撤单/同步超时均一致归并，网络期间其他连接可取得 writer |
| R03 | [order safety](../tests/test_b0_order_safety.py)：`test_r03_new_review_after_prior_fill_must_recheck_risk_and_acknowledge`；实际风险计算由可信 FX 变为 fallback，已有首笔也不能跳过新风险确认；完整不可变参数冲突拒绝 |
| I01 | [Batch tests](../tests/test_execution_batch_service.py)：`test_i01_remaining_review_cas_never_changes_concurrently_claimed_or_stopped_leg`；重审期间独立连接提交/停止，旧结果不得改写 linked 参数或停止状态 |
| R04 | [migration rehearsal](../tests/test_b0_schema_migration.py)：`test_r04_multiple_sources_cannot_become_one_canonical_identity`；同计划多源同目标、重复源及克隆后碰撞阻断，原行/数量/成本与契约保持不变 |

这些是业务安全断言，不能把原“缺陷存在”的复现脚本 exit 0 作为修复通过。旧 SHA 与新候选的红/绿结果、最新提交的定向复审及质量门在交付记录中分别保留。此次修复没有增加字段、索引或表。

## 6. 剩余生产条件与停止边界

生产实际 Schema/账户归属/旧数据清单、SDK ARM 兼容、Gateway 权限/历史窗口、真实重连和新入口首次真实成交仍需单独授权验收。隔离自动化不等于生产业务验收。

本轮没有执行生产迁移、部署、启停、数据库/配置变更或真实券商交易。成交后持仓/现金自动同步消费者、历史收益和 MCP 订单查询仍属于后续批次；OrderManager 成交不直接改 Position/现金。开发交付后停在 PR/CI，等待单独生产发布授权，不自动开始 B1。
