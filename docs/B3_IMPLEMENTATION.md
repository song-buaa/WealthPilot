# B3 交易体验与账户产品证据

> 2026-10-22 状态更新：本模块随4.1 **RELEASED — 已完成生产交付**；完整验收边界及非阻断观察项见[4.1结项记录](releases/v4.1.md)。下文保留原开发/隔离测试/待发布时点，不再作为当前部署状态；不据此推定真实数据已完整或全部自然场景已验证。

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

## 6. B3 IBKR运行时定向修复

基线为 `origin/main` 的 `3165c70f5764ee0bfc8388ef931a8cde2b8fa24b`。Mini 的 `B3_Runtime_Closeout_Report.md` / `Minimal_Runtime_Fix_Plan.md` 通过原部署会话的报告生成记录及更新记录读取：其健康串行查询与并发首次连接探针是本次诊断输入，不代表本分支已经生产验收。本轮仅本地 Fake SDK/独立事件循环及临时数据库，不访问 Mini/Gateway。

- 原 `action._get_adapter/_get_ibkr_adapter` 共享创建锁；原 `IBKRBrokerAdapter` 内保留一个专属 loop 和一个初始化 Task。并发等待者共享同次初始化结果，不增加 clientId。候选 IB 对象保持局部，完成原 managedAccounts/Owner/实盘门校验后才发布；失败、取消或超时按 SDK disconnect 清理。断线使连接/账户验证失效，重连重新核验；旧 socket 的迟到断线事件不能清除新连接。
- 单个等待者超时只取消该等待者，不取消其他请求依赖的共享初始化。初始化由 Adapter 拥有，按原连接预算（默认10秒 SDK timeout，加5秒完整初始化上限）有界完成；后续请求加入同一任务，不能创建同 clientId 的重叠候选。shutdown 首先永久关闭本实例准入；`_ensure_connected`、loop内 single-flight 及原 `_run_on_loop` 共用关闭门，迟到请求不能在清理后创建任务。锁只保护检查/入队，不跨网络等待。shutdown 在锁外取消并等待已接收调用和未完成初始化、断开候选/原连接，再停止loop并清引用；清理失败保留引用且明确报错，不伪称已完成，实例不自动重开。
- 仅 IBKR tradability 使用 Backend **15秒单次总预算**（连接与合约查询共用 monotonic deadline），bridge HTTP **18秒**，给原 `_run_on_loop` 最多1秒收敛余量和HTTP序列化留界。Tiger与U1 prepare原5秒HTTP预算不变；不新增用户参数、服务、池或无限重试。
- 合约查询超时取消本地 SDK Future/协程，并按Future身份定位该请求，通过ib_async 2.1.0原 `_endReq` 退休 `_futures/_results/_reqId2Contract`，不把Future.cancel冒充登记已清理。原连接wrapper的contractDetails/bondContractDetails仅委托仍有结果容器的reqId；已退休的迟到数据安全忽略，End沿原SDK空操作，不保存无界墓碑集合，不污染新请求。IBKR没有 `cancelContractDetails` 协议：本地退休不代表撤回券商端只读查询，不为清理而断开仍有效的共享订单连接。
- 已配置并鉴权通过的在线查询失败保持 `UNKNOWN` / `ACCOUNT_PERMISSION_UNPROVEN` / 费用NULL，使用 `BROKER_QUERY_UNAVAILABLE`（Backend SDK失败）或 `LIVE_ACCOUNT_QUERY_UNAVAILABLE`（bridge传输失败）。只读fallback移除互相矛盾的 `LIVE_ACCOUNT_QUERY_NOT_CONFIGURED`；真正无bridge才保留NOT_CONFIGURED。

