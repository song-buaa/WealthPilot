# Phase 0｜Governance Validation Report

> 第 1–6 节保留初轮同会话演练记录；最终独立新会话验证见第 7 节，初轮“未验证”仅描述当时状态。
> 2026-10-17。模拟需求：“IBKR 订单成交后，自动同步投资账户持仓及现金，并让 ChatGPT 查询最新状态。”本轮只执行静态开发演练，不改业务代码，不运行交易、真实同步或生产服务。

## 1. 演练方法与规则发现

同一 Codex 会话在文档整合后，以仓库根为入口重新读取 AGENTS 及其四份必读文档，再检索订单/成交/同步/MCP 与测试。记录的是本次会话的规则发现及静态推演，**没有新建独立 Codex 会话或在第二台设备实测自动加载**；不把“链接存在”当所有客户端自动执行的证明。

可复查命令（只读仓库）：

```bash
cat AGENTS.md README.md docs/PROJECT_GOVERNANCE.md docs/ARCHITECTURE_CURRENT.md docs/CAPABILITY_REGISTRY.md
git ls-files '*AGENTS.md'
rg -n 'sync_order_status|_sync_execution_batch_linkage|filled_quantity' backend/services/action/order_manager.py
rg -n 'sync_order_status|session.commit' backend/services/action/order_poller.py
rg -n 'sync_and_persist|PositionUpsertService|cash_balances|_source_channel' backend/services/broker_sync/snowball
rg -n 'real_config|get_trade_order_status|get_investment_details' pocs/chatgpt_skills/server.py
rg -n 'test_order_poller_state|test_.*cash|test_.*sync' tests/test_execution_batch_service.py backend/services/broker_sync/tests
python scripts/check_engineering_docs.py
```

## 2. 六项验证结果

| 要求 | 结果 / 证据 | 限定 |
|---|---|---|
| 1 发现 AGENTS 及架构规则 | 本会话走查通过：根 AGENTS 明确引用 README、治理、现行架构、能力索引，checker 检查必读引用 | 其他设备/全新客户端自动加载尚未实测；不宣称跨会话行为强制执行已验证 |
| 2 找到现有订单、成交与同步能力 | 静态通过：[poller](../backend/services/action/order_poller.py) → [OrderManager](../backend/services/action/order_manager.py)；[SnowballSyncService](../backend/services/broker_sync/snowball/sync_service.py) → [IBKRPortfolioAdapter](../backend/services/broker_sync/snowball/adapter.py) → [upsert](../backend/services/broker_sync/position_upsert_service.py) | 当前以轮询状态/成交量及 snapshot 为可复用依据，未发现可直接使用的可靠持久化成交后同步事件闭环 |
| 3 区分已有/缺口 | 静态通过：订单、累计量、Batch linkage、IBKR 持仓/原币现金同步、投资详情读取已有；成交变化触发同步/可靠重试和 real 只读订单查询缺口明确 | 不是说“IBKR 同步未实现”；代码有能力不等于生产账号/Gateway 已验收 |
| 4 最小变更方案 | 方案通过规范走查：原订单提交状态后触发/合并原同步入口，成功提交后原工具读取；订单查询仅增薄投影 | 本轮没有实现/测试未来事件持久化语义；具体可靠性设计待下一任务评审 |
| 5 不建平行交易/持仓服务 | 通过：沿用 OrderManager、PositionUpsert、Portfolio、WealthService；保留 snowball/ibkr 历史归属，不新增第二套模型/交易流程 | 若后续论证必须新增持久化任务记录，先评审最小 schema 需求及批准，不默认建消息平台或第二套同步服务 |
| 6 生产/交易权限边界 | 通过：券商只读快照与本地/生产写入区分；MCP 仅查询，用户最终确认仍在 Web；不 place/cancel/modify，不启用交易门 | 本轮授权文档/PR，不包含新业务实现、Mini 同步、重启或真实订单验收 |

## 3. 已有调用链与具体缺口

**订单 owner**：[OrderPoller._poll_once](../backend/services/action/order_poller.py) 对可轮询订单调用 [OrderManager.sync_order_status](../backend/services/action/order_manager.py)，持久化 filled_quantity/均价/时间和审计，重新汇总策略累计量；`_sync_execution_batch_linkage` 回写关联 Leg/Batch。[现有测试](../tests/test_execution_batch_service.py) 的 `test_order_poller_state_propagates_to_execution_batch` 验证批次联动，不证明 Portfolio 更新。

**持仓/现金 owner**：[broker_sync._run_sync](../backend/api/broker_sync.py) 选择既有 service；SnowballSyncService 的 IBKR 分支调用原 IBKRBrokerAdapter `get_account_info` 和 `get_positions`，将逐币种现金和证券转快照，再由 PositionUpsertService 按 broker/account/source 原范围对账。成功/失败 run 由原 repository 记录；trigger 的“任务已提交”不是成功。[cash tests](../backend/services/broker_sync/tests/test_ibkr_cash_sync.py) 和 [upsert tests](../backend/services/broker_sync/tests/test_position_upsert_service.py) 可复用。

