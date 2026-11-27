# Binance Spot 只读账户接入

状态：开发候选；仅合成隔离验收，真实账户、生产迁移、启用与同步均待独立授权。研发基线 `7621f2437fc94ad779e5584b70ebd6f50e33be8e`。不提供 Binance 下单、测试下单、撤单、提现或划转能力。

## 复用与最小差量

| 状态 | 权威入口 | 本次差量 |
|---|---|---|
| EXISTING | restricted_sync / SyncExecutor / Repository / PositionUpsertService | 原 claim/fence、事务、Owner、Last-good、手动入口及每天22:00北京时间调度；Binance默认不启用，启用后周末同样执行 |
| EXTEND | broker_sync/binance | 标准库 GET/HMAC 客户端、UID绑定、Spot余额及BTCUSDT成交分页；无重型SDK或独立scheduler |
| EXTEND | Classification / FXService / U0/U1 / 原Web与MCP | BTC=CRYPTO/ALTERNATIVE；USDT=CASH；保留USDT原币，以批准的美元等值约定消费原USD/CNY FX；冻结原输入及质量 |
| FIX | 原快照/B2/持仓聚合 | 成本和盈亏允许UNKNOWN，外部成交复用B2，不伪造本地下单；原消费端显示UNKNOWN而非0 |
| EXTEND | 原B3/C | 严格识别Binance BTC现货，仍要求原Owner BTC覆盖证明；不签发Owner、不改变ACTIVE政策、阈值或正式分母资格 |

## 身份、覆盖和时间

配置用稳定 Binance UID 绑定现有 portfolio_id；API Key轮换不变更账户/Position身份。`GET /api/v3/account` 必须返回相同UID、SPOT账户和SPOT权限、显式BTC/USDT余额行，且不存在重复资产。请求明确 `omitZeroBalances=false`。`quantity=free+locked`，`available_quantity=free`，原始free/locked在原快照raw保存；二者不拆成两份资产。

BTC与USDT两行可全部明确为0；空列表、缺少任一行、身份错误、未知非零资产或畸形响应拒绝发布，保留Last-good。未知非零资产的代码/free/locked/总量及UNKNOWN估值保留于失败Run，并通过原同步状态质量投影读取。此为一期支持资产覆盖限制，不能将遗漏资产计为0。Funding、Earn、Margin、Futures等钱包未查询，始终披露非Spot覆盖未知。

账户HTTP响应本地接收时间与原始 `updateTime` 分别保存；后者不等于持仓/现金有效时间，`positions_as_of`、`cash_as_of`保持UNKNOWN。BTC价格取官方 `aggTrades` 的最新成交p/T，最多300秒且拒绝未来时点，明确last_trade，不声称LIVE bid/ask或可交易报价。按24/7估值，不套用股票周末休市。仅BTC/USDT总量到8位小数；超过支持精度拒绝，不四舍五入。

## 估值、成本及费用

BTC人民币市值 = BTC总量 × BTCUSDT成交价 × 1 USD等值/USDT × 原FXService USD/CNY。USDT原始资产、数量及原币仍是USDT，不改写成真实USD现金，不并入其他账户。FX的rate/date/source/quality和估值约定随Run保存；缺少有效汇率时拒绝发布，现有陈旧/质量规则不放宽。

USDT=USD为批准的估值约定，非兑付或真实美元资金证明。一个最小USDCUSDT公开参考只用于披露已观察到的相对偏离：超过1%标记 `USDT_RELATIVE_STABLECOIN_DEVIATION`，参考不可用标记UNKNOWN；不据此声称USDT实际美元兑付合格。418/429立即停止整个同步，记录Retry-After，不继续请求或自动循环重试。

BTC首次纳管成本、转入与期初库存未证，因此Position、原快照、Web、MCP、汇总均保持NULL/UNKNOWN。USDT按单位1的估值约定展示零估值差，不声称实际投资收益。BTCUSDT成交从fromId=0开始、每页1000、游标max(id)+1，每次最多10页，超限拒绝而不伪称完整；下一次按原B2中已保存trade ID增量。稳定身份为portfolio + UID + pair + trade ID，保留UTC毫秒、方向、数量、价格、成交额、原手续费资产与金额。库存按BTC扣费调整，USDT扣费按批准等值约定处理，其他手续费币种无可靠换算保持UNKNOWN。成交币种USD为B2估值层约定，原始quote_asset=USDT、quote_quantity及约定明确保留，非真实USD交割。

仅有myTrades不证明转入、兑换、期初或完整历史。B2覆盖始终partial；税费缺证及成本未知不会生成盈亏。成交与Snapshot/Position在原受fence保护的同一事务发布；任一步失败全部回滚，失败Run与Last-good区分。原LEGACY纪律消费者遇UNKNOWN成本明确受限，不以零回撤代入；ACTIVE政策规则不变。Binance没有交易Adapter，读取不能获得交易权限。

