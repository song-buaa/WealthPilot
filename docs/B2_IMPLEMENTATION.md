# B2 历史成交与收益归因

## 1. 基线与定向 Gap Matrix

基线：fetch 后 `origin/main` = `462fc61668709971e27794bdeac486788423aae4`。用户确认 D1 已部署，本轮不访问生产。B2 是本 PR 候选，代码/Mock/CI 不证明真实历史已补全；不开发 B3 或全组合 TWR/MWR。

| 能力 | 分类 | 原 Owner / 代码证据 | 最小实现与 Schema |
| --- | --- | --- | --- |
| 订单与 execution 关联 | EXISTING / EXTEND | [OrderRecord](../backend/services/action/models.py) 的 owner/key/raw/fill_sync_json；[IBKR list_execution_details](../backend/services/action/brokers/ibkr.py) 已按 acctCode 查询并再次过滤账户，含 execId/permId/orderRef/shares/price/time/commission | 复用原 Adapter，补 detached sec_type/multiplier；订单累计量不是逐笔历史，不改原订单表 |
| IBKR 超窗口历史 | IMPORT | 原 Adapter 只提供 Gateway 有限近期窗口；全仓无 Flex 历史持久化 | 严格 Flex XML / 单 Trades 节 CSV EXECUTION 导入；两张最小事实表 |
| Tiger 历史 | DEFER | tigeropen 3.5.8 本地 TradeClient.get_transactions 为 prime 账户接口、带分页；get_filled_orders 是订单级；现有 [Tiger Broker](../backend/services/action/brokers/tiger.py) 未提供原生逐笔历史合同 | 不把订单均价拆成 execution；实际账户权限、交易/费用字段及历史报表样例待验；本批不另建适配器 |
| 国金历史 | DEFER | [三文件解析](../backend/services/broker_sync/guojin/native_exports.py) 是当前持仓/资金而非历史成交 | 不把持仓或截图反推买入；后续需真实逐笔历史导出和身份合同 |
| 当前成本/参考盈亏 | EXISTING | [Tiger](../backend/services/broker_sync/tiger/adapter.py) 读 SDK average_cost/unrealized/realized；[IBKRPortfolioAdapter](../backend/services/broker_sync/snowball/adapter.py) 读当前 average_cost；[PositionSnapshot](../backend/services/broker_sync/models.py) 有成本法标签 | 仅对账，不覆盖 Position 成本或券商参考数值；adapter 成本法标签不等于本轮已核实真实券商会计方法 |
| 当前收益分析 | EXISTING / EXTEND | 原 [Portfolio](../backend/services/portfolio_service.py)、[analysis captured_evidence](../pocs/chatgpt_skills/analysis.py) / decision_engine 仅当前持仓账面表现 | 保留；新增独立 historical_attribution 证据，不修改 decision_engine |
| 移动加权、历史归因 | EXTEND | 没有可信完整历史计算 Owner | 在原 Portfolio 业务域 [portfolio_history](../backend/services/portfolio_history) 增加唯一历史算法；不影响财富统计 |
| 历史 FX | EXISTING / EXTEND | [FXService.get_quote](../app/fx_service.py) 已支持指定日期及 historical 质量 | 仅显式导入阶段补源，实际 rate_date 必须等于资金流日期；缺失保持未知，无新 FX 服务 |
| 覆盖/期初/分红 | IMPORT | Run 是持仓发布证据，不能证明历史无遗漏；原固定视图可冻结同一数据库 | 导入证据表记录来源/时间窗/人工核对证据；真实分红逐笔，缺源阻断相应结论 |
| 历史 6 笔 Owner 缺失 | FIX（查询边界） | 新 [owner_scope](../backend/services/portfolio_history/imports.py) 复用已有 Position/Run/显式订单归属；NULL 不作证据 | 本轮仅合成六笔 NULL owner 测试；生产实际数量/映射未调查。不认领 Portfolio 1、不改原订单。账户有独立归属证据时可读报表事实，原订单关联保持 NULL |

没有发现需另建订单/持仓/现金或任务平台的架构阻断。风险按 L3 Schema/历史金额质量处理，真实导入、迁移、部署另行授权。

