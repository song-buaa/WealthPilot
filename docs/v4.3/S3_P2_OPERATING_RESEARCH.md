# S3 P2 企业盈利情景与条件回报（本地研究候选）

复用S3 P1冻结公司池、SEC/Evidence/Financial Research、原零派息盈利倍数结算、四底线/八项敏感性/排序及归档。新增显式经营假设适配，不自动生成预测或替代PEER。未合并、未部署，Research Only；本S3 P2与Investment Policy P2不是同一功能。

## 权威与增量

- [Financial Research](../../backend/services/market_data/financial_research.py)新增opt-in `operating_ttm_bridge()`，从原`build()`取得合格EPS，由原`build_quality()`事实在同一EPS四季度窗口桥接收入/营业利润/归母利润。FY+当前YTD−可比前期YTD只用于流量，不重建Q4 EPS。财政边界、52/53周、币种单位、来源时钟及冲突缺证继续拒绝，不用期末股数推EPS。
- [原估值Owner](../../backend/services/market_data/quality_valuation.py)新增经营路径：EPS[t]=原TTM EPS×收入指数×营业利润率相对变化×净利/营业利润转换相对变化÷相对股份。利润率和转换五年线性收敛，收入逐年显式；这是条件性盈利/股数指数，不是精确未来GAAP加权稀释股本预测。
- 零派息下以等价五年整体盈利CAGR调用原`scenario()`结算终值/折现/回报。旧函数及合同输出不变；非零派息本版本拒绝，不叠加回购收益或把FCF当可分配现金。原现金/ROIC/正常化UNKNOWN保留。
- [适配](../../backend/services/s3_operating_research.py)仅消费显式私人dossier，重核父工件字节，并比较原质量/输入/P1情景。公司特定收入fade、利润率、below-line、股份及PE均为研究假设，引用不证明未来参数。

方法`s3-p2.operating-earnings.v1`，估值路径`s3-p2.operating-path-pe.v1`；原终值结算`s3-a.earnings-scenarios.v1.1`另留版本。旧工件不改，原方法回放；新归档保留完整父工件和新增方法hash。

## 来源边界

dossier命名父artifact/公司并记录研究编写时钟。每类参数需Registry引用及理由；原件笔记绑定manifest中的公司URL/hash及字节片段hash。引用只提供历史上下文。新旧比较固定原decision_at/事实/价格，事后编写的方法和假设为回溯研究，不冒充当时投资信号或样本外预测。

没有经最新修订重确认的五年指引。过期季度展望与仍在适用年度但未重确认的旧费用指引分别标注；META早先FY2026费用指引不能一概称过期，也不作为当前承诺或五年输入。非经营收益/税项保留GAAP，转换收敛不是已核正常化。历史EPS vintage与拆股价格桥缺失时，历史PIT PE为UNKNOWN，不用最新重列EPS配旧价格制造历史合理倍数。终值PE为公司特定经济与风险假设，不声称统计校准或公允价值。

## 敏感性与分档

原10%研发标尺、−10%谨慎累计门槛、并列带及八项完整性不变。八项增长扰动仍为等价整体盈利CAGR±2pp，不称收入变化；新增九格为每年收入±3pp/0与终值PE±20%/0的联动。

九格完整性及回报门槛独立进入`research_method_blockers`，不改四底线；缺失格不能晋升。每格重算该假设中心的整个联合网格和八项，再用原排序，不能沿用原中心资格。这是新增研发门，不是ACTIVE政策或概率风险边界。反推达到要求回报的收入CAGR同时披露利润率/below-line/股份/PE/零派息条件，不当作预期。公司分档不代表实际配置。

## 内部入口及复算

沿[原CLI](../../scripts/s3_quality_valuation.py)：

```sh
python scripts/s3_quality_valuation.py operating /private/prior/bundle.json --dossier /private/research/dossier.json --archive /private/research/archive
python scripts/s3_quality_valuation.py replay /private/research/archive/ARTIFACT/bundle.json
python scripts/s3_independent_recompute.py /private/research/archive/ARTIFACT/bundle.json
```

CLI业务导入前禁网，不建MCP/Runtime/数据库。改dossier再运行产生新版本，不覆盖旧版本。同输入/参数/方法确定性重复；replay重跑Owner，不能只相信存储投影。标准库[校验器](../../scripts/s3_operating_verify.py)独立检查父工件、流量桥、年度路径、终值/反推、八项/九格、重中心化资格及排名，不导入业务引擎。

边界[回归](../../tests/test_s3_p2_operating_model.py)使用合成材料。四家真实dossier/原件/输出在私人交付，不进Git。独立Review按数据准确性、预测合理性、估值可信度、风险、决策价值等权评分；工程通过不自动代表8分或未来收益。

## P2.1 本地证据校准增量

`s3-p2.operating-earnings.v1.1`保留原经营桥、终值公式、10%/−10%研发阈值、八项/九格门禁和排序。原P2归档不改；旧方法在原source version回放，新旧数值兼容另做同输入/参数对照。未Push、未集成、未部署。