**缺口**：已核对 OrderManager、OrderPoller、ExecutionBatch 和 main 的相关路径；订单同步链没有接上述持仓同步调用。main 定时任务是独立触发。IBKR callback/成交明细读取能力不自动等于具备可恢复、去重、事务一致的业务事件投递。部分成交、重复轮询、同账户并发同步、进程退出后重试及券商更新延迟均需明确设计；本轮不凭经验声称它们已解决。

**查询 owner**：[reader](../pocs/chatgpt_skills/reader.py) 经 readonly snapshot 读已提交 Position/来源质量；overview 复用原 Wealth/Portfolio。生产 real server 只有原七工具，既无订单查询，也无主动 push。Mock get_trade_order_status 会调用 OrderManager 并写审计，不能包装为只读生产查询。查询时刻并不保证券商已完成后续 snapshot 更新。

## 4. 复用优先的最小方案（仅设计，未授权实施）

1. 需求拆清“同步持仓/现金”和“查询订单/同步结果”；风险取 L3（成交状态/生产写路径），离线开发与上线授权分开。输入必须有真实 broker、账户和订单身份，IBKR 同步仍映射历史 snowball 通道，不根据字符串盲建新 owner。
2. 在原订单状态事务**成功提交后**观察累计成交量增加或相关成交状态变化，再向原同步入口提出该账户刷新请求。不要在券商 callback 内直接持仓加减、现金扣减，也不要在订单数据库事务中做阻塞网络调用。部分成交也需要刷新，不能只关注最终 filled。
3. 复用 SnowballSyncService / PositionUpsertService 的完整成功 snapshot，按账户合并重复触发并防并发覆盖。先核实原调度能否覆盖可靠重试；本轮未证实已有 durable job。崩溃丢触发、延迟/失败重试、去重和事务提交顺序须提出最小可靠性方案，评审后再决定是否需要任务记录，禁止直接引入消息平台/第二个同步服务或 schema。
4. 成功写入并提交后，Web 沿用原刷新事件/读取；MCP 继续调用 get_investment_details/get_wealth_overview，返回当次已提交快照及原来源时间/失败质量，不能谎称即时实盘。若“最新状态”包括订单与同步结果，在原查询 service/model 上增加最小只读投影，MCP 仅适配脱敏状态，不调用 refresh=true 或任何同步写入。
5. 不改变原草稿/Batch/OrderManager、风控、确认哈希、幂等或最终 Web 人工确认；不重复发单，不重放历史生产订单。真实持仓/现金必须由券商 snapshot 权威对账，未知/失败不能清空账户或标记最新。

## 5. 后续实施应补的验收（本轮未执行）

使用临时 DB、fake IBKR 与 Mock 数据，覆盖：新成交/部分成交 → 订单/批次提交 → 触发原同步 → 持仓和分币种现金成功写入 → Portfolio/财富页面与 MCP 一致；重复轮询不重复应用，其他账户/来源不受影响，BASE 不重复计入。验证同步失败保留旧快照且标失败/过期、延迟后重试、重启恢复、并发、空快照成功与连接失败的区别；只读 MCP 查询不得产生订单/数据库写入。

复用定向入口：[OrderManager tests](../backend/services/action/tests/test_order_manager.py)、[batch linkage tests](../tests/test_execution_batch_service.py)、[cash sync tests](../backend/services/broker_sync/tests/test_ibkr_cash_sync.py)、[sync status tests](../backend/services/broker_sync/tests/test_sync_status_api.py)、[MCP protocol](../pocs/chatgpt_skills/test_protocol.py)、[财富一致性 E2E](../frontend/tests/e2e/wealth-portfolio-consistency.spec.ts)。后续新增缺口测试和完整 Gate，不能把现有测试名字当未来场景已通过。

真实验收需独立批准 Mini 同步写入及对应只读券商查询，并验证数据、Web/ChatGPT 实际呈现、权限和恢复。新成交实盘测试若有必要还需逐次交易授权，不能自动下真实单制造事件。Web/iOS 各自记录；工具传输成功不能替代最新业务状态/设备可达性验收。

## 6. 规范缺口与结论

本次静态演练找到了既有 owner、缺口及最小复用方案，没有建议平行服务；权限边界可由入口文档和 PR 模板追溯。结构检查、同会话走查与未来实现的业务验收是三种证据。

未关闭的规范验证项：新会话/另一设备的自动加载实测；任意架构文字与代码的自动语义一致性；未来同步事件可靠性设计及生产验收。规则已要求显式加载/报告 UNKNOWN、设计评审及完整场景验收；文档本身不能代替这些执行证据。本轮交付后停止，不进入 4.1 实施。