## 2. 事实、导入与权限合同

权威：[sources](../backend/services/portfolio_history/sources.py) → [imports](../backend/services/portfolio_history/imports.py) → 两张事实表 → [query](../backend/services/portfolio_history/query.py) / [calculation](../backend/services/portfolio_history/calculation.py)。不从 OrderRecord 的平均价/当前持仓反推 execution；不调用 place/cancel/replace。

- 执行主键是 broker + account + kind + external_id（执行使用真实 ibExecID/execId）；同单多次部分成交分别保留。相同事实幂等；已知数量/价格/时间/身份或费用冲突整批回滚。缺失佣金、税费、原订单引用或历史 FX 可由后续报表补充，保留导入证据。IBKR correction suffix/原交易修正拒绝自动处理，避免将修正当新增成交。
- XML 按 FlexStatement 的真实 accountId 过滤；选中账户内的混合/缺失账户证据拒绝。CSV 支持一段 Trades 表，要求稳定 execution ID、明确 EXECUTION 级别；不是通用报表猜测器。XML 外部实体/DTD、过大/畸形数据、订单/closed lot 行拒绝。当前支持 UTF-8；真实其他布局先补隔离样例，不宽松猜测。
- UTC 规范化保留执行实际时间；无时区的 Flex 时间必须显式提供已确认的 source_timezone，DST 重叠/不存在时间拒绝。分红可保留日期精度，不伪造瞬时时间。coverage_end 为排他边界，原报表日期边界按来源时区转换校验。
- coverage 的 complete、opening_zero、corporate_actions_reviewed 来自操作员核实的完整报表/期初证据及私有 evidence_ref，不因记录缺失、查询成功或空账户而自动授予。按账户或 instrument_keys 限定；区间缺口、字段/现金流不支持、后续新增回溯事实推翻旧覆盖声明时降级。每个导入保存事件身份集合，不能用旧完整报表为后来缺失事实背书。
- 历史近期采集调用原 IBKR Adapter，先校验配置账户及已有归属，始终 partial；不宣称 Gateway 已覆盖开户至今。实际可回溯窗口受 Gateway/TWS/版本配置影响，代码不假定固定天数。
- 显式 owner 为既有 Position / SnapshotRun / OrderRecord 中非 NULL 的同一物理账户与唯一 Portfolio；ibkr 交易和 snowball 持仓别名沿用。不同 Portfolio 证据冲突拒绝。历史已清仓账户可由原 Run/显式订单证明；无证据则待人工核对，不新建账户库。
- 私有报表/coverage 文件只在仓库外、0600 读取。DB 事务开始前完成解析/FX/券商读取；短 BEGIN IMMEDIATE 内再次核对 owner、执行去重和事实冲突，一次提交。异常只输出安全原因码，不回显账户、原始异常、凭证或文件内容。

