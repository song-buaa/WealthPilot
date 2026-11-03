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