新增两种显式原件笔记：`CURRENT_DISCLOSURE_CONTEXT`和`HISTORICAL_PE_ANCHOR`。它们必须绑定父工件公司manifest的SEC原件URL、全文/片段hash，经原SEC envelope/identity确认披露与抓取均不晚于新的decision_at；摘要是人工核对的研究上下文，不自动成为Owner GAAP事实或五年指引。近季度指引的适用期、已结束待披露季度目标、非GAAP定义变化、一次性项及原事实覆盖滞后分别说明。新披露不能塞回旧P1/P2决策时钟。

[原SEC Owner](../../research_v2/sec_disclosures.py)增加opt-in `asfiled_annual_eps()`：仅原primary 10-K当前财年、单上市类别、非分部US GAAP diluted EPS、USD/shares、350–380天年度、期间不晚于申报及公开日；不采用后续报表比较列、不做股份换算/TTM/正常化。Unsupported编码、重复/冲突、负EPS及多上市类别拒绝；默认SEC事实链不调用此入口。

稀疏年度PE只配对严格晚于NY公开资格日期的首个真实原始交易日收盘，并在新研究时点已经完成。价格/SPLITS沿原Owner严格解码和身份；OHLCV/来源hash/抓取时钟校验，财年起点至配对日跨任何非1拆股统一拒绝。原始价格在今天抓取，绝不声称当年下载快照或完整历史PIT估值分布；年度GAAP税项/损益可能污染倍数。2–3个样本只能检验终值假设量级，不能宣称历史均值、公允PE或机械决定终值。

`Profile.return_unit_qualification`是显式研究输入。已知未来跨类别派发且缺乏原Owner经济权益桥时，`KNOWN_FUTURE_CLASS_DISTRIBUTION_REQUIRES_ENTITLEMENT_BRIDGE`保留质量卡，清空可比回报并令rank=None；不是基本面差，不降为观察后继续数值排名。不能把股票股息当现金、额外100%收益、SBC稀释或擅自按两类价格各半。META新增C类事件属于此真实证据边界。

四组对照分离：旧D0/旧参数/原方法；同D0参数新方法兼容；新D1时点数据/旧数值参数；新D1/校准参数。后两组允许新资料改变适用性；不能把数据/时间/假设效应混称模型准确性提升。股息继续零建模，未来股数、正常化、ROIC及未合格来源缺口不因新增证据而自动解除。

P2.1边界回归见[测试](../../tests/test_s3_p2_1_calibration.py)。独立标准库复算扩展验证历史配对/拆股/PE数值与资格时钟；原SEC数据真实性仍由来源Owner及人工原件Review负责，不以算术复算代替数据审计。真实私人校准报告与五维评分分别交PM，不因工程通过或样本回报升降就宣称8分达标。

## P2.2 本地证据与发布准备候选

从最新main保留PR #89权益保护后接合原P2两提交；不修改生产P1或META权益桥。方法`s3-p2.operating-earnings.v1.2`仍用同一经营路径/PE结算/研发阈值。排名参数容器改用原`PoolRequest`，允许来源Owner拒绝公司的参数为None；全部公司不可比也保留研究卡，不制造估值参数来满足合同。

原[官方披露Owner](../../research_v2/official_disclosures.py)提供显式opt-in的`verify_current_issuer_context()`，仅三条本轮官方IR原文URL，检查发行人、HTTP200、最终URL、字节hash及捕获时钟。不自动解析或提升GAAP事实，不用可变网页标示日期回填PIT；只有`known_at=fetched_at`，历史发布时间UNKNOWN。原SEC正式申报时钟及GAAP/TTM资格不变。研究dossier可引用这些人工核对上下文，近季度指引不等于五年预测；新增来源及摘要随原档案保存，独立脚本核验相同资格。

历史年度PE继续逐份primary原件与已拆股资格配价，增加原件经营背景说明；无论增加到几个样本，均不是历史TTM分布、合理倍数的统计证明或客观公允价值。股息仍零建模，PP&E-only FCF/完整正常化缺证继续UNKNOWN。现金CapEx、租赁新增/本金和折旧各自保留口径，不通过相减把不一致变成已解释差额。

专项边界见[P2.2测试](../../tests/test_s3_p2_2_evidence.py)。真实三公司报告及独立五维评分留在私人交付；三公司同口径P2.1评分基线为8.0，不能把剔除META造成的范围效应当改进。没有新证据支持则保留数值参数，不追求回报上调。

当前正常MCP公司池入口仍默认P1自动候选；此本地P2 CLI不会隐式切换生产方法。正式替代须先明确并审核服务端dossier选择/时效/适用范围、原start/get/read与P2回放/transport的薄适配及历史版本隔离，再经受保护PR/CI、另行授权部署和真实链路验收；本轮不Merge或Deploy。没有这一产品桥不得声称当前正常MCP已能交付P2。
