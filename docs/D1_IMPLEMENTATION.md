# D1 资产数据质量收尾

开发基线为 fetch 后的 `origin/main`：`b4a5047fab1b4d53d46729f98c752b62bb1fd290`。本批 L2，仅代码、隔离测试和 PR；不部署、不触发生产 prepare/同步、不修改余额、订单或交易设置。需求权威为用户提供的 2026-10-20 v1.0 任务说明；本文记录实现与验收，流程仍以 [PROJECT_GOVERNANCE](PROJECT_GOVERNANCE.md) 为准。

## 1. 定向 Gap Matrix

| 需求 | 分类 | 代码 owner / 原缺口 | 最小实现 / 人工边界 |
|---|---|---|---|
| P0-A issuer | EXTEND | [issuers.exposure](../backend/services/instruments/issuers.py) 原先只有 LI_AUTO；美团/PDD 落入 unmapped | 同一映射补 MEITUAN/PDD_HOLDINGS；按标准证券身份、币种和原生来源标识匹配。返回已映射中国直持股金额/比例、分母及未映射金额，已知中国敞口明确是下界 |
| P0-A 分类 | FIX | [TigerAdapter](../backend/services/broker_sync/tiger/adapter.py) 把所有 STK 当普通股；冻结基准 QQQ 被标 COMMON_STOCK | 在[原 verified classifications](../backend/services/instruments/verified_classifications.py) 增加有官方证据的 Tiger/QQQ:US/USD 限定记录；冲突 ISIN 拒绝。其他无证据产品仍 UNKNOWN，不建第二套分类 |
| P0-B 来源时间 | FIX / EXTEND | [Tiger](../backend/services/broker_sync/tiger/sync_service.py) 未消费 PortfolioAccount.update_timestamp；[IBKR get_account_info](../backend/services/action/brokers/ibkr.py) 用本地读取完成时间填 cash_as_of | Tiger 仅以 SDK 服务器资产更新时间填现金时间；证券时间仍 UNKNOWN。IBKR 本地 account_queried_at 与未知 cash_as_of 分开，不更改执行资金读取/TTL |
| P0-B 估值质量 | EXTEND | [asset_view.capture](../pocs/chatgpt_skills/asset_view.py) 原证券估值仅按自然日/秒阈值判断 | [原 market_hours](../backend/services/execution_plan/market_hours.py) 增加只读估值判断，使用本地 exchange-calendars；原交易触发判断不变 |
| P0-C 手工财富 | EXTEND / MANUAL_CONFIRM | [WealthService](../backend/services/wealth_service.py) 原 freshness 以编辑时间计算；旧有效余额可能显得新鲜 | 以 value_as_of、确认及记录时间的保守时效判断；原列表增加 quality_issues/review_required/update_path，U1 给出金额、日期、原因。真实贷款本金/银行余额须用户复核，不自动改值 |
| P0-C 养老关联 | EXISTING / MANUAL_CONFIRM | 原 _manual_totals 已按 included/already_accounted 重分类/扣除，holding_link_verified=False | 保持原金额口径及主表/副表，不按名称自动匹配。未证实关联继续 PENSION_HOLDING_LINK_UNVERIFIED，给出人工清单 |
| P0-D 资格 | EXTEND | [public_view](../app/asset_views.py) 原先仅 can_propose_rebalance=not issues | 同一个冻结输入上的 A/B/C 分级及逐项 blockers；原 bool 对应 C。按相关证券/分析用途判断，PEER 开始/输出均重核 |
| P1 现金/FX/同步 | EXISTING / 回归 | 原 U0 Run/claim/fence/FX、U2 fill_sync 仍为权威 | 不增加同步、现金、收益或任务体系。保留原币、BASE 排除、Last-good；无日期、fallback 或无效 FX 不获精确资格 |
| B2/B3、全量穿透 | DEFER | 不是当前资产质量缺口 | 不实现历史收益、实际佣金归因、交易 UI 重构、新账户或基金穿透 |