[运行时故障注入](../tests/test_b3_ibkr_runtime.py)覆盖真实独立loop、并发首次连接、失败共享/重试、候选超时清理、等待者超时、断线/重连、旧事件、shutdown、真实ib_async Future/迟到reqId、工厂并发、受保护HTTP预算及IB01/CBU3身份/Owner；[PR31评审安全回归](../tests/test_b3_ibkr_review_regressions.py)覆盖迟到初始化/shutdown交错、清理期间锁释放、16次无End超时及迟到STK/bond/End、正常CBU3恢复、显式等待者取消、共享连接上的原OrderManager FILLED/UNKNOWN与原orderRef对账且无重发；[MCP协议回归](../pocs/chatgpt_skills/test_u1_protocol.py)覆盖已配置/未配置错误投影、只读注解与越权拒绝。原交易确认、B0提交幂等、UNKNOWN恢复、U2同步及固定视图合同不变。Registry无需更新：能力、权威Owner、工具及接口参数没有新增/迁移，仅修复原入口的运行时生命周期。

生产部署输入（需另行授权）：Backend、MCP和业务worker统一本PR固定SHA，按现有流程切换进程以清除旧Adapter状态；Web可同SHA统一发布。**0 Schema/Digest/依赖/权限变更，不执行DDL，不改Gateway/clientId/Owner/token/实盘开关**。先健康串行、再受控并发IB01/CBU3只读合约查询，核对身份、一次初始化、断线/超时后的收敛及错误码；ChatGPT刷新目录后中文只读查询，仍允许权限UNKNOWN和费用NULL。不得通过试探下单/撤单验收。回滚组件到上一固定SHA，保留全部数据库事实，不恢复旧数据库。


### 2026-11-02 移动端限价编辑修复候选

普通P2预填草稿原先每次按键立即parseFloat，导致`82.`回写为`82`。原ActionDraftCard保留独立的限价编辑文本并指定`inputMode=decimal`，仅将数值投影给原API；保留尾零和中间态，不通过格式化或舍入修正价格。空值、非正数、未完成小数及超过原Numeric(20,4)存储精度的价格不能继续确认。非法字母、符号/指数/多小数点输入不覆盖当前合法编辑值。数量字段、原确认/资金/WhatIf/TTL/订单授权链路不变；不新增Schema或依赖。

四位是现有持久化精度上限，不是证券最小报价单位承诺。现有普通单链路没有统一账户级tick证据，此项仍需来源合同核实；不得据此声称所有市场价格已验证，也不自动round到可成交价格。

新增原trade-link浏览器回归覆盖输入、删除、整值替换、清空/重编、整数、小数及非法输入，核对API/后端预览价格一致、订单前后不变。Chromium整组24/24、WebKit移动模拟定向1/1、lint/build、后端全量3070 passed/7 skipped及Offline M5 18/18通过；WebKit复用本机已有webkit-2311，不是iPhone实机证明。iPhone Safari及ChatGPT内置浏览器实机验收待用户配合，只验证参数不提交订单。当前为本地候选，未push/合并/部署；生产限价编辑问题不得标记已上线修复。

### 2026-11-03 PR #86 本地回归修复候选

稳定基线 `dac2b22161d194c075c278cde97fc384ecffd405` 与 PR #86
`fe0020b4cddef32a2009f1f0db72b6ef70f87c11` 的隔离对照确认：
仅增加小数键盘不能保留 `82.`；独立编辑文本可以。PR #86 的全策略
`validLimits` 还会额外阻断未编辑且无限价的历史草稿，即使原 `missing_fields`
允许继续；这不是移动端小数编辑所必需的规则变化。

本候选仅校验用户实际编辑过的限价文本，保留原 missing-fields、后端策略校验及
最终确认/资金/WhatIf/报价TTL。编辑后的空值、零、未完成小数、超过四位或数值
传输精度丢失仍不能确认。未编辑策略恢复稳定版确认资格，不代表绕过后端检查。
原始草稿上下文以 IBKR request、P2 prefill request、draft ID 及来源 payload 绑定；
关闭或切换来源时重置本地编辑状态，防止索引文本挂到另一条策略。空 draft ID 的
请求复用问题在稳定版已存在，本候选同时防止新增文本状态延续该问题。

