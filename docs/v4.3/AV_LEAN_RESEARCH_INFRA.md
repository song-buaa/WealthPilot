# AV × LEAN 个人历史回测研究基础设施

## 集成范围与来源核查

用户明确指定个人`song-buaa/WealthPilot`为唯一集成目标；不向公司GitLab写入，不迁移整个Redstone仓库或Git历史。本模块为离线研究工具，不接入生产PEER、S3、MCP、账户、ACTIVE政策或订单流程。

白名单由本对话个人P1/S1研究授权、文件首次新增史及第二位独立Reviewer共同核对；这些文件在旧公司基线`cb7e208`均不存在，路径历史未见公司P2/CLI/Skills提交介入。这是任务来源核查，不把公司仓库无许可证视作通用迁移授权，也不作法律权属保证。

|个人来源|首次提交|迁入方式|
|---|---|---|
|`av_history_algorithm/prepare/run/verify`、`av_lean_history`|`07e69b2`|迁入纯数据/账本/验收/原生算法；仅改个人命名空间及入口位置|
|`av_history_report.research_gates`|`07e69b2`/`f9e5051`|只迁资格派生函数，不迁旧报告目录布局/历史硬编码文案|
|`personal_av_quality`|`70ad6a6`|只读文件质量核验，无HTTP|
|`personal_av.SYMBOLS/write_new`|`70ad6a6`|只提取纯定义；HTTP客户端、Key路径/读取全部排除|
|`personal_av_lean.factor_rows/run`|`70ad6a6`|只提取必要转换与禁网隔离执行，不引入整套PoC包|
|`lean_poc.open_feed`、锁定IMAGE常量|`6918104`、`3cdbe52`|原个人开盘适配/镜像；本机socket定位去除用户名硬编码|
|个人转换/Review反例测试|`07e69b2`、`f9e5051`|迁纯离线行为测试，排除旧HTTP端点测试|

不迁：Redstone CLI、Skills、公司业务、合成PoC框架、研究包receiver、公司Git配置/历史、私人Key、生产配置、付费原始行情和大型冻结包。超出白名单、来源不明或公司专有依赖不准迁。

## 数据Owner与复用方式

现有AV Owner仍是`backend/services/market_data/av_fundamentals_service.py`、`execution_plan/kline_provider.py`及显式冻结入口`s3_pool_capture.py`；本任务不改这些生产/S3文件、不复制第二套HTTP客户端。

`research_backtest`只消费个人不可变文件与来源/hash/数据口径清单，不导入运行生产Owner，不读环境Key或生产缓存。本次兼容原已验收个人B3冻结布局；它是显式导入格式，不需要原Redstone代码或GitLab在线。既有WP DAILY/SPLITS冻结响应可作为来源材料，但**仅有它们并不等于完整B3资格包**：精确DIVIDENDS、日期/日历及调整语义仍必须齐全并重新资格化，不自动把DAILY的RAW与DAILY_ADJUSTED字段混接。采集能力以后需要扩展时沿原AV Owner单点完成，不在本模块另建客户端。

## PM已接受能力与边界

`AV_LEAN_RESEARCH_INFRA=PASS`限SPY/QQQ/XLK/SMH/IGV，2012-07-02—2026-10-22各3598日，锁定官方ARM64 LEAN18100 digest。原多年买入持有/有限年度调仓、独立Decimal经济复算和观察逐字节复现已验证；迁入后另做等价性验证，仓库集成/CI/合并分别留证。

`AV_LEAN_STRICT_SETTLEMENT=PARTIAL`：218笔分红中216笔原生金额与精确源不同；SPY100股原例差0.4452美元；ex-date提前入现；97笔缺pay_date；真实支付日日内资金、T+2/T+1历史制度及购买力未资格。

默认模式仅研究。`--strict-settlement`必须在Docker启动前拒绝`STRICT_SETTLEMENT_NOT_QUALIFIED`；不得捕获后自动降级默认模式，默认结果不得标严格PASS。独立账本的应收/源支付日金额不写回原生现金，不是严格购买力验收。

不宣称全市场PIT、完整历史行业/退市权益、任意策略或实盘资格；不改变S1/S2结论。PM接受限制，不继续改内核或开发新会计引擎。
