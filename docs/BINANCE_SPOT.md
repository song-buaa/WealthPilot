# Binance Spot 只读账户接入

状态：原PR #133已集成；生产Owner已确认网络/TLS/签名、环境加载、Key只读权限及UID预检。本次兼容候选基于d89b32226da83689f97f6f96c90def5a0ee95069，仅隔离修复权限组误判、USD现金和未知资产partial；不重复真实预检，不迁移/部署/同步。不提供交易或资金操作。

## 复用与最小差量

| 状态 | 权威入口 | 本次差量 |
|---|---|---|
| EXISTING | restricted_sync / SyncExecutor / Repository / PositionUpsertService | 原 claim/fence、事务、Owner、Last-good、手动入口及每天22:00北京时间调度；Binance默认不启用，启用后周末同样执行 |
| EXTEND | broker_sync/binance | 标准库 GET/HMAC 客户端、UID绑定、Spot余额及BTCUSDT成交分页；无重型SDK或独立scheduler |
| EXTEND | Classification / FXService / U0/U1 / 原Web与MCP | BTC=CRYPTO/ALTERNATIVE；USD走原生现金，USDT保留原币并用批准的美元等值约定消费原USD/CNY FX；其他资产的分类沿原Owner，无法证明仍UNKNOWN；未估值数量保留于原Run |
| FIX | 原快照/B2/持仓聚合 | 成本和盈亏允许UNKNOWN，外部成交复用B2，不伪造本地下单；原消费端显示UNKNOWN而非0 |
| EXTEND | 原B3/C | 严格识别Binance BTC现货，仍要求原Owner BTC覆盖证明；不签发Owner、不改变ACTIVE政策、阈值或正式分母资格 |

## 身份、覆盖和时间

账户余额标识接受 1–20 位 ASCII 大写字母或数字，或 1–20 个 CJK 基本区汉字（U+4E00–U+9FFF），按原字符串判重。交易对和成交手续费继续使用原 ASCII 规则。其他资产的明确零余额保留在原 Run 库存证据中，无需查询报价。非零中文标识保留数量和原身份，走 UNKNOWN/partial，无交易对请求。余额校验失败时，失败 Run 的 `balance_validation_error` 记录零基行索引、字段、类型与失败谓词，不记录原值。

`BINANCE_UID`为稳定账户身份；Portfolio由原`portfolio_scope()`（共享库合同/原Portfolio配置及Owner）解析，并由原Repository拒绝跨Portfolio账户冲突，不另配Binance专属portfolio_id；API Key轮换不变更账户/Position身份。`GET /api/v3/account` 必须返回相同UID、accountType=SPOT及显式BTC/USDT余额行，且不存在重复资产。请求明确 `omitZeroBalances=false`。`quantity=free+locked`，`available_quantity=free`，原始free/locked在原快照raw保存；二者不拆成两份资产。

账户permissions是交易资格组（如TRD_GRP_236），不是API Key权限，不要求包含字面SPOT，也不从canTrade/permissions推导Key可写。既有只读Key预检及应用GET白名单不变。身份失败脱敏区分BINANCE_ACCOUNT_RESPONSE_INVALID、BINANCE_UID_MISSING、BINANCE_UID_MISMATCH、BINANCE_ACCOUNT_TYPE_NOT_SPOT，不输出UID或密钥。

BTC/USDT可明确为0；USD存在时按独立原币现金保存free、locked及总量，包括明确0，不与USDT合并。空响应、缺少核心余额行、账户不符、重复资产、非法数值仍拒绝并保留Last-good。其他非零资产不因代码未知拒绝整批：原aggTrades只读端点最多补8个USDT计价、带真实成交T的可靠报价；超预算/缺报价/超出持仓精度的数量保存在原Run的unvalued_assets，估值UNKNOWN/NULL，发布complete=false。其他资产不增加历史成交、研究或交易能力。

复用原observed_rows短事务发布：仅更新有可靠估值的行；不删除未覆盖行，不覆盖未能重估行的Last-good。上次已知资产本次未返回时，也记录缺证而不推断0。完整来源明确归零后按原机制处理。原始余额（包括0）保存在同一Run的balance_inventory，无需新表或额外nullable列。未知资产不伪造market_value=0的Position，而通过原Run收据、原Web总览/同步卡、MCP及U1质量清单显示数量和UNKNOWN；既有Position保留已知/Last-good估值。总额明确是已记录估值小计，不代表完整账户现值。新来源恢复后，原最新成功Run替换缺口投影，无第二套账本。Funding/Earn/Margin/Futures未查询，仍不声称全钱包覆盖。