证据分层：已读取 Mini「4.1 部署」会话的国金发布验收记录，记录报告 Backend/Web/MCP 为上述 SHA、国金 Run 177 成功；附件记录后续三券商 Runs 178/179/180。本轮没有重新探测生产进程/券商；这些属于已有发布/业务证据，不能替代 D1 的未来生产验收。通过现有只读 MCP 取得 `asset-view:v1:56`，其原始数据与金样校验结果保存在仓库外私有目录；未调用 prepare，也未改写旧视图。

## 2. 唯一权威与合同

- 同步仍为原 SyncExecutor → SnapshotRun/Repository → Upsert；不修改 claim/fence、账户隔离和原币事实发布。
- 发行主体只合并 CNY 市值风险。理想美股/港股、国金 HGT/SGT 成员的账户、证券、通道、数量、成本和币种独立保留；不做 ADS 换股。ETF/基金不列为单一企业，也不按上市地猜 economic_region。
- 新 issuer 证据：[美团年报](https://www.hkexnews.hk/listedco/listconews/sehk/2024/0429/2024042900115.pdf)、[PDD 上市身份](https://investor.pddholdings.com/information-investors)、[PDD 业务及风险披露](https://www.sec.gov/Archives/edgar/data/1737806/000110465926050727/pdd-20251231x20f.htm)。economic_region 是本项目经济风险归属标签，不是注册地/交易所或收入地域穿透；PDD 的跨国业务不被描述为 100% 中国收入。
- [QQQ 官方产品资料](https://www.invesco.com/us/en/financial-products/etfs/invesco-qqq-trust-series-1.html)证明其 ETF/权益指数属性；未来正常同步通过原 canonical classifier 更新当前投影，不回写历史 Snapshot 或旧固定 payload。
- source `fetched_at`、`queried_at`、account_synced_at、positions_as_of、price_as_of、cash_as_of、FX rate_date 各自保留。Tiger 3.5.8 原 PrimeAssetsResponse/PortfolioAccount 将 updateTimestamp（毫秒）解为 update_timestamp；该字段只用于现金资产更新时间，不能证明证券报价时间。[SDK 对象合同](https://docs-en.itigerup.com/docs/appendix-object-detail)。无值/异常/未来值保留 UNKNOWN。
- IBKR accountDownloadEnd 证明本次读取完整，不证明 CashBalance 原始有效时间。只读账户摘要新增 account_queried_at，cash_as_of 无权威证据时为空；复用 pre-D1 成功 Run 时，原公开质量投影也去除旧本地下载时钟的来源资格，不改 Run 或旧固定 payload；原交易专用 get_cash_snapshot 及资金/报价 TTL 未放宽。U2 若因此缺现金时间仍返回未验证，不能伪造覆盖。
- 国金 GUI 查询时间继续仅作 queried_at；TXT 保存时间不变成估值时间，原生文件无法提供的持仓/现金/行情时间明确 UNSUPPORTED。
- 每个新固定 payload 在原 data_quality 中增加 policy_version=d1.1、security_inputs（来源/时间/FX/估值质量）、manual_review_required、analysis_eligibility、qualification_scope。Web/MCP/PEER 都复用原 public_view，不重新计算金额或持仓。

## 3. 估值与分析资格

`valuation_quality` 使用版本固定的本地 exchange-calendars（XNYS/XHKG/XSHG/XLON），处理 DST、假日、半日市；无网络调用，不改变原交易时段函数。日历范围外、未知市场、无时区或无来源时间均 UNKNOWN；不以工作日猜休市。[库的 session 合同](https://github.com/gerrymanoim/exchange_calendars/blob/master/exchange_calendars/exchange_calendar.py)。

- intraday：来源时间位于当前交易时段且距判断不超过 300 秒。
- recent_close：休市时，来源时间位于最近已完成交易日收盘前后 300 秒；仅日期精度必须另有 price_time_basis=close，日期等于最近已完成 session。周末/假日不按自然日误报 stale。
- stale：时间可信但不满足上述时效，或未来时间；unknown：来源/市场/时间语义无法证明。抓取新鲜不改变价格状态。

资格是用途相关的**分析数据条件**，不是交易安全校验。保留所有原 issues，不以删除告警获资格：

| 等级 | 条件 / 阻断 | 允许范围 |
|---|---|---|
| A 战略配置 | 有相关资产金额；金额无效或已知发布事实冲突时阻断。未知、旧行情、未映射公司、手工项目均披露 | 可解释比例、集中度与风险方向；未知敞口不假设为零 |
| B 金额规划 | A 的事实条件；重要资产分类/FX/行情估值须合格、发布可核对，成交待同步不得隐藏；整体公司敞口规划缺重要 issuer 时阻断；到期上下文不可用 | 允许大致金额，不承诺精确数量/限价。最近收盘可用于此级；持仓来源时间不足仍需明确说明 |
| C 精确参数 | 相关证券价格/数量有效、intraday，券商 CNY 市值反推的单位价不充当 HKD 限价；来源持仓/本账户现金时间、最近成功同步和冻结视图均在 300 秒内；FX 有效且 current；无待同步/发布冲突。30 分钟副本有效期不等于行情新鲜度 | 允许形成具体参数建议，仍必须通过 B0 原 live quote/WhatIf/资金/风险/Web 人工确认 |

重要性规则明确为选定投资资产**绝对市值总和的 1%**；仅用于 B 级重要未知项判断，所有金额仍保留和披露，不能豁免目标证券的 C 级条件。这是版本化、明示的分析政策，不是 force 开关。单标相关证券仍需其自身账户现金；其他账户的旧现金不越权授予或阻断该单标资格。

PR #27 定向修复政策（原 qualify_view 负责，不增加服务/Schema）：

- **B 级重要现金上限为 24 小时**，不同于 C 的 300 秒。最近成功券商读取必须在 24 小时内；存在明确 cash_as_of 时，该来源时间也必须在 24 小时内，不能用新 Run 或 captured_at 洗新。来源天然不提供时间时，保留 UNKNOWN/partial，可用有界的最近成功读取支持大致金额；小额或范围外现金不因此一律否决 B。重要现金已超期且刷新失败分别返回 CASH_SOURCE_TIME_UNVERIFIED_OR_STALE_FOR_PLANNING、CASH_SUCCESSFUL_SYNC_UNVERIFIED_OR_STALE_FOR_PLANNING、CASH_REFRESH_NOT_SUCCESSFUL_FOR_PLANNING。无效金额仍沿原金额校验拒绝。A 保留降级分析、原金额及 Last-good；重复 prepare 不更改来源时间。
- **C 级现金匹配同账户及证券结算币种**，优先已有 security_currency，否则 original_currency/currency。未区分 BUY/SELL 的通用分析须有同币种现金事实，缺少时返回 SETTLEMENT_CURRENCY_CASH_UNAVAILABLE 和 required_currency；不假设自动换汇、融资或购买力。已有可信 USD 现金时，同账户无关 HKD 的旧时间仍披露，但不单独否决 USD 单标 C。完整组合仍检查其全部相关现金。已知零/负余额可以分析，不代表足够下单，B0 原资金检查决定真实可支付性。
- **A/B/C 是不同用途资格，不要求机械嵌套**。单标 C 不代表全组合 B 金额规划获批；例如公司归属未知可阻断公司敞口规划，但不必阻断证据完整的单标参数。ChatGPT/Web 应显示 qualification_scope、各级资格及实际 blockers，不能把一个范围的资格扩大到另一范围。所有等级都不替代 B0 实时行情、WhatIf、资金、风控与 Web 最终人工授权。
- QQQ listing 修正仅用于已有证据覆盖的 Tiger STK/ETF 原生 wrapper，不能覆盖 OPT/FUT/其他不兼容类型；稳定 ID 冲突仍 fail closed，未知基金不猜测。

原 AnalysisRequest 新增可选 `analysis_scope=investments|net_worth`（默认 investments），原 symbol 仅在单标专业分析中限定相关证券（组合/收益分析不能借此缩小全组合检查）；不存在 force=true。贷款陈旧仅在 net_worth/偿债用途阻断 B/C，不阻断无关单标。未证实的 linked pension 影响全组合投资分母，保持保守阻断及人工待办；不通过重复计入解决。

PEER 获得同一视图及其等级/具体 blockers，返回时重新评估同一冻结来源。上下文到期依原 U1 拒绝专业结果；不切换 snapshot_id、不偷偷刷新。Web 使用最终质量，显示三级资格、来源时间和原财富更新链接。MCP 工具权限不变，只有既有 prepare 能触发受控同步。

## 4. 测试与证据

- [D1 合成回归](../tests/test_d1_asset_quality.py)：发行主体跨账户/通道、未知身份、证券分类、来源时间、现金/FX、假日/半日市/开市、A/B/C 及范围、人工贷款、养老金待核、同视图一致性和失败 Last-good。
- [D1 评审安全回归](../tests/test_d1_review_regressions.py)：将六个评审探针转为安全断言，覆盖现金 Last-good/失败/重复 prepare、24 小时 B 边界、小额与 UNKNOWN、结算币种和账户隔离、QQQ wrapper/ID 冲突，以及独立等级语义。
- [原 U1 故障/输出重核](../tests/test_u1_review_regressions.py)、[U1](../tests/test_u1_asset_views.py)、[U0](../tests/test_u0_review_regressions.py)、[U2](../tests/test_u2_review_regressions.py)及原 B0/同步/共享库/协议回归保持。
- [浏览器固定视图](../frontend/tests/e2e/asset-view.spec.ts)覆盖资格、人工待办、相同金额/ID及最终返回质量；原国金/财富/交易确认场景同时回归。
- v1:56 私有金样仅核对三家企业/投资分母/四个理想成员，未把真实账户或完整财务 payload 提交 Git；公开测试使用不同的合成数量/金额。旧生产视图保持不可变、到期状态不被伪装为当前。
- 全量 pytest、MCP 协议/同步专项、浏览器、Offline M5、lint/build、文档与四项 CI 的实际结果在交付报告及 PR 记录；未完成的不能标 PASS。

## 5. Schema、生产与回滚

**0 列、0 索引、0 新表，无 Schema/Digest 迁移。** 新字段位于原 Run context_json、WealthSnapshot payload_json 和只读响应。旧 u1.1 payload 没有 d1.1 policy_version 时保留原保守资格，不重新映射/改写旧快照；新分析任务重新捕获。原财富趋势与分析快照筛选语义不变。

新增运行依赖 exchange-calendars==4.13.2，沿原 requirements 安装，不建设日历服务。缺库/超出范围安全返回 unknown。未来发布须 Backend/Web/MCP/分析 worker 协调固定 SHA，确认受控 prepare 原鉴权及 SDK 版本；旧组件不理解 analysis_scope，不允许把混合版本当验收通过。

真实验收须另行授权：核对组件 SHA/严格 Digest；三家只读来源及 Tiger 真实 update_timestamp；必要同步写入须按原流程获得授权。新 U1 view 下比对 Web/MCP/PEER 金额、三公司成员、QQQ 分类及所有真实时间；无时间的来源允许 partial，不编造行情。贷款/银行现金/养老金关联由用户核实，验证主/副表、投资分母与外币日期；不自动订正生产数据。

回滚为原固定兼容组件 SHA；无需反向 DDL，不回填旧日期，不以旧数据库覆盖新业务事实。已产生的新固定 view 保留，只读展示新字段可降级；旧固定 view 不改。保留 U0/U2 后台任务和交易开关，本批不开展 B2/B3 或其他接入。