[本地组件安全回归](../frontend/tests/e2e/action-draft-price.spec.ts)覆盖中间态、
API 数值、旧草稿资格、missing-fields、请求切换、来源重排和关闭重开；原交易链路
回归继续覆盖数量、TTL、WhatIf、风险及幂等。稳定/A/B/C/修复候选使用相同依赖和
构建方式，Chromium及本机 WebKit 移动模拟均能加载页面，无脚本异常，JS响应与
构建文件字节一致。**本地未复现真实 iPhone 白屏，白屏根因仍未定位**；这些业务
回归不能解释或宣称修复了生产资源下载中断。无 Schema/依赖/API/权限变化；
本轮候选仅本地，未 push、合并或部署。Registry无需更新：能力和权威入口未变。

本轮本地质量门：全量pytest 3070 passed / 7 skipped，浏览器27/27（原24+新增3），
Offline M5 18/18且公网尝试0，lint/build、工程文档及diff检查通过。新候选未push，
因此新提交CI为NOT_RUN；不将原PR的CI结果用于本候选。

### 2026-11-03 编辑上下文最终收口与发布准入

上一候选把整个payload序列化为React key；仅对象属性插入顺序变化也会丢失
`82.40`编辑。本候选改为稳定请求身份及显式业务字段数组：策略身份、方向、
数量、价格、触发条件、币种、TIF、意图关联、配置比例、缺字段身份和风险提示。
配置比例的对象键排序后比较，策略数组顺序仍有业务含义。对象字段顺序、
更新时间、说明和value_sources等元数据变化不再重置编辑；新prefill请求、
真实经济参数变化、策略重排及必要授权事实变化仍会重置。关闭重开规则、
仅对实际编辑限价校验和旧草稿原资格不变；不修改Backend或最终交易授权。

新增回归先复现属性顺序导致编辑丢失，再验证字段/配置键顺序及元数据变化保留
`82.40`与焦点，`82.`仍不可确认；逐项验证10个策略业务字段及风险/缺字段变化
清除旧编辑。原交易回归继续验证价格和数量、资金、WhatIf、TTL、风险确认及
幂等。本轮按用户要求仅重跑相关Chromium/WebKit、lint/build和必要安全检查，
复用上一轮已完成的3070 passed/7 skipped与Offline M5证据，不冒充本轮重跑。
WebKit使用本机已有webkit-2311，仅模拟，不是iPhone实机验收。

历史iPhone白屏根因仍UNKNOWN；上一轮MacBook外部Tailnet已验证稳定版和PR86的
HTTP/gzip/内容哈希，本地修复不能被描述为解决了历史下载中断。生产继续保留
回退Web。本轮无push、合并或部署，无Schema/Digest/依赖/接口/权限变化。

授权后的受控发布仅切必要Web组件，固定制品先验证HTML全部引用和资源SHA，
保留稳定Web回滚制品；沿原launchd退出等待/启动流程，保留原Serve与Backend/MCP。
切换后先由MacBook外部Tailnet验证TLS、完整下载、哈希、页面及Console，再安排
一次iPhone参数编辑验收；不确认/提交已在券商App完成的PDD交易。资源不完整、
页面异常或输入静默变化立即回滚Web，不恢复数据库。发布前仍需新提交CI及独立
发布授权；不得以本地准入代替生产或实机验收。Registry无需更新：业务能力与
权威入口不变。

最终候选本地结果：Chromium标准整组29/29、WebKit标准整组29/29、WebKit移动输入
专项7/7，lint/build、文档与diff检查通过；候选及稳定回滚包各自通过HTML引用、
归档还原哈希和两种浏览器gzip加载核验。另将整组强制设为390px时，既有历史记录
断言在原PR86制品上同样失败，未改变该历史页面或把整组移动业务标记为全通过。
已确认的小数、旧草稿资格和无意义重挂载问题关闭，未发现新增Critical/Important；
本地准入为READY_FOR_RELEASE，表示可在另行授权后进入原PR/CI及单Web受控发布流程，
不表示新CI、生产部署或iPhone验收已完成。