[IBKR 官方执行/佣金说明](https://interactivebrokers.github.io/tws-api/executions_commissions.html)区分逐笔成交与佣金回报及受配置影响的近期窗口。[Flex Trades 字段说明](https://www.ibkrguides.com/reportingreference/reportguide/tradesfq.htm)区分 execution、order、closed lots，提供 execution ID、实际佣金及币种；原报表盈亏口径与系统法分开。[Tiger 原生历史入口](https://quant.itigerup.com/openapi/en/cpp/operation/trade/tradeList.html)不能替代实际账户/报表验收。

## 3. 成本与收益语义

所有数量/原币金额为 Decimal，JSON 使用十进制字符串；不经过二进制 Float 计算交易成本。当前 Position 原有 Float 投影只作单独对账。Flex 佣金/税是带符号现金影响，规范化成本取反号；Gateway commission 是原正成本，返佣不取绝对值。

- 默认系统法为 long-only 移动加权：买入增加价款与真实费用成本，卖出按当时平均成本结转，卖出实际费用扣减一次。买入费用分配到已售/剩余成本。零/负费用按真实返佣保留；缺佣金/税费或异币费用时净成本/净收益保持 NULL，不把估算 WhatIf 代入。仍可输出已证实的原币毛价格收益和逐笔事实。
- 真实 broker_cost_basis、fifoPnlRealized 及当前 broker average_cost/reference P&L 独立呈现为券商口径；系统移动加权与其差异明确返回，不强行调平。衍生品、非 1 multiplier、卖超已知多头、缺顺序证据、缺期初/连续覆盖不计算可信已实现收益。
- 未实现收益还要求同证券/账户/币种、数量对账、U0 发布指纹、D1 行情来源质量及可排序的持仓/价格时间，历史覆盖到估值时点；不能用 fetched_at 冒充。清仓历史可在已证实成交窗口内以终值 0 计算，不宣称未来仍零持仓。
- 只有真实现金流才计分红、预扣税或利息；累积型 ETF 净值增长属于价格，不另计息。无法证明独立于成交费用的其他费用/现金流保留证据并阻断总收益，不重复扣减。净归因分价格、分红、收入税、利息、其他现金流、真实交易费用及残差。
- 多次资金流入/流出只可提供明确命名的 profit_on_cumulative_outlay，绝不称为 MWR/TWR。holding_period_return 仅允许一个初始资金日期、无中途提款/分配、完整现金流及有效终值；annualized_return 另要求至少 365 天及可定义正终值。其余返回具体原因，不建全组合 MWR/TWR 引擎。
- FX 导入只复用原 FXService 指定日期，实际来源日期和 historical 质量均匹配才存入；查询不联网、不自动用今日 FX 补历史。CNY 总收益要求每笔原币现金流及终值有日期证据。单次初始资金使用首笔汇率桥接，FX 分量包含交叉项；多日期流入流出 FX 归因暂不分解，明确返回缺口/残差。

## 4. 查询、MCP、PEER 与固定视图

新增原只读工具 `get_trade_history(request)`：account_ref（不可逆历史账户引用）、symbol 或 conid、start/end、limit 1–200、offset、可选 snapshot_id。按配置的 Portfolio 过滤；客户端不接受 raw account_id/portfolio_id/refresh/import/force。每次查询同一只读 SQLite 事务，最多 10000 个事实、200 个证券组；超限明确拒绝。分页只裁显示事实，成本仍从已知期初计算，不能从用户筛选起日重置成本。

返回逐笔执行/现金流、实际佣金、broker/local order 关联、来源/导入/执行时间、分账户/证券/币种的收益和覆盖区间。原始账户、orderRef、evidence_ref、原异常不返回。历史 account_ref 按 Portfolio/broker/物理账户派生，持仓清仓后仍稳定，与原投资展示的平台分组引用区分；它不授予权限，授权仍核对原 Owner 及已证实导入绑定。费用补源保留逐字段证据 import_id 与来源 SHA。本地订单关联还要求同 Owner、同证券及方向，歧义保持 NULL。原八项只读工具保持，新增后默认九项只读；独立 prepare 启用时共十项，权限不变。Web 复用 `GET /api/portfolio/trade-history` 同一查询，不开发新历史交易页面或写 API。

有 snapshot_id 时沿原 `load_view/frozen_input` 读冻结数据库，不修改 payload 或新建快照体系；旧视图缺历史表时返回 HISTORY_SCHEMA_NOT_AVAILABLE。新导入不改已冻结历史证据。原 PEER 旁路证据 `evidence.historical_attribution` 来源同一数据库，问题上下文仅传前 20 组、说明范围截断；原 current-holdings performance 计算/decision_engine 不改，也不将历史估算冒充券商确认。

## 5. Schema、生产迁移与回滚

必要性：订单表不能表达同单多个 execution；Run 不能证明历史覆盖。仅增加：

| 表 | 字段职责 | 索引 |
| --- | --- | --- |
| portfolio_history_imports | id/Portfolio/broker/account；source/digest/imported_at；coverage_start/end；evidence_json 保存文件 SHA、覆盖/期初/过滤范围、事件身份与来源问题 | uq_history_import(broker,account_id,source,digest) UNIQUE |
| portfolio_history_events | id/Portfolio/broker/account；kind/external_id；instrument_key/symbol/currency/executed_at；import_id 外键；payload_json 保存 Decimal 事实、真实费用、历史 FX/报告参考及补源证据 | uq_history_event(broker,account_id,kind,external_id) UNIQUE |

共 2 表、2 个显式唯一索引，0 个既有字段/索引修改；主键隐含 SQLite 索引另计。不是订单状态机、余额账本或持仓库。无新依赖。

复用 [原迁移脚本](../scripts/migrate_b0_safety.py) 的显式 `--stage b2` 与 [严格形状](../app/b2_schema.py)，不新建迁移框架。共享模式启动只校验不 DDL；旧严格 receipt 需经显式迁移更新 digest，不手工改值。

后续另行授权的发布顺序：

1. 核对固定 main ancestor SHA 与 exact-commit 四项 CI、当前 Owner/组件/契约，记录旧入口。
2. 停写并协调 MCP/Backend/读取；使用原 `plan --stage b2 --database … --contract … --plan …`，克隆演练仅建空事实表、验证目标 Digest/唯一索引和原数据 inventory。
3. 原 `apply --stage b2` 指定全新私有 backup/receipt 并 `--writers-stopped`；备份与 inode 保留、短事务目标核验。中断保留停写，按原 resume-contract 核对精确目标，不能猜修 Digest。
4. 统一 Backend/MCP/分析 worker 固定 SHA，复用原环境与权限；重新启用写入前原 mark-writers-enabled 收据必须落盘。无 Web 源码变化，无交易权限变化。
5. 先验健康/契约和原功能，再另行授权私有历史导入。输入可以默认 dry-run：`python scripts/import_trade_history.py --database … --contract … --portfolio-id … --account-id … --flex-file … --coverage …`。真实账户/文件名不得贴公开日志；无 `--apply` 不写库，`--recent-ibkr`/`--historical-fx` 额外读取也须单独授权。覆盖 JSON 须含明确来源时区、UTC 起止、account/instruments 范围及已核实 evidence_ref；不能把 opening_zero/complete 当免核验开关。
6. 抽样核对 IB01/CBU3 原始 execution、分批、佣金币种、报表成本法、分红/税及日期，先补齐开户/期初到估值的区间。已缺 owner 的 6 笔保留原订单，只有独立账户/Portfolio 证据充分才建立查询关联。Tiger/国金原生历史样例未验不标启用。
7. 实际 Web/MCP/PEER 同一 snapshot 对账，区别当前浮盈、历史已实现、持有期及未知；ChatGPT 既有连接器刷新目录后新会话验收新只读工具。

回滚：未恢复写入且 inventory 未变时才可原 restore-prewrite；产生任何新事实后禁止旧备份覆盖。优先回滚兼容组件、保留新增表及更新后严格契约，协调旧读取端确认可忽略新表；不要自动 drop 历史事实或反向删除。源码回滚不撤销券商订单。所有生产动作本轮 NOT_RUN。

## 6. 隔离证据入口及待验收项

- [历史金样/并发/冲突/覆盖/权限](../tests/test_b2_history.py)：分批买入/部分卖出/清仓、费用/FX 未知、修正、回溯事实推翻旧完整声明、跨市场和六笔 NULL owner。
- [原固定视图/API/PEER](../tests/test_b2_frozen_and_api.py)：费用后补不会修改旧视图；Web/MCP 读结果一致，原资产统计不变。
- [显式 Schema 演练](../tests/test_b2_schema.py)：精确 2 表 2 索引、原收据/旧数据、克隆目标、幂等、中断恢复和新事实后禁止备份覆盖。
- [原 MCP 协议](../pocs/chatgpt_skills/test_protocol.py)：实际工具目录、原权限、历史金样、脱敏和拒绝客户端导入/越权参数。

本轮仅合成事实和隔离库；真实执行日期、费用、Flex 布局/时区、历史完整性、真实 Owner、分红/FX 覆盖及 ARM/生产组件仍需发布后独立验收。历史数据不足是可解释 partial，不以清除 partial 为交付标准。