账户HTTP响应本地接收时间与原始 `updateTime` 分别保存；后者不等于持仓/现金有效时间，`positions_as_of`、`cash_as_of`保持UNKNOWN。BTC价格取官方 `aggTrades` 的最新成交p/T，最多300秒且拒绝未来时点，明确last_trade，不声称LIVE bid/ask或可交易报价。按24/7估值，不套用股票周末休市。BTC/USDT/USD沿原8位持仓精度；其他资产超精度时保留原始数量、估值UNKNOWN，不四舍五入。每份有效报价时点独立保留，不能把新BTC时点套到其他保留的旧持仓。

## 估值、成本及费用

原币USD现金人民币市值 = USD总量 × 原FXService USD/CNY；复用cash_position、原Classification和现金身份，不套用USDT约定。USD/USDT的FX及资产分别保存，不覆盖、不重复。

BTC人民币市值 = BTC总量 × BTCUSDT成交价 × 1 USD等值/USDT × 原FXService USD/CNY。USDT原始资产、数量及原币仍是USDT，不改写成真实USD现金，不并入其他账户。FX的rate/date/source/quality和估值约定随Run保存；缺少有效汇率时拒绝发布，现有陈旧/质量规则不放宽。

USDT=USD为批准的估值约定，非兑付或真实美元资金证明。一个最小USDCUSDT公开参考只用于披露已观察到的相对偏离：超过1%标记 `USDT_RELATIVE_STABLECOIN_DEVIATION`，参考不可用标记UNKNOWN；不据此声称USDT实际美元兑付合格。418/429立即停止整个同步，记录Retry-After，不继续请求或自动循环重试。

BTC首次纳管成本、转入与期初库存未证，因此Position、原快照、Web、MCP、汇总均保持NULL/UNKNOWN。USDT按单位1的估值约定展示零估值差，不声称实际投资收益。BTCUSDT成交从fromId=0开始、每页1000、游标max(id)+1，每次最多10页，超限拒绝而不伪称完整；下一次按原B2中已保存trade ID增量。稳定身份为portfolio + UID + pair + trade ID，保留UTC毫秒、方向、数量、价格、成交额、原手续费资产与金额。库存按BTC扣费调整，USDT扣费按批准等值约定处理，其他手续费币种无可靠换算保持UNKNOWN。成交币种USD为B2估值层约定，原始quote_asset=USDT、quote_quantity及约定明确保留，非真实USD交割。

仅有myTrades不证明转入、兑换、期初或完整历史。B2覆盖始终partial；税费缺证及成本未知不会生成盈亏。成交与Snapshot/Position在原受fence保护的同一事务发布；任一步失败全部回滚，失败Run与Last-good区分。原LEGACY纪律消费者遇UNKNOWN成本明确受限，不以零回撤代入；ACTIVE政策规则不变。Binance没有交易Adapter，读取不能获得交易权限。

## Schema及生产迁移边界

未新增表、列或依赖。仅将 `position_snapshots.avg_cost/cost_basis/unrealized_pnl/unrealized_pnl_pct` 四列NOT NULL放宽为nullable；原Position成本/P&L字段已可空。其余券商写入及原数据不变。

复用 `scripts/migrate_b0_safety.py --stage binance` 的原plan/apply/verify、备份、schema digest、Inventory/CAS、停写门禁与contract切换，SQLite事务内重建原表并逐值复制和重建原索引。触发器、入向外键、混合Schema或冲突临时表拒绝，需人工Review。运行时只verify，不自动DDL。必须先在生产一致性副本演练、验证行级指纹/外键/完整性及原U0/B2回归，再单独批准生产迁移。旧业务仍兼容放宽约束；若已经写入NULL，不能直接回滚到假定成本必填的旧应用。

## API Key一次性准备（由用户在本机操作）

1. Binance官方账户「API Management」创建系统生成HMAC Key，名称建议 `WealthPilot-ReadOnly`。仅Enable Reading；关闭Spot/Margin交易、Futures、提现、内部划转及其他写权限。账户返回canTrade/permissions不是API Key权限证明，须独立核对Key管理页。
2. 复用现有私有`.env`及原启动环境加载机制，由用户在本机填写以下三项；不新建独立凭据文件或加载器。下例仅变量名，真实值不得发Chat或上传Git：

   ```dotenv
   BINANCE_UID=
   BINANCE_API_KEY=
   BINANCE_API_SECRET=
   ```

   原`backend.main`经`load_project_environment()`加载已有`.env`；Adapter只消费已加载环境，不自行寻找/打开`.env`。空shell变量与Tiger一样让位给`.env`，有效运行环境变量优先。沿原私有配置Owner、受保护位置和文件权限管理，既有Portfolio选择与账户隔离不变。离线校验仅输出PASS/稳定错误码，禁止打印配置对象字典、认证URL、签名或Secret。缺少任一值返回`BINANCE_NOT_CONFIGURED`，格式非法返回脱敏`BINANCE_CONFIGURATION_INVALID`。
