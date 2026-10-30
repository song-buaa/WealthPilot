# WealthPilot 已有能力索引

## 4.3 五层能力复用补充（2026-10-30）

五层职责、14份Skill逐项矩阵、研究融合与最小Gap统一见[统一投资决策架构](v4.3/UNIFIED_INVESTMENT_DECISION_ARCHITECTURE.md)。本节仅索引原Owner，不新建Registry/Runtime。CODE表示源码可用，生产/研究证据分别列出；不因主分支合入而升级策略资格。

| 能力/业务层 | 原Owner及入口 | 成熟度与复用限制 |
|---|---|---|
| 正式政策L1 | [philosophy](../backend/services/philosophy_service.py)、[discipline](../backend/services/discipline_service.py)、[config](../app/discipline/config.py)、[knowledge](../knowledge_base/README.md) | CODE：MD/JSON/默认/索引分流；投资理念、投资纪律、长期资产配置三类政策由Investment Policy Bundle统一引用与管理版本为TARGET；Markdown、结构化配置及最终权威关系待P0人工审议，不是已有有效政策API；私人现役配置UNKNOWN |
| 共享证据L2 | [U1](../app/asset_views.py)、[Portfolio](../backend/services/portfolio_service.py)、[Evidence](../backend/services/market_data/evidence.py)、[SEC](../research_v2/sec_disclosures.py)、[Repository](../research_v2/repository.py) | CODE/限定历史运行证据；复用身份、币种、来源时点及质量；共享目标池版本引用仍PROPOSED |
| 可复用分析L3 | [SkillsLoader](../backend/skills/loader.py)、[Tool Layer](../backend/graph/tools.py)、[AllocationService](../backend/services/allocation_service.py)、[financial_research](../backend/services/market_data/financial_research.py) | 14份定义，本轮Loader发现13项；action-planner YAML失败但Web直连服务保留。方法/计算可复用，不把technical signals称为S1/S2策略 |
| 综合判断L4 | [原PEER](../backend/services/decision_service_v3.py)、[Expressing](../backend/agents/expressing_agent.py)、[Reviewing](../backend/agents/reviewing_agent.py)、[Output Policy](../backend/services/market_data/output_policy.py) | CODE/既有单标组合链路；池内公司与配置两种排序及统一冲突解释为TARGET，不投票或累加相关因子 |
| E1及复核L2/L3/L5 | [investment_thesis](../backend/services/investment_thesis)、[MCP研究桥接](../pocs/chatgpt_skills/investment_thesis.py) | 核心真人保存/提醒历史验收；PR #59已发布并通过首次水位实测，水位后真实自然新事件PENDING；验收时AUTO_SCAN=OFF，本轮未复测生产；partial/敞口UNKNOWN不升级，历史不覆盖 |
| 授权执行与复盘L5 | [ActionPlanner](../backend/services/action/action_planner.py)、[RiskEngine](../backend/services/action/risk_engine.py)、[OrderManager](../backend/services/action/order_manager.py)、[Batch](../backend/services/execution_batch/service.py)、[BrokerAdapter](../backend/services/action/brokers/base.py)、[历史域](../backend/services/portfolio_history) | 原实现/限定路径历史验收；建议→具体人工授权→原交易门→成交证据。无新策略执行资格；原当前收益不是TWR |
| S1/S2及成熟ETF研究 | 仓外冻结S1-C0、S2-F1、S2-G1报告/hash见[研究矩阵](v4.3/UNIFIED_INVESTMENT_DECISION_ARCHITECTURE.md#6-策略融合与成熟度清单) | RESEARCH_ONLY；S1/S2自研已结项不晋升，SPMO产品关注/政策采纳不等于自研动量上线或具体交易许可 |
| S3/S4/异常偏离候选 | 原[财务计算](../backend/services/market_data/financial_research.py)、[技术证据](../backend/services/market_data/technical_evidence.py)、E1、PEER可供复用 | TARGET：质量估值池内比较；S4为后续协作，弱趋势先复核；均值偏离只作线索。无对应新Skill、完整生产策略或本轮开发授权 |

新增公共契约只补目标池版本、PolicyRef、共享证据/方法引用与综合/执行关联，扩原Agent/Analysis/E1/交易Owner；本轮未实现、未修改Schema或工具。测试入口沿本索引原条目，文档更新不冒称重新完成业务验收。

> 2026-10-22：4.1 RELEASED，B0/U0/U1/U2/D1/B2/B3及国金已生产交付；运行观察与真实数据覆盖不自动视为完整。生产代码及验收见[4.1结项](releases/v4.1.md)。本索引只维护复用入口，流程由[治理文档](PROJECT_GOVERNANCE.md#registry-维护检查)维护。

任务先读 [AGENTS](../AGENTS.md)、[当前架构](ARCHITECTURE_CURRENT.md) 和[治理工作流](PROJECT_GOVERNANCE.md#9-标准开发流程与风险分级)，再按条目检索代码与测试。状态“已有”只证明仓库存在实现；生产验收及数据质量单列。API 前缀以 [main.py](../backend/main.py) 挂载为准。

| 能力 / 业务边界 | 当前实现状态 | 权威代码入口 | 现有接口或工具 | 复用建议 | 测试入口 | 已知限制 |
|---|---|---|---|---|---|---|
| 资产与负债：完整财富含手工资产/负债及投资汇总 | 已有 | [wealth_service](../backend/services/wealth_service.py)、[WealthItem / WealthSnapshot](../app/models.py) | [wealth API](../backend/api/wealth.py)：`/api/wealth/summary`、items；`get_wealth_overview` / `get_manual_wealth_items` | 投资部分调用 Portfolio；沿用 included / already-accounted 去重 | [wealth tests](../tests/test_wealth_service.py)、[财富刷新 E2E](../frontend/tests/e2e/wealth-refresh.spec.ts) | 养老金仅金额级去重，未建立持仓身份映射；Web summary 写聚合快照，MCP 不可直接调用 |
| 投资账户及持仓：本地投资事实及账户归属 | 已有 | [PortfolioService](../backend/services/portfolio_service.py)、[Position / Portfolio](../app/models.py)、[analyzer](../app/analyzer.py) | [portfolio API](../backend/api/portfolio.py)：`/api/portfolio/summary`、positions；`get_investment_details` | 沿用 portfolio / broker / account / source 归属，不把 ticker 当跨账户主键 | [同步归属回归](../tests/test_portfolio_sync_corrections.py)、[一致性 E2E](../frontend/tests/e2e/wealth-portfolio-consistency.spec.ts) | 本地持仓是上次成功同步/导入结果；MCP opaque account_ref 是存储分组，不是实时账户盘点 |
| 现金与多币种：展示现金和执行预算分开 | 已有 | [IBKR adapter](../backend/services/action/brokers/ibkr.py)、[IBKRPortfolioAdapter](../backend/services/broker_sync/snowball/adapter.py)、[FXService](../app/fx_service.py)、[预算计算器](../backend/services/execution_batch/calculator.py) | `get_account_info` / `get_cash_snapshot`；Portfolio 与财富读取 | IBKR 逐币种 CashBalance → 原同步 CASH 行；预算复用原币 cash ledger；Tiger 同次同步持久化逐币种 CASH，读取不再追加现金虚拟行 | [IBKR cash sync](../backend/services/broker_sync/tests/test_ibkr_cash_sync.py)、[native cash](../backend/services/action/tests/test_ibkr_native_cash.py)、[batch calculator](../tests/test_execution_batch_calculator.py) | 排除 BASE 重复汇总；不把 AvailableFunds/BuyingPower 当可支出原币现金。U0 缺源拒绝发布并保留 last-good；已确认零/负现金保留。FX TTL/实际日期/fallback/unknown 见原 FXService，未知非零估值不按 1:1 换算；CNH 使用同一 Provider 的 v2/BI 同日 USD 交叉报价，见 [CNH 回归](../tests/test_cnh_fx.py)，不与 CNY 合并 |
| 投资组合分析与配置 | 已有 | [AllocationService](../backend/services/allocation_service.py)、[PEER 编排](../backend/services/decision_service_v3.py)、[分类权威](../backend/services/instruments/classification.py) | [allocation API](../backend/api/allocation.py)、[decision API](../backend/api/decision.py)；`review_portfolio` | 复用配置快照/偏离和原专业结论；分类沿用 canonical evidence | [分类测试](../tests/test_asset_classification.py)、[分析适配测试](../tests/test_analysis_skills.py)、[Offline M5](../scripts/m5_e2e_18_cases.py) | 投资持仓分母不等于完整财富；默认目标不是确认的个人偏好；未知分类单列 |
| 单标专业分析 / 研究 / 知识 | 已有 | [Planning](../backend/agents/planning_agent.py)、[Executing](../backend/agents/executing_agent.py)、[Expressing](../backend/agents/expressing_agent.py)、[Reviewing](../backend/agents/reviewing_agent.py)、[Skills loader](../backend/skills/loader.py) | decision SSE；`analyze_position`；[内部 Skills](../skills) | 原 invoke_skill / PEER 专业链路，MCP 只传历史及整理结果；不复制决策引擎 | [analysis tests](../tests/test_analysis_skills.py)、[Offline M5](../scripts/m5_e2e_18_cases.py) | 缺研究/行情/原则索引可 partial；不得将 fallback 当实盘投资依据。内部 Skills 与对外 MCP 不同层 |
| 历史交易与收益 | B2已交付，真实覆盖仍需补源 | [Portfolio历史域](../backend/services/portfolio_history)、[当前表现](../decision_engine/llm_engine.py) | `/api/portfolio/trade-history`、`get_trade_history`；原`analyze_performance`保留 | execution/实际费用/覆盖证据与移动平均成本复用原历史服务；当前持仓表现分开 | [B2金样](../tests/test_b2_history.py)、[安全回归](../tests/test_b2_review_regressions.py) | 缺期初/成交/费用/分红/历史FX时完整收益/HPR/年化保持未知；无全组合TWR/MWR，不覆盖Position成本或订单Owner |
| 交易预填及 Web 确认 | 已有；新入口首次真实成交未验收 | [trade_link](../pocs/chatgpt_skills/trade_link.py)、[link_handoff](../backend/services/execution_batch/link_handoff.py)、[Action](../frontend/src/pages/Action.tsx)、[确认弹窗](../frontend/src/components/ConfirmOrderDialog.tsx) | `prepare_trade_confirmation_link`；action trade-link preview / execution-batch trade-link APIs | 普通单标走原草稿/策略；IB01 预算变体复用 Typed Intent / Batch 和原 Web 编辑/确认体验 | [trade link tests](../tests/test_trade_link.py)、[IB01 tests](../tests/test_ib01_trade_link.py)、[trade-link E2E](../frontend/tests/e2e/trade-link.spec.ts) | URL 不写库/不连接券商；进入 Web 后主动操作才报价/落库；预填/预览不等于交易授权；报价/费用缺失保持阻断 |
| IBKR / Tiger 交易适配与风控 | 已有，范围各自受限 | [BrokerAdapter](../backend/services/action/brokers/base.py)、[IBKR](../backend/services/action/brokers/ibkr.py)、[Tiger](../backend/services/action/brokers/tiger.py)、[RiskEngine](../backend/services/action/risk_engine.py) | [action API](../backend/api/action.py)、[batch API](../backend/api/execution_batch.py) | 始终经过原 OrderManager；Batch 额外资金/合约/费用/确认哈希校验；不可用风险确认绕过执行安全门 | [IBKR adapter tests](../backend/services/action/tests/test_ibkr_adapter.py)、[batch service tests](../tests/test_execution_batch_service.py) | 默认只读及实盘开关关闭；普通 US/HK 与可信 LSEETF/USD/SMART Batch 路由不可互相冒充；不自动扩大支持市场 |
| 订单状态及成交 / 批次状态 | 已有轮询与关联回写；U2 已合入的实现增加原 poller 成交消费，详见下节 | [OrderPoller](../backend/services/action/order_poller.py)、[OrderManager](../backend/services/action/order_manager.py)、[ExecutionBatchService](../backend/services/execution_batch/service.py) | `/api/action/orders`、`/{order_id}?refresh=true`；batch refresh / reconcile | 复用 OrderRecord 状态机、累计成交量及关联 Leg/Batch 更新；查询带 refresh 会持久化状态，不能当只读 MCP 工具 | [OrderManager tests](../backend/services/action/tests/test_order_manager.py)、[batch service tests](../tests/test_execution_batch_service.py) | 轮询状态不是 Portfolio 快照；超时/unknown 不盲重提；U2 已合入的实现新增不刷券商的只读 trade/sync 查询 |
| 持仓同步（包括 IBKR 原币现金） | 已有手动及定时；U0 增加跨连接认领和发布 fence | [broker_sync API](../backend/api/broker_sync.py)、[SnowballSyncService](../backend/services/broker_sync/snowball/sync_service.py)、[PositionUpsertService](../backend/services/broker_sync/position_upsert_service.py)、[repository](../backend/services/broker_sync/repository.py) | `/api/broker-sync/trigger`、status；[main lifespan](../backend/main.py) 定时任务 | IBKR 复用历史 snowball 通道及 source_channel=ibkr；统一 SyncExecutor，按 Portfolio/broker/account/source 认领及原子发布；空/部分/重复输入保留 last-good，避免另建同步/现金库 | [cash sync](../backend/services/broker_sync/tests/test_ibkr_cash_sync.py)、[upsert tests](../backend/services/broker_sync/tests/test_position_upsert_service.py)、[sync status](../backend/services/broker_sync/tests/test_sync_status_api.py) | trigger 响应是任务提交，不是成功；IBKR 与 Snowball 标识历史兼容，不能另增 ibkr 通道造成重复。券商只读同步仍会写本地/生产数据库，须授权 |
| MCP 工具及只读协议 | 原七项及U2/B2/B3只读扩展已交付 | [server](../pocs/chatgpt_skills/server.py)、[worker](../pocs/chatgpt_skills/worker.py)、[reader](../pocs/chatgpt_skills/reader.py)、[PoC02 readonly reader](../pocs/poc02_wealth_mcp/reader.py) | 七工具名称与边界见[当前架构](ARCHITECTURE_CURRENT.md#3-七个生产工具) | 优先扩展薄适配，原业务 owner 计算；每请求读取已提交数据并报告来源/质量 | [protocol tests](../pocs/chatgpt_skills/test_protocol.py)、[reader tests](../pocs/poc02_wealth_mcp/test_reader.py)、[analysis tests](../tests/test_analysis_skills.py) | Mock 写工具仅隔离测试。U2 已合入的实现增加只读交易/同步状态工具；仍无主动通知；读到最新已提交数据不保证券商已同步；Web/iOS 各自验收 |

新增能力前须填写“候选 owner → 已检索入口/测试 → 缺口 → 最小扩展 → 权限/验收”。本索引未列出某项能力不等于仓库没有实现，应继续检索。消费、技术形态等专属设计保持原 owner，不能因本索引不是完整目录而另建体系。

### B0 已合入的复用更新

交易提交、状态与同步的权威入口沿用上表。B0 在 [OrderManager](../backend/services/action/order_manager.py) 增加 `create_order_review` / durable `place_order` / `apply_broker_observation`；在原 [Batch service](../backend/services/execution_batch/service.py) 增加 `review_remaining`，保留已提交腿。原 [IBKR adapter](../backend/services/action/brokers/ibkr.py) 的 `recover_order` / `find_order_by_ref` 使用新鲜账户过滤证据。原 sync Repository/Upsert 和现有两个 MCP reader 统一 Portfolio/run/账户作用域；LSE 身份沿用既有 trusted instruments。

复用时必须显式提交准备事务、传当前 owner/授权和确认参数，不绕过 Review/hash/epoch/TTL/WhatIf/人工风险确认。UNKNOWN/NOT_FOUND 不补单，缺 owner 的历史记录先核实；未订正的 LSE 身份冲突阻断同步。新 Schema 的字段/索引理由与兼容规则见 [Runbook](B0_PRODUCTION_RUNBOOK.md)，故障注入及原 Web 场景入口见 [实现说明](B0_IMPLEMENTATION.md#5-可重放隔离验收入口)。B0 已合入且用户确认已部署；历史开发文档不能作为本轮生产实测；B0 不包含成交后同步消费者或 real MCP 订单工具，U2 已合入的实现见下节；B0本身不承担历史收益；B2条件归因及覆盖限制见下节。

PR #21 定向修复仍使用上述 owner：`existing_submission` 复用发送 key/参数校验，观察归并锁后重读，`review_remaining` 在外部计算后进行版本/linked/事实 CAS；迁移对计划内及应用后的目标碰撞均拒绝。正式回归映射见[评审回归](B0_IMPLEMENTATION.md#pr-21-定向评审回归)，不新增业务服务或 Schema。


### U0 可复用合同（已合入）

[原 Executor](../backend/services/restricted_sync.py) 统一完整/restricted/manual/cron，原 [Repository](../backend/services/broker_sync/repository.py) 提供 durable scope claim、过期 fence、完整性与重复校验、FX 预取、原子发布/失败状态；[Upsert](../backend/services/broker_sync/position_upsert_service.py) 不在写事务内联网。各券商仍负责实际来源覆盖和完整性证据。状态 API 原字段兼容，新增 last_successful_run_id/data_quality；内部 token 不对外返回。手动 all 的未配置源留下失败尝试，不阻断已配置源。

U1 可复用 run id/last-good、来源/时间/币种/FX/质量，不得把单源 run id 当跨源一致 snapshot_id。U2 复用同 owner 执行和发布；U2 已补同事务成交进度、事务外认领、水位覆盖、补扫与退避（下节）。仍禁止从订单事务直接调用会 commit 的 synchronize。

PR #22 修复：Tiger 原 SDK 账户逐条核验、真实 SDK 纯现金合同；Upsert 对旧现金/导入/API 碰撞阻断，不自动认领。已核实同笔现金走原离线迁移工具的显式 cash_owner 映射与整行 CAS，生产核对另行授权；没有新 Schema 或现金服务。回归见[定向安全测试](../tests/test_u0_review_regressions.py)。

正式回归：[U0 安全测试](../tests/test_u0_sync_safety.py)、[U0 迁移演练](../tests/test_u0_schema_migration.py)，以及原 cash/upsert/restricted/readonly/browser 测试。迁移、证据及运行待验边界见 [U0 实现说明](U0_IMPLEMENTATION.md)。U0本身没有新增MCP工具、固定视图payload、收益计算、公司映射或QMT接入；后续批次能力见下节。


### U1 分析前准备与固定视图（已合入）

权威入口：[原同步上的 prepare](../backend/services/asset_analysis.py)、[固定视图合同](../app/asset_views.py)、[原只读 worker](../pocs/chatgpt_skills/worker.py)、[原 PEER 适配](../pocs/chatgpt_skills/analysis.py)。账户刷新和 publish 仍由原 SyncExecutor/Repository/Upsert/FX 负责；金额与手工/养老金去重仍由原 Portfolio/Wealth 计算。复用 WealthSnapshot 的 ID，新增 nullable request/payload 及请求唯一索引；不建账户/持仓/任务表。

接口：受保护 `/api/asset-analysis/prepare`、requests 状态、views 固定读取与评估；原 Wealth/Portfolio/MCP 读取和专业分析可传 snapshot_id。显式启用 `--prepare-config` 后才多注册一个非纯只读 `prepare_asset_analysis`，原七工具权限不变；未提供 ID 的原读取不自动同步。Issuer 证据在原 [instruments 域](../backend/services/instruments/issuers.py)，U1 初始确认理想双市场身份，D1 扩展见下节，未知不猜测，保留成员明细。

测试入口：[任务/隔离/PEER](../tests/test_u1_asset_views.py)、[API 权限](../tests/test_u1_api.py)、[Schema](../tests/test_u1_schema.py)、[MCP loopback](../pocs/chatgpt_skills/test_u1_protocol.py)、[Web](../frontend/tests/e2e/asset-view.spec.ts)。限制：源时间缺失仍 partial；30 分钟副本到期不能重跑旧专业分析，历史 payload 可读；原财富与投资/Allocation 分母不同；不含 U2 成交、B2 历史收益或 QMT。详细合同与发布影响见 [实现说明](U1_IMPLEMENTATION.md)。

PR #24 修复仍复用原 owner：Run context 的原子发布指纹用于复用及冻结后核对，旧证据不足不反向补造；原 PEER 最终输出重核时效，Web 使用最终质量；失败尝试时间/脱敏原因与 last-good 分开。安全回归见 [review regressions](../tests/test_u1_review_regressions.py)，不新增服务、表或迁移。

### U2 成交驱动同步与查询（已合入）

- 权威入口：[原 OrderManager](../backend/services/action/order_manager.py) 原子记录成交进度、[原 poller](../backend/services/action/order_poller.py) 事务外调用原 [Executor](../backend/services/restricted_sync.py)、[Repository](../backend/services/broker_sync/repository.py) 完成 Run claim/fence/持仓发布及覆盖水位。IBKR/snowball 与 Tiger Owner 映射不另建账户通道。
- 复用/限制：[fill_sync](../app/fill_sync.py) 记录原订单累计量和已发布 snapshot 的差量证明，不手工改现金或成本；缺基线、原始券商持仓/现金时间水位、实际成交时间、净零往返、实际费用未知/币种不一致和缓存滞后保持未证实，不用抓取时间替代。实际成交状态不因同步失败改变，进程恢复沿原 poller/lease，不自动重放订单。
- PR #25 修复：原 JSON 分离有界资产刷新（4 次成功发布）和未验证覆盖；Tiger 缺少当前适配器证据时明确 UNSUPPORTED/UNKNOWN；新事实/显式刷新或证据能力版本可恢复。已验证历史覆盖保留，当前发布用官方 checkpoint 增量核验，真实回退和冲突仍降级。回归见 [review regressions](../tests/test_u2_review_regressions.py)，无新增 Schema、服务或事实表。
- 查询：[trade_status](../app/trade_status.py)、`GET /api/action/trade-and-sync-status`、`get_trade_and_sync_status` MCP 均不联网/不写库。默认八个只读工具；显式 prepare 为第九工具，原七工具合同不变。订单 ID/批次/标的过滤，只读当前 Portfolio，原始账户/SDK 不返回。
- U1：新冻结视图含待覆盖 issue，旧 ID/payload 不改；不把 SYNCED 与数据实时性或精确调仓授权混同。
- Schema：一列 `order_records.fill_sync_json TEXT NULL`，零索引/零新表；原 stage u2 迁移/receipt/Digest，见 [U2 实现与发布输入](U2_IMPLEMENTATION.md)。
- 测试：[故障/并发/覆盖](../tests/test_u2_fill_sync.py)、[迁移](../tests/test_u2_schema.py)、[MCP](../pocs/chatgpt_skills/test_protocol.py)、[U1 浏览器](../frontend/tests/e2e/asset-view.spec.ts)。无生产/真实交易验收，本批不含完整历史账本或 B2/B3/QMT。


### 国金完整原生持仓来源（已合入）

- 能力/Owner：[原 GuojinSyncService](../backend/services/broker_sync/guojin/sync_service.py)、[Adapter](../backend/services/broker_sync/guojin/adapter.py)、[严格三文件解析](../backend/services/broker_sync/guojin/native_exports.py)，复用原 Executor/Repository/Upsert/FX/Portfolio/Wealth/MCP。旧 MiniQMT HTTP 来源退出；无 Schema 新增。
- 入口：原 broker-sync trigger/status；[原脚本](../backend/scripts/guojin_sync_e2e.py) 默认仅校验，显式发布仍经原 Executor/严格共享契约。文件由既有 GUI 操作生成；已启用午间11:45及收盘后17:30双窗口/按需编排，复用 [Guojin CLI](../backend/scripts/guojin_daily_sync.py) 和原 Mini 定时机制，候选先验再原子 ready、同批次恢复，原v2可观察来源允许仅Upsert正事实并保留未知完整性；金额双上限WARNING不平账。真实worker/候选及受控发布已验收，双次任务已加载；自然运行持续观察，见[结项记录](releases/v4.1.md)。
- 复用：沪港通/深港通底层身份独立，原聚合/issuer 投影合并敞口；CNY 估值和成本，HKD 报价独立证据；旧汇总/截图按显式归属和 CAS 处理，失败不发布。
- 测试：[隔离安全回归](../tests/test_guojin_native_sync.py)、原 restricted/U0 与 [浏览器](../frontend/tests/e2e/guojin-native.spec.ts)。
- 限制：HK 文件无账户号或估值时间，必须依赖私有 GUI 来源收据；mtime 不作报价时间，未知仍 partial；生产同步已有独立验收记录，来源未知时间和完整性不因D1或结项升级。只验证过已提供的原生版本；表头/市场代码变化拒绝发布，不宽松猜测。[完整来源/启用合同](GUOJIN_NATIVE_SYNC.md)。

### D1 当前资产质量（已合入）

- 发行主体 owner：[issuers](../backend/services/instruments/issuers.py)，LI_AUTO / MEITUAN / PDD_HOLDINGS 按证据映射；给出已知中国公司直持风险金额/投资分母、未映射金额及独立成员。基金不穿透，economic_region 不等同注册地或上市地。
- 分类 owner：[原 classifier](../backend/services/instruments/classification.py) / [verified 数据](../backend/services/instruments/verified_classifications.py)，修正 Tiger QQQ STK wrapper；未来正常同步更新投影，不回写旧视图或猜未知证券。
- 时间/资格 owner：[原 capture](../pocs/chatgpt_skills/asset_view.py) / [public_view](../app/asset_views.py)，复用原 Run/FX 时间。A/B/C 分级按相关 symbol 和 analysis_scope 给出具体 blockers；原 bool 对应 C，原交易门不变。[原市场模块](../backend/services/execution_plan/market_hours.py)用本地 session 库识别近期收盘、开市、过期或未知。
- 手工核对 owner：[WealthService](../backend/services/wealth_service.py)，保留主/副表及养老金金额去重，按 value_as_of 提醒并返回原更新入口；无自动贷款余额或养老金关联订正。
- 接口/测试：原 prepare、固定 view、MCP/PEER request 可选 analysis_scope，无新工具/写权限。[D1 合成回归](../tests/test_d1_asset_quality.py)、[固定视图浏览器](../frontend/tests/e2e/asset-view.spec.ts)；[合同及限制](D1_IMPLEMENTATION.md)。零 Schema 迁移；D1已生产交付，来源不提供的时间、真实手工余额和养老关联仍可能partial，按原入口提示人工核实；见[结项边界](releases/v4.1.md)。

### B2 历史事实与收益（已合入）

- Owner：[原 Portfolio 域历史事实/算法](../backend/services/portfolio_history)，复用原 IBKR list_execution_details、Position/Run/订单 Owner、FXService、U1 frozen_input；原当前持仓收益算法不改。
- 来源：显式私有 IBKR Flex XML / 单 Trades 节 CSV EXECUTION；近期 Gateway 永远不冒充完整历史。稳定 execution 身份幂等，费用缺失/异币、历史缺口、修正/Owner 冲突 fail closed，不从持仓反推历史。Tiger/国金原生历史报表待真实样例/权限核实。
- 查询：原 MCP 新增 get_trade_history（只读），Web /api/portfolio/trade-history 和 PEER historical_attribution；按 Portfolio 与已证实账户隔离、返回不透明引用，不接受 raw account_id/Portfolio/导入/刷新/force。固定 ID 使用旧冻结库，不读新导入。
- 复用/限制：系统唯一历史收益法为 long-only 移动加权，与券商原口径对账；不覆盖成本、订单或财富。精确归因/HPR/年化的完整性门限及两表必要性见 [B2](B2_IMPLEMENTATION.md)。不新增任务/订单状态机、现金总账或全组合 TWR/MWR。
- 覆盖/日期：沿原 evidence_json 校验 v2 报表与独立期初附件，纯日期按来源时区整日处理，FX 采用已证实业务/结算日；共同归因截止与旧收盘价时间分离，缺证保持未知，无新增 Schema。合同见 [B2 第 7 节](B2_IMPLEMENTATION.md#7-pr-29-定向修复后的覆盖日期与截止合同)。
- 测试：[PR29 安全回归](../tests/test_b2_review_regressions.py)、[金样及隔离](../tests/test_b2_history.py)、[原视图/PEER/API](../tests/test_b2_frozen_and_api.py)、[原迁移](../tests/test_b2_schema.py)、[协议](../pocs/chatgpt_skills/test_protocol.py)。生产真实历史补源/对账 NOT_RUN，不因本地或 CI 通过标真实历史完整。

### B3 交易体验与产品证据（已交付）

- Owner：原 [link handoff/API](../backend/api/execution_batch.py)、[ConfirmOrderDialog](../frontend/src/components/ConfirmOrderDialog.tsx)；原请求完整参数/Portfolio精确恢复，最终人工操作顺序调用原confirm/submit，报价、版本、资金、WhatIf、风险及B0幂等保持原权威。U2状态在原弹窗展示。
- 产品证据：[instruments/tradability](../backend/services/instruments/tradability.py)及原IBKR/Tiger Adapter只读合约能力；新MCP `get_instrument_tradability`。复用原prepare的受保护loopback bridge查询产品，不触发prepare/同步/交易；未配bridge时原RO worker仅给存储身份及UNKNOWN。
- 三态合同：缺账户级肯定证据UNKNOWN；产品明确禁止买入可NO；当前SDK的肯定合约/WhatIf不授予YES。账户、证券身份、时区、来源、未知费用及实际验收边界见 [B3](B3_IMPLEMENTATION.md)。零Schema/依赖/新服务。
- 测试：[B3隔离](../tests/test_b3_trade_experience.py)、原trade-link浏览器及MCP协议。B3已生产交付；账户/证券权限与费用证据依具体查询判断，不以Mock或合约存在冒充账户可买。

### 4.2 开发：单标证据门槛

沿原 PEER 增加 [证据 Skill](../skills/wp-assess-position-evidence/SKILL.md)（原13项基础上第14项）；[evidence](../backend/services/market_data/evidence.py)负责统一来源/时间、冲突与L1-L4，[output_policy](../backend/services/market_data/output_policy.py)负责原Expressing输出限制。原 [KlineProvider](../backend/services/execution_plan/kline_provider.py) 与 [technical_evidence](../backend/services/market_data/technical_evidence.py)提供可复算日周线，不替换Pattern治理或交易价格/数量Owner。测试：[证据回归](../tests/test_v42_evidence.py)。PR #39 已合并；用户报告已部署，首轮真实投研未通过；实施证据见[4.2记录](V42_IMPLEMENTATION.md)。

4.2阶段目标及情景复用原 [AllocationService](../backend/services/allocation_service.py)，纯计算合同见[allocation_scenarios](../backend/services/allocation_scenarios.py)，回归见[情景金样](../tests/test_v42_scenarios.py)。请求携带未核验版本假设；[服务端目标配置](../backend/services/stage_targets.py)提供候选/受控激活/期限/组合隔离，[本地CLI](../scripts/stage_targets.py)不对外暴露写能力；仅显式配置的有效确认记录进入L3必要条件，不写正式纪律或资产表；单标与组合读取同一固定视图，回款保留投资分母/还贷缩小分母分别计算。

4.2结构化ExitPlan/七层报告由[原PEER组合函数](../backend/services/position_analysis.py)复用上述证据/配置owner；[原Research Repository](../research_v2/repository.py)仅投影事实来源，不以叙事KPI/入库时间充当源事实。原Web/SSE/MCP接口返回可选exitPlan，L4关闭，不连交易准备。测试见[三标/资格/边界](../tests/test_v42_exit_plan.py)，报告及生产待验收见[4.2记录](V42_IMPLEMENTATION.md)。

PR #39 收口：[账户候选](../backend/services/exit_candidates.py)无需手填scenario_legs，复用原计算Owner形成分阶段账户/去向/被动集中比较；[专业解释投影](../backend/services/market_data/output_policy.py)保留原模型引用型定性推论，多入口最终重核。原AV补OCF/CapEx/FCF及字段级期间/披露/估值/抓取时钟；[Web快照适配](../backend/services/snapshot_chat.py)复用U1冻结库及原PEER隔离执行。正式目标未激活、真实三标仍待验收；完成矩阵及CI见[4.2记录](V42_IMPLEMENTATION.md)。


2026-10-23 最小修复：[原 AV adapter](../backend/services/market_data/av_fundamentals_service.py) 独立端点/缓存/时钟，保留收入来源冲突及零 CapEx 阻断；[原 symbol Owner](../backend/utils/symbol.py) 的 position_market_symbol 优先 canonical identity；[原输出 policy](../backend/services/market_data/output_policy.py) 负责技术定性主题、同次候选恢复及无敏感内容诊断，Reviewer 区分新旧合同。未新增 Provider、投研引擎或写权限。脱敏真实来源回归见[冻结夹具](../tests/fixtures/v42_real_research/README.md)及[定向测试](../tests/test_v42_real_research.py)；修复待合并/部署后复验，不宣称三标已通过。

2026-10-27 L1证据链候选：原AV resolver统一Worker/Fundamentals/Kline/Research变量与安全诊断；原证据Owner按字段隔离估值冲突、显式逐季度关联官方披露，并按日/周独立提供L1引用，L3仍需完整证据。官方时钟关联须独立服务端原文核验，卡片自报URL/日期/匹配值不授信；默认无核验owner不补钟。原Research投影区分抓取/入库时钟并有界优选候选；Web/Explain按周期重核。无新引擎或写权限；[实现/源合同及运行待验项](V42_IMPLEMENTATION.md#l1证据链p0p1修复2026-10-27开发候选)，[新增回归](../tests/test_v42_evidence_quality.py)。

### 生成模型配置与 API 兼容（工程候选）

原 PEER/意图/研报/画像/草稿调用统一复用 [llm_config](../app/llm_config.py)，不另建投研引擎或改变业务 owner。Sol medium（可 high）/Luna none 或 low；旧 GPT-4.1 仅显式 legacy profile，API 错误不切换。严格提取仍用原 Responses schema，其余保留 Chat；Embedding/专用搜索单列。入口矩阵、24 次真实 A/B、性能代价、ARM 锁依赖及未验收项见[升级记录](GPT6_MODEL_UPGRADE.md)，专项见[SDK/资格回归](../tests/test_gpt6_models.py)。

MCP 模型配置仅由原 [BusinessClient](../pocs/chatgpt_skills/server.py)/[worker](../pocs/chatgpt_skills/worker.py) 的非交易白名单转发，策略在首个业务导入前固定；更改需重启获授权 worker。原 Key 与 broker/只读开关隔离不变。


GPT-6 发布前观测候选：原统一请求 owner 加入 [安全元数据](../app/llm_observation.py)，原 PEER/线程/worker 用匿名 analysis_run_id 汇总阶段、实际型号、Usage、SDK 重试和版本化参考费用；原 SSE/MCP/Explain 通道传递，隐私抑制和最终证据/交易 owner 不变。无 Schema/日志服务/模型路由扩展。字段、UNKNOWN、SDK 部署交接见[升级记录](GPT6_MODEL_UPGRADE.md#发布前最小观测收口2026-10-23待合并候选)，专项见[观测回归](../tests/test_llm_observation.py)。PR #42已合并，用户报告38c3faf部署PASS，真实三标仍BLOCKED；不把metadata当业务验收。验收专用[入口](../scripts/v42_peer_acceptance.py)复用原PEER，使用[发送前预算](../app/llm_acceptance.py)的同一私有并发账本，不改此事后观测owner。LI一次性显式复验复用同一账本/锁/累计上限，v1兼容、v2受控扩展且损坏拒绝，不新建预算；[复验回归](../tests/test_v42_li_retest.py)。未知网络/收费路径拒绝，源条件和固定视图方案见[4.2记录](V42_IMPLEMENTATION.md#最终真实验收阻断定点收口2026-10-23独立-pr-候选)，[专项回归](../tests/test_v42_acceptance_budget.py)不替代真实调用。

正常 MCP 错误诊断复用 [server](../pocs/chatgpt_skills/server.py)、[Worker](../pocs/chatgpt_skills/worker.py) 与[原只读事件入口](../pocs/poc02_wealth_mcp/server.py)，[闭合投影](../pocs/chatgpt_skills/diagnostics.py)只传匿名 ID、阶段、协议/业务状态、耗时和有证据的模型调用统计；未知为 UNKNOWN。无新工具/数据库/业务计算/付费调用，原 L1–L4/人工交易权限不变。专项见[正常 MCP 协议回归](../pocs/chatgpt_skills/test_v42_protocol.py)；部署与真实验证仍待单独授权，见[4.2说明](V42_IMPLEMENTATION.md#正常-mcp-单标错误诊断2026-10-27待合并候选)。

正常ChatGPT单标长研究：原[server](../pocs/chatgpt_skills/server.py)的`start_position_analysis` / `get_position_analysis_result`复用原只读Worker/PEER/AnalysisResult与安全观测；[PositionJobs](../pocs/chatgpt_skills/position_jobs.py)仅管理有界进程私有登记/结果。客户端唯一ID在进程内幂等，旧scope格式兼容且不作门槛；跨重启不保证去重，未知job查询不执行业务或自动重跑；完整原报告保留两小时。无新平台/数据库/模型/Provider/权限，原同步入口兼容、原11工具保留。测试见[登记](../tests/test_position_jobs.py)、[真实隔离HTTP/Worker](../pocs/chatgpt_skills/test_v42_protocol.py)；生产与真实质量仍待授权验收，见[4.2边界](V42_IMPLEMENTATION.md#正常-mcp-长研究异步取回2026-10-27待合并候选)。

### 4.3：E1 人工投资假设、复核与本地待办发现

Owner：WP [investment_thesis](../backend/services/investment_thesis/service.py)；[store/types](../backend/services/investment_thesis/store.py)、[CLI](../backend/services/investment_thesis/cli.py) 实现显式私人研究库、不可覆盖版本/差异、支持/反对claim、原PEER结果副本与Review持久化。[纯敞口](../backend/services/investment_thesis/exposure.py) 复用U1/发行人和已核阶段目标，缺证未知、ETF不穿透；不维护第二套账户或风险规则。

原MCP显式配置E1库时增加 Thesis/Review [两项只读查询](../pocs/chatgpt_skills/investment_thesis.py)；N1候选再增加一项只读待复核任务查询。独立研究写开关另增加预览/确认保存两项。无配置原工具不变；原业务库/Worker边界不变，没有券商/订单或新的模型。B0/B1/B2/B3/N1不作为独立产品版本。B1实现和合成端到端测试见 [4.3开发记录](v4.3/E1_B1_IMPLEMENTATION.md)。

E1-B2 已用三种真实公开事件完成私人 CLI 记录/版本/Review/本地只读 MCP 重启读回；三份实际原 PEER 报告均 partial，经 CLI 导入仍为未认证副本，不代表买入资格或线上 E1 可用。未定期限的逾期状态保留 UNKNOWN；实际边界、最小修复与测试见 [B2 本地验证](v4.3/E1_B2_VALIDATION.md)。

B3-1新增 [ResearchBridge](../backend/services/investment_thesis/bridge.py) 与原 [MCP适配](../pocs/chatgpt_skills/investment_thesis.py) 的可选预览/明确确认研究保存。显式E1私人库+独立写开关才注册；默认原工具/业务权限不变。复用原store事务、CAS/幂等和Review降级，不调用Provider/同步/订单。日常用法、令牌过期/重启及客户端确认责任见 [B3-1记录](v4.3/E1_B31_RESEARCH_SAVE.md)；测试见 [确认/原子性](../tests/test_e1_save_bridge.py) 和 [独立MCP协议](../pocs/chatgpt_skills/test_e1_save_protocol.py)。

E1-I1已于2026-10-29完成私人ChatGPT线上真人预览确认保存、只读回查和MCP受控重启后持久化读回；实际工具为原13项加E1四项。该验收不升级PEER partial、真实敞口或交易资格，也不免除每条记录的当次确认。发布计划与边界见 [I1集成记录](v4.3/E1_I1_INTEGRATION.md)。

B3-2本地候选新增 [ReviewMonitorStore](../backend/services/investment_thesis/monitoring.py) 和 [手动CLI](../backend/services/investment_thesis/monitoring_cli.py)：显式迁移同一私人E1库后，绑定当前Thesis revision及已完成Review的复核时间/正式事件条件，生成确定性去重的待办收据。冻结SEC fixture验证发行人、发布时间/可知时间、原件哈希及同一经济事件去重；版本推进、关闭、取消、未来/身份不明事件不触发。扫描不运行PEER/Provider，不写Thesis/Review，不通知或交易。生产调度OFF、主动通知`NOT_WIRED`；测试见 [B3-2回归](../tests/test_e1_b32_monitoring.py)，启用边界见 [B3-2记录](v4.3/E1_B32_REVIEW_MONITORING.md)。

PM Review后的单PR、Mac Mini安装、E1 v1→v2迁移、到期实测与最小SEC/通知路径统一见 [E1最终发布计划](v4.3/E1_FINAL_RELEASE_AND_ACCEPTANCE.md)。该计划不代表生产授权，初始自动扫描继续OFF。

N1已发布 [有界SEC自然扫描](../backend/services/investment_thesis/natural_scan.py)：只处理显式opt-in monitor，首次水位不回放，复用原SEC身份/原件核验，源失败隔离且不冒充无事件；schema v2不变。`get_due_investment_reviews`只读返回稳定event key和有界分页，提供at-least-once提醒去重输入，不改变任务或研究记录。PR #59时钟修复已发布：保留真实`fetched_at`，仅以SEC acceptance/known clock判断未来信息。2026-10-29验收收据记录运行SHA `4e4fdb4ea0831eb079cf96518f91d4397e326e05`、SEC实际读取及首次水位通过、跨进程无重复任务；原18项工具、E1 v2及既有canonical记录不变。Scheduled task私人连接与TEST ONLY提醒此前已通过，但水位后真实自然新事件仍待观察，状态为`E1_CORE_RELEASED_N1_NOTIFICATION_PASS_NATURAL_NEW_EVENT_PENDING`，不是`E1_RELEASED`；验收时`AUTO_SCAN=OFF`、WealthPilot通知`NOT_WIRED`，临时monitor已取消，本轮未连接生产复测。当前证据边界见[统一映射](v4.3/UNIFIED_INVESTMENT_DECISION_ARCHITECTURE.md#7-生产证据与现状差异)；测试见 [N1回归](../tests/test_e1_n1_natural_review.py)，历史发布计划见 [N1记录](v4.3/E1_N1_NATURAL_REVIEW.md)。

### 4.2 第一轮质量修正（开发候选）

复用原 [SEC/披露 Owner](../research_v2/sec_disclosures.py)、[表格解析](../research_v2/official_disclosures.py)、[周期证据](../backend/services/market_data/technical_evidence.py)及 [Skill 工具](../backend/graph/tools.py)。通用正式附件标题/断行字段可进入原 Evidence Registry；日周线按各自完整窗口校验，真实缺口给出日期；默认纪律与关键词信号不再声明个人纪律或已验证基本面 PASS。原风控数值、PEER 和交易权限不变。范围与未恢复项见[来源说明](V42_US_FINANCIAL_SOURCES.md#第一轮集中质量修正-p0)。HK 官方财报/半年度合同仍缺失，不将 AKShare 公司概况当作财务；生产实际 K 线缺口须取得原日期集再核验。

正常异步L1可靠性候选：原[PEER阶段回调](../backend/services/decision_service_v3.py)、[分析适配](../pocs/chatgpt_skills/analysis.py)、[一次性Worker](../pocs/chatgpt_skills/worker.py)/[父进程](../pocs/chatgpt_skills/server.py)共用[小型运行期限](../app/analysis_runtime.py)，SDK重试计入同一deadline；已有合格事实按原Evidence/Output Policy纯内存重核并复用确定性报告，模型失败可返回明确facts-only partial。无新队列/数据库/研究引擎/资格或交易权限，原E1与同步入口保留。机制、时间策略、事件计数与生产待验收见[原4.2记录](V42_IMPLEMENTATION.md#正常异步-l1-可靠性2026-10-29-开发候选)；专项见[可靠性回归](../tests/test_l1_reliability.py)和[真实Worker/IPC](../pocs/chatgpt_skills/test_v42_protocol.py)。

P1研究内容候选：[原SEC表格/标准事实](../research_v2/sec_disclosures.py)补充明确营业利润、归母利润及每ADS/普通股稀释EPS；[原Evidence](../backend/services/market_data/evidence.py)单列历史可比事实，不改当前字段TTL或L2–L4。[财务研究纯投影](../backend/services/market_data/financial_research.py)复用原AV窗口/求和，按币种/口径/期间计算变化与有条件的静态估值，FX仍归原FXService。原[Output Policy](../backend/services/market_data/output_policy.py)用证据字段绑定渲染数字；[ExitPlan](../backend/services/position_analysis.py)增加研究性条件/比例/期限，不提供执行股数或权限。无新数据Owner、存储或Agent；合同及限制见[财务来源说明](V42_US_FINANCIAL_SOURCES.md#p1-专业研究扩展2026-10-29-开发中)，回归见[P1](../tests/test_p1_research_quality.py)。

P1.1定点修复仍复用上述原财务投影/SEC表格/Output Policy：同原文比较列优先且保留原值差异，精度不是任意容差；明确列币种及EPS分组变体、绑定诊断及普通排版支持。无新Owner/Provider/权限，见[原V42记录](V42_IMPLEMENTATION.md#p11-多披露比较与数字解释2026-10-29-开发候选)及[真实多来源回归](../tests/test_p1_1_research_quality.py)。

基础估值正向链 P1 本地候选仍由[SEC原 Owner](../research_v2/sec_disclosures.py)、[正式表格解析](../research_v2/official_disclosures.py)、[Evidence](../backend/services/market_data/evidence.py)和[financial_research](../backend/services/market_data/financial_research.py)共同完成：正式8-K/EX-99.1的GAAP稀释EPS以CompanyFacts年度/前季事实证明非日历Q4期间，原日线Evidence核验价格，原`build`确定性输出四季EPS、历史PE和盈利收益率。AAPL/NVDA真实原文禁网回归及开发侧冻结复算见[来源说明](V42_US_FINANCIAL_SOURCES.md)；不代表生产研究、内在价值或交易资格。

P2汽车经营指标候选沿原[SEC来源](../research_v2/sec_disclosures.py)、[官方表格语义](../research_v2/official_disclosures.py)、[Evidence Registry](../backend/services/market_data/evidence.py)和[financial_research](../backend/services/market_data/financial_research.py)运行，复用[Output Policy](../backend/services/market_data/output_policy.py)最终数字绑定；明确季度交付/汽车收入/车辆毛利率缺源即UNKNOWN，汽车收入/交付量只为近似。LI与XPEV真实完整SEC原文均覆盖离线来源资格回归；XPEV封面同一表格行分列的EX-99.1声明按精确附件身份识别。该回归不代表生产自动发现或真实PEER验收。口径与边界见[4.2实现记录](V42_IMPLEMENTATION.md)。
