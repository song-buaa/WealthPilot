# B3 交易体验与账户产品证据

## 1. 定向 Gap Matrix

开发基线：fetch 后 origin/main `791f058074c67f6d258d73d7320a03b96da2c8de`。用户确认 B2 生产已启用，本轮不访问生产。风险 L3（交易 UI/权限），仅隔离 Mock。

| 范围 | 分类 | 原 Owner / 证据 | 最小实现 | Schema |
| --- | --- | --- | --- | --- |
| 链接/预填 | FIX | [trade_link](../pocs/chatgpt_skills/trade_link.py)、[link_handoff](../backend/services/execution_batch/link_handoff.py)、[Action](../frontend/src/pages/Action.tsx) | 已有纯预览及请求幂等；补精确参数/Portfolio 校验的只读恢复，优先恢复原 READY/已提交批次 | 无 |
| 报价及 WhatIf | EXISTING | [Batch service](../backend/services/execution_batch/service.py)、[calculator](../backend/services/execution_batch/calculator.py) | 原30秒TTL、资金、WhatIf、refresh重建版本继续使用，不缓存新授权 | 无 |
| 最终确认 | EXTEND | [原 Dialog](../frontend/src/components/ConfirmOrderDialog.tsx) | 在同一次最终人工操作内顺序确认当前版本、再原提交；改价仍清空勾选和风险确认 | 无 |
| 幂等/恢复 | EXISTING | [OrderManager](../backend/services/action/order_manager.py) 与 Batch submit | 不改状态机；网络不确定先查询原请求，禁止自动补单 | 无 |
| 结果/同步状态 | EXTEND | [U2 query](../app/trade_status.py) | 原确认弹窗消费订单与独立同步状态，不把SYNC_UNVERIFIED当成交失败 | 无 |
| 合约/产品信息 | EXTEND | [IBKR](../backend/services/action/brokers/ibkr.py)、[Tiger](../backend/services/action/brokers/tiger.py)、原 trusted instruments | 原适配器只读合约查询，严格证券身份、账户校验；不试单、不调用新执行通道 | 无 |
| 账户可交易性 | EXTEND | [原 instruments 域](../backend/services/instruments)、原 Portfolio Owner/固定库 | 三态证据投影；合约存在/历史成交/WhatIf均不直接授予YES，缺当前账户权限证据UNKNOWN | 无 |
| MCP | EXTEND | [原 server](../pocs/chatgpt_skills/server.py)/[worker](../pocs/chatgpt_skills/worker.py) | 一个只读get_instrument_tradability，原脱敏账户引用与数据库只读权限；不向MCP注入券商凭证 | 无 |

没有发现需新增订单、持仓、现金或调度体系的架构阻断。共同基金执行、新行情平台及 B2 扩展 DEFER。

## 2. 交易确认及恢复合同

沿用原 `prepare_trade_confirmation_link → ActionDraftCard → ConfirmOrderDialog → ExecutionBatch.confirm/submit → OrderManager`，不新增确认页面/状态机。新链接打开仍只做纯参数校验，不自动获取报价/WhatIf或落库。用户按原入口核对后获取报价，原 ActionDraftCard 展示当前参数；最终弹窗在**一次最终人工点击**内顺序调用原 confirm 与 submit。两者仍分别校验版本、风险、30秒TTL、资金和WhatIf，未放宽任何后端安全门。

- `POST /api/execution-batches/trade-link/restore` 仅查询与 request_id、完整原始参数及当前 Portfolio 精确匹配的原批次。恢复 READY/CONFIRMED/已关联订单及终态；不取新报价、不创建批次或订单。失败重核的已失效批次按原 refresh_pending 合同重新核对，不复活旧授权。
- 只把已有可信参数直接恢复；新链接未产生数量/费用前仍明确“待计算”。重开同请求不重复填写或建 Batch，篡改原预算/参数拒绝。link_handoff在原意图确认提交前额外核对已存在批次的Owner，拒绝跨Portfolio/账户认领，不等到submit再阻止。
- 原 `reprice_all_in` 先撤销旧确认再联网，重核后清空风险文字/勾选，显示前后数量、限价及费用；新版本必须重新取得最终人工确认。
- PR #30 评审修复：原 confirm 复用短 SQLite 写锁，禁止自动 flush 旧缓存，锁内重新读取 Batch/Leg 并核对 Web 显式 expected version、READY/未认领腿、经济 review_fingerprint、Owner、TTL/WhatIf及风险授权后才写入 CONFIRMED。并发重核/完成后的迟到确认返回 HTTP 409 / CONFIRMATION_CONFLICT，不复活旧授权或倒退终态。原多腿 remaining-review 仅重新授权未认领的 READY 腿，保留已关联腿事实；锁内没有券商 I/O。跨连接 WAL 回归见 [B3安全回归](../tests/test_b3_review_regressions.py)。
- 原币可用现金、原账户现金余量、只读投影的本次all-in预算余量、预计名义金额/佣金/总支出及安全垫分开（不得把10000账户余额扣3930后的6070称为4000预算的70余额）；WhatIf费用只是预估，不是实际佣金。conId/request/batch/version放诊断折叠区。
- 浏览器 busy ref 阻止重复点击；传输异常不能证明下单失败，当前弹窗冻结发送并查询原 Batch。UNKNOWN 只对账不自动重发。原 B0 durable claim/orderRef/幂等不改。
- 结果通过原 U2 `/api/action/trade-and-sync-status?batch_id=…` 读取，区分交易与资产同步。`SYNC_UNVERIFIED` 不表示成交失败；U2只读查询不触发真实刷新。原订单轮询/对账仍由原 Owner 处理。
- `broker_pending` 显示“已提交，等待券商确认或成交”，原状态码保留在结果诊断中，不解释为已成交。