3. Product Owner已确认当前只读Key不设IP限制，固定IP白名单不是上线前置条件；以后若自行启用，沿官方Key管理处理。默认直接连接官方 `https://api.binance.com:443`，忽略系统和通用代理变量。只有确认账户、设备实际所在地和既有出口符合适用的 Binance 服务规则时，才可在已受保护的运行配置中设置 `BINANCE_HTTPS_CONNECT_PORT` 为现有 `127.0.0.1` HTTP CONNECT 监听端口（1–65535）。此选项仅作用于 Binance，禁止使用匿名代理、TLS 解密代理或用来规避访问限制。CONNECT 仅发送官方目标地址，完成端到端 TLS 校验后才发送 API 请求；失败时保持原错误处理，不切换出口、不跟随重定向。发布前在服务身份下用选定路径验证公开时间接口，再按原授权验证只读账户。
4. 保持`WEALTHPILOT_ENABLED_SYNC_BROKERS`原有效配置，生产未验收前不添加binance。正式授权后在原启用列表追加binance（保留原账户），原Web同步卡/手动触发和每天22:00（含周末）调度共用SyncExecutor，不新建任务。凭据存在不等于同步任务获准启用。
5. 轮换：在原`.env`或现有运行环境安全替换`BINANCE_API_KEY`/`BINANCE_API_SECRET`，保持UID不变，按既有服务环境重载流程由Owner操作；经单独授权验证，再在官方管理页撤销旧Key。泄露时撤销Key并停用原启用列表中的binance，不在聊天处理Secret。


## 首次Mini接入清单（本轮不执行）

- 项目管理核准Draft PR、exact SHA四项CI、独立Review及4.4 BTC消费者合同；按原install-github安装已合并SHA，保留原完整制品。
- 复用已封存的环境、UID及只读权限预检，保留原Portfolio/共享库Owner；不重复独立网络/权限探针，不要求IP白名单，不新增JSON凭据；一致性备份并完成上述显式Schema演练与批准，禁止DDL随启动隐式执行。
- 本候选经批准发布后，另行授权原入口首次正式同步；同步本身仍逐次校验UID与accountType，不重复单独网络/TLS/权限研究。先展示范围（Spot余额、USD/USDT现金、BTCUSDT历史及最多8项其他资产只读报价）、请求预算/成本和停止条件；账户冲突、限流、核心价格/FX失败仍停止并保留Last-good。其他缺报价资产保留数量/UNKNOWN并partial，不删除或覆盖原有效估值。
- 核对真实free/locked/总量、原币、交易ID/手续费、快照/Position/B2原子一致性及Web/MCP读回；首次纳管成本仍UNKNOWN。未授权不能执行生产U1 prepare或Owner签发。
- 4.4业务验收取得来源Run、原始账户覆盖与时间质量、BTC明确现货身份、USDT约定、冻结FX及必要Owner覆盖/养老金去重证据。正式分母不足仍UNKNOWN，不以此次同步成功替代A3资格。
- 验收通过后另行批准调度启用；失败撤销启用并恢复已验证组件，已有真实新事实提交后禁止用旧数据库覆盖并发业务写入；走原定向修正流程。

## 验证入口及官方合同

本次真实兼容定向覆盖TRD_GRP身份、USD/USDT、未知资产数量与partial、原Run/B2事务及Last-good、Web/MCP只读投影。此前配置收口验证原dotenv优先级、脱敏、缺失/非法凭据及原Owner/UID绑定（`backend/core/tests/test_env.py`、`tests/test_binance_spot.py`），不重复浏览器专项或主体业务验收。原合成回归：`tests/test_binance_spot.py`；原U0/B2/B3/C回归；实际React+临时SQLite：`frontend/tests/e2e/binance-spot.spec.ts`。CI仍为原四项。已由生产Owner确认的预检事实不重复研究；本候选的生产迁移、正式同步及自然调度仍待授权验收，Mock不等于生产PASS。

- [官方REST概述及错误/限流](https://developers.binance.com/en/docs/products/spot/rest-api)
- [账户与成交接口](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/account)
- [市场成交与价格接口](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market)