## Schema及生产迁移边界

未新增表、列或依赖。仅将 `position_snapshots.avg_cost/cost_basis/unrealized_pnl/unrealized_pnl_pct` 四列NOT NULL放宽为nullable；原Position成本/P&L字段已可空。其余券商写入及原数据不变。

复用 `scripts/migrate_b0_safety.py --stage binance` 的原plan/apply/verify、备份、schema digest、Inventory/CAS、停写门禁与contract切换，SQLite事务内重建原表并逐值复制和重建原索引。触发器、入向外键、混合Schema或冲突临时表拒绝，需人工Review。运行时只verify，不自动DDL。必须先在生产一致性副本演练、验证行级指纹/外键/完整性及原U0/B2回归，再单独批准生产迁移。旧业务仍兼容放宽约束；若已经写入NULL，不能直接回滚到假定成本必填的旧应用。

## API Key一次性准备（由用户在本机操作）

1. Binance官方账户「API Management」创建系统生成HMAC Key，名称建议 `WealthPilot-ReadOnly`。仅Enable Reading；关闭Spot/Margin交易、Futures、提现、内部划转及其他写权限。账户返回canTrade/permissions不是API Key权限证明，须独立核对Key管理页。
2. 如平台支持IP白名单，仅加入已核实且稳定的Mini实际公网出口。Tailnet地址不是公网出口；本轮未查询出口，保持UNKNOWN。如出口变化需按官方流程更新；不使用代理、VPN或其他地域/资格绕过方案。
3. 在原生产私有配置根由既有Owner安装，例如 `~/Library/Application Support/WealthPilotProd/config/binance-spot.json`（实际以Mini Runbook为准），父目录0700、文件0600、同一运行用户持有，不使用软链接，不放Git或聊天。设置绝对路径 `BINANCE_CONFIG_FILE`。文件格式：

   ```json
   {"portfolio_id": 1, "uid": "USER_VERIFIED_UID", "api_key": "LOCAL_ONLY", "api_secret": "LOCAL_ONLY"}
   ```

   示例为占位符，不能直接用于真实连接。使用原Portfolio Owner确定portfolio_id，不新建影子账户。客户端验证文件Owner/权限及UID格式；本地离线检查配置只输出配置是否合格，禁止打印对象字典、认证URL、签名或Secret。
4. 保持 `WEALTHPILOT_ENABLED_SYNC_BROKERS` 原值不变，生产未验收前不添加binance。正式授权后在原启用列表追加binance（保留原账户），原Web同步卡/手动触发和原日调度共用SyncExecutor，不新建任务。
5. 轮换：先创建同权限新Key、在私有文件原子替换key/secret、确认UID不变；经单独授权验证，再撤销旧Key。泄露时立即在官方管理页撤销，停用原启用列表中的binance。不要把Secret发给Chat。

## 首次Mini接入清单（本轮不执行）

- 项目管理核准Draft PR、exact SHA四项CI、独立Review及4.4 BTC消费者合同；按原install-github安装已合并SHA，保留原完整制品。
- 核查私有配置Owner、UID、实际账户范围/读取权限/白名单及原共享库身份；一致性备份并完成上述显式Schema演练与批准，禁止DDL随启动隐式执行。
- 独立授权一次真实只读来源调用及原正式同步写入。先展示范围（仅Spot BTC/USDT+BTCUSDT历史）、请求预算/成本、停止条件；有其他非零资产、账户冲突、限流、精度/价格/FX不合格时停止并保留Last-good。
- 核对真实free/locked/总量、原币、交易ID/手续费、快照/Position/B2原子一致性及Web/MCP读回；首次纳管成本仍UNKNOWN。未授权不能执行生产U1 prepare或Owner签发。
- 4.4业务验收取得来源Run、原始账户覆盖与时间质量、BTC明确现货身份、USDT约定、冻结FX及必要Owner覆盖/养老金去重证据。正式分母不足仍UNKNOWN，不以此次同步成功替代A3资格。
- 验收通过后另行批准调度启用；失败撤销启用并恢复已验证组件，已有真实新事实提交后禁止用旧数据库覆盖并发业务写入；走原定向修正流程。

## 验证入口及官方合同

合成回归：`tests/test_binance_spot.py`；原U0/B2/B3/C回归；实际React+临时SQLite：`frontend/tests/e2e/binance-spot.spec.ts`。CI仍为原四项。真实账户、ARM、网络可达、Key权限、生产迁移及自然调度待验收，Mock不等于生产PASS。

- [官方REST概述及错误/限流](https://developers.binance.com/en/docs/products/spot/rest-api)
- [账户与成交接口](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/account)
- [市场成交与价格接口](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market)