## 3. 产品查询及权限合同

业务 Owner：[instruments/tradability](../backend/services/instruments/tradability.py)。输入 broker=ibkr/tiger、至少一种 symbol/ISIN/conId、可选原 `get_investment_details` 的 account_ref。复用原Owner归属核对（含跨Portfolio冲突）及原opaque账户哈希合同，只接受现有授权 Portfolio 下 API 来源的账户身份；多账户不猜测，返回待选择账户。raw account_id、Portfolio、force、refresh、quantity、side/交易操作参数均拒绝。purpose 明确为 BUY_STOCK_OR_ETF，不把买入资格泛化为融资、做空或基金申购。

- `get_instrument_tradability` 是第10个生产只读工具；U1显式prepare为第11项（仍非只读）。原七工具和已有U2/B2均保留。
- 未配原 prepare bridge 时：原只读 worker 查询已有身份证据，返回 UNKNOWN/LIVE_ACCOUNT_QUERY_NOT_CONFIGURED；不注入券商凭证、不写库。
- 已配原 prepare bridge 时：复用其0600配置、Bearer token、固定loopback、禁代理/重定向约束，调用 `POST /api/action/instrument-tradability`。此端点独立校验同一token，执行原账户/Portfolio选择；只调用现有Adapter的可选 `instrument_details`。SQLite读事务在券商I/O前释放。无 prepare/synchronize、WhatIf/place/cancel/modify 调用。
- IBKR只用 `reqContractDetailsAsync`；Tiger复用原普通交易Adapter的已配置模式，未配置则UNKNOWN，不默认选择paper或改用另一账户；只用同账户 TradeClient的 `get_contract`，验证SDK账户。不是证券存在即YES，不按USD猜上市市场，不按历史成交/缓存确认/WhatIf PASS判断当前权限。
- **当前经核实的SDK无法独立证明正向账户买入资格**，因此肯定合约信息仍UNKNOWN，不伪造YES。三态合同保留YES但本批没有未经验证的正向授予路径；Tiger明确 `trade=False` 或 `close_only=True` 时，对当前BUY用途返回NO。来源或证券身份/上市冲突、OPT/FUT/共同基金、不支持的ISIN渠道均UNKNOWN。
- 提供实际来源的币种、支持订单类型、交易时段/时区、最小数量；来源缺失保持NULL。无具体订单及真实费证时费用UNKNOWN，不抄估算费率或BuyingPower。查询时间不冒充历史证据日期；本地registry标为STORED_IDENTITY_ONLY并保留原验证日期。
- Bridge离线/SDK错误降级UNKNOWN，仅安全原因码，不外泄原SDK异常/账户/凭证。故障不会触发同步或新订单。工具没有改变MCP数据库只读权限。

官方字段与本地SDK交叉核对（仅公开文档，未连接券商）：[IBKR ContractDetails](https://www.interactivebrokers.com/docs/tws-api/ref/contract-details) 将orderTypes/tradingHours与账户限制字段分开；当前本地ib_async合同不能把缺失限制列表当肯定权限。[Tiger Python SDK](https://github.com/tigerfintech/openapi-python-sdk) 的TradeClient.get_contract绑定配置account，Contract的trade/close_only为产品限制信息；trade=True不等于已证明当前账户权限。

## 4. Schema、部署与真实验收

**0 Schema列/表/索引、0 Digest变更、0新增依赖。** 沿用B2已迁移的严格共享契约，不重复B2迁移或真实历史导入。Backend/Web/MCP业务worker及分析组件须统一确认的SHA；只需复用已配置的U1受保护bridge/token，无新服务。未配置时明确只提供已有身份及UNKNOWN，不能当实时资格验收。

后续另行授权的Mac mini验收：固定SHA与四项CI→原组件备份/预检/切换→原交易开关不变→ChatGPT目录刷新→真实链接4000USD预填/原批次恢复→只读获取报价/WhatIf及资金（各按授权）→30秒过期/重核/人工确认变化检查→IBKR/Tiger产品查询身份、实际可用字段、UNKNOWN/NO原因及原币现金口径→已有U2交易/同步事实展示。**不得为了验收试探性真实下单/撤单；真实成交场景需独立逐次授权。** 桌面/移动浏览器隔离通过不等于真实券商或ChatGPT生产验收。

回滚：无新数据迁移，回切兼容的B2组件/前端并恢复旧工具目录即可；保留B2表/严格Digest和所有新事实，禁止旧备份覆盖。订单事实仍由原B0恢复，UI回滚不能撤销券商订单。

## 5. 隔离验收与证据

- [B3后端回归](../tests/test_b3_trade_experience.py)：原请求恢复/参数冲突/Portfolio隔离，适配器只读路径、币种/上市/ISIN/conId冲突、未知费用、源失败脱敏、事务释放、原token鉴权及越权拒绝。
- [原浏览器回归](../frontend/tests/e2e/trade-link.spec.ts)：桌面与移动端、4000 USD、报价TTL/重核差异/清空确认、批次恢复、同次最终确认、重复点击、FILLED/PARTIAL/UNKNOWN和U2同步；仅HTTP合成结果，不调用真实提交端点。
- [MCP协议](../pocs/chatgpt_skills/test_protocol.py)：真实工具目录/只读注解/参数校验、无账户证据UNKNOWN、越权拒绝及查询前后库哈希不变。
- 本地/CI结果由交付报告记录，生产、真实来源字段/账户权限及实际ChatGPT体验均runtime-pending；本批不修改decision_engine、OrderRecord权威归并、原持仓成本、交易授权、实盘配置或历史Owner。