## 7. 最终修订后的独立新会话验证

### 会话与证据范围

2026-10-17 使用 Codex app 新建独立会话 **Phase 0 新会话只读规范验证**（`01a147f2-3ea2-7c10-b426-9fd6653400f4`，host=local），没有 fork 前序聊天。初始输入只有目标仓库目录、模拟需求、只读边界与交付要求，没有提供文档名单、既有方案或前序报告；要求不读取其他聊天、memory 或先前验证报告。该会话已 completed 并停止，没有写文件、运行测试或连接业务服务。

读取记录显示：先检查祖先目录规则，再自行检索并读取根 AGENTS → README → PROJECT_GOVERNANCE → ARCHITECTURE_CURRENT → CAPABILITY_REGISTRY，之后检索原代码、测试源码、PR 模板和 CI。没有打开先前模拟报告。验证对象是 HEAD `bf6701c` 上的未提交修订快照；四份核心文档在会话开始/结束的 SHA-256 完全一致，后续提交包含这份相同核心快照：

| 核心文档 | SHA-256 |
|---|---|
| AGENTS.md | `1aca873f7493a60eb5470ccc7b04351c5c22fc0508bc588333208a650becdffe` |
| ARCHITECTURE_CURRENT.md | `d3d0d0fabd57fde1d1156f3f3400ca90135fe2e04d5b00b06e6778b677d993fe` |
| PROJECT_GOVERNANCE.md | `54a33b50383c77c76d487f46c0a673223e8c472ff8e1332fb2d810bac3955c22` |
| CAPABILITY_REGISTRY.md | `3acc3af787862c63d4117001ea7016c8bfaef20472a24e3e0c75694e0946c8e2` |

期间主会话仅为两份既有报告补历史/最终轮次标签，导致独立会话观察到脏文档从六份变为七份并如实报告来源 UNKNOWN。主任务执行记录可确认这些报告改动来自主会话；核心文档哈希及业务目录均未变化。该观察没有被当作新会话写入或业务变更。

### 验收结果

| 项目 | 结果 |
|---|---|
| 主动发现规范与必读引用 | PASS（本机独立新会话的主动发现/读取，非所有客户端自动注入保证） |
| 自行检索已有能力、接口及测试 | PASS（读取源码，未把测试定义冒充测试通过） |
| 最小复用方案 / 无平行体系 | PASS：复用原 OrderManager、SnowballSyncService、snapshot/upsert、Portfolio 与薄 MCP |
| 数据/生产/交易权限 | PASS：区分券商只读与本地写库、模拟与实盘、查询与触发同步；按最敏感路径识别 L3 |
| Registry 判断 | PASS：本次无业务能力变化，无需改业务条目；未来入口/职责、迁移/废弃等按规则同步 |
| 核心文档职责和明显冲突 | 未发现必须采用第二套业务权威的冲突；摘要均回链唯一详细 owner。发现 CLAUDE 旧指针，已在本轮最小修正 |

独立会话额外发现 [restricted_sync.SyncExecutor](../backend/services/restricted_sync.py) 的互斥/run/中断记录可以评估复用，但它仅接受 manual/cron 且用于受限 Web，不能说完整 backend 已具备成交事件闭环。此前能力索引并非全量函数目录，因此没有以“未列出”断定无能力。

最小方案补足：原 [place_order](../backend/services/action/order_manager.py) 可首次返回即成交，而 [poller](../backend/services/action/order_poller.py) 不轮询 filled；将来触发方案须覆盖首次提交及后续对账、部分成交后撤单，不能仅在 poller 添加回调。还需核实订单到真实账户/Portfolio 的现有关联、现金/证券分步快照完整性及成交反映延迟。现有 [source_quality](../pocs/chatgpt_skills/reader.py) 主要返回成功日期，不足以证明特定成交已反映；后续需设计原同步请求/run 关联与明确时间/状态。以上只是模拟需求方案和未来验收项，本轮未实现或增加 schema。

### 限制与收口判断

独立性有明确范围：必读架构本来已含该模拟需求的缺口摘要，因此验证证明“新会话沿规范定位并用代码复核”，**不证明完全无提示地发现同一问题**。没有读取前序报告，不以文档摘要代替源码证据。

跨设备新会话未完成：当前项目清单仅提供本机 WealthPilot，没有本轮可用的远端验证项目；未迁移到生产 Mini，也未做设备可达性判断。按用户要求为非阻塞项，不冒充跨设备 PASS。

本轮规范验证无实质性合并阻塞；源码能力缺口留作下一阶段明确范围，不阻塞 4.1 启动，也不授权本轮实施。精确候选 CI、PR 合并/main SHA 与 main CI 在 PR #19 和最终交付记录核对；新会话静态通过不替代这些 Gate。无生产部署、数据库、交易配置或券商连接变更。
