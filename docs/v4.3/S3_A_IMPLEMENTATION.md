# S3-A 冻结公司池质量与估值研究

S3-A已由PR #67以Research Only集成main `99040f6811167ee6af2318ab2ae59d212e1ad36f`；原基线PR #66 `1ce59ae58c234777c1e4bd9d3697acdf4f649ab3`。以下S3-A段落保留v1.1方法的能力边界，S3-B本地新版本见文末，不覆盖旧方法或工件。没有生产接线、MCP/Web入口、新Skill、模型调用或交易权限。

## 范围与复用

内部入口 [s3_quality_valuation.py](../../scripts/s3_quality_valuation.py) 支持run/read/replay/diff。原SEC/官方披露提供事实，原 [Evidence.assess](../../backend/services/market_data/evidence.py) 在共同截止重新核资格，原 [financial_research.build](../../backend/services/market_data/financial_research.py) 计算TTM、EPS、价格、PE和收益率。S3只新增 [确定性比较服务](../../backend/services/market_data/quality_valuation.py) 与 [小型冻结工件适配](../../backend/services/quality_valuation_research.py)，不改原财报解析或TTM。

风险L2：新增跨公司口径、情景与本地研究文件写入。数据Owner和原PEER不变；本阶段不接入PEER或新建Agent Runtime。已有SQL研究Repository需要业务数据库，E1库保存当次人工确认的Thesis/Review，均不适合作为自动计算工件缓存。这里仅用原E1无副作用的canonical序列化函数，保存显式指定目录下的内容寻址JSON及来源文件；不打开或迁移任何数据库，也不替用户保存E1投资判断。

## 冻结合同与内部入口

ResearchRequest要求pool_id/version、purpose、带时区decision_at、用户指定P1 manifest SHA、1–20个canonical US symbol及逐公司三情景参数；未知字段拒绝。首版支持普通股、USD且合格稀释EPS为正的盈利情景法，ADS/其他币种及不适用方法明确拒绝/UNKNOWN，不承诺20家公司全覆盖。

```bash
PYTHON_DOTENV_DISABLED=1 python scripts/s3_quality_valuation.py run \
  --request /private/s3/request.json \
  --p1-root /private/p1-frozen-inputs \
  --archive /private/s3/archive
python scripts/s3_quality_valuation.py read /private/s3/archive/HASH/bundle.json
python scripts/s3_quality_valuation.py replay /private/s3/archive/HASH/bundle.json
python scripts/s3_independent_recompute.py /private/s3/archive/HASH/bundle.json
python scripts/s3_quality_valuation.py diff /private/old/bundle.json /private/new/bundle.json
```

CLI在业务导入前禁用dotenv及socket连接，不访问Provider、模型或券商。P1的原始manifest固定方法/输入hash；适配器核对SEC请求原件、行情原文及收据、旧full行情压缩原文；从冻结SEC记录与原technical记录重新assess，不重新collect。原输入资格变化不能静默升级。hash证明工件完整性，不替代原Owner的来源资格。

冻结bundle包含请求、原Owner重核assessment、缺失原因、源文件hash、方法文件hash及结构化结果；原始SEC/价格/收据复制在sources，报告及逐公司JSON同存。内容hash为长期artifact_id，不依赖calc ID或进程内缓存；原calc ID仅作包内证据链接。临时目录完成后原子rename，不覆盖历史；相同输入重复保存返回同一目录；读回验证来源hash，重算检测方法漂移。diff区分数据、假设、方法和名单变化。

参数例：

```json
{
  "basis": "S3_RESEARCH_ASSUMPTION_NOT_POLICY",
  "rationale": "说明增长、稀释、倍数和分红的研究假设，非管理层指引",
  "required_return": "0.10",
  "cautious": {"earnings_growth":"0.02","annual_share_change":"0","terminal_pe":"18","dividend_payout":"0.01"},
  "base": {"earnings_growth":"0.15","annual_share_change":"0","terminal_pe":"25","dividend_payout":"0.01"},
  "optimistic": {"earnings_growth":"0.25","annual_share_change":"0","terminal_pe":"32","dividend_payout":"0.01"}
}
```

这是每家公司companies映射中的对象，外层池合同由ResearchRequest校验。实际AAPL/NVDA请求及冻结样例在仓外验收目录；不把临时本机路径固化为默认运行参数。

## 四项底线与事实边界

数据可信、财务持续性、利润与现金支撑、核心业务变化分别输出通过/警示/暂无法判断。展示已核年度及季度收入、营业利润、CFO和CapEx；仅同期间可比年度相除/相减形成经营利润率及历史FCF，历史不称最新TTM。年度增长需要相邻财年；缺当前完整现金流与营运资本/一次性/SBC桥时现金底线仍未知。

原样例缺资产负债表/债务及受限现金闭包，持续性UNKNOWN；缺平均资本、租赁/研发/回购影响，ROE/ROIC UNKNOWN。GAAP事实、正常化调整和研究参数分域，未核实调整列表为空，不把GAAP当正常化。经营风险列出缺失及需要改变结论的事实，不编造竞争地位或管理层指引。

## 五年盈利情景方法

方法版本 `s3-a.earnings-scenarios.v1.1`。五年是比较窗口，不是强制持有期。

1. EPS(t)=合格TTM稀释EPS×(1+整体盈利增长)^t/(1+相对股数变化)^t。
2. 分红(t)=EPS(t)×假设派息率；期末价=EPS(5)×假设PE。
3. 折现条件价格（字段conditional_value）=逐年分红折现之和+期末价折现。年化回报=((期末价+累计分红现金)/观察价)^(1/5)−1。
4. 分红持有现金、不再投资；回购只反映在股数变化中，不另加回购收益。相对股数路径是研究假设，不声称实际当前股本已核验。
5. 反推在忽略分红时当前价与要求回报所需的第五年EPS和整体盈利增长，明确这一简化。

条件价值指基于研究情景及要求回报的折现条件价格。这是盈利终值倍数法，**不是完整DCF、正常化利润预测或客观公允价值**。净债务/受限现金未闭合，不生成EV桥。PE/盈利收益率只作原Owner交叉指标；非正EPS不输出虚假倍数/情景。

谨慎/基准/乐观参数均明确S3研发假设，没有概率。敏感性为基准盈利增长±2个百分点、PE±20%、年股数变化±1个百分点、要求回报±2个百分点；超出合同边界返回不可计算，参数影响现金流与折现分别列明。Decimal确定性运算，输出八位小数；年化为终值现金总额口径，不冒称IRR。

## Research Only能力边界与S3-B扩展点

S3-A缺少财务持续性、利润现金支撑和核心业务变化的完整正向判断路径。当前priority_blockers恒非空，**优先考虑档不可达**；该档只是保留的合同位置。不得通过删除blockers或接受请求中的pass标志制造推荐。1–20家公司是输入合同容量，不是任意20家公司自动研究能力；实际验收仅为AAPL/NVDA冻结历史样例。

结构化classification_reasons分别列出质量证据缺口、研究阈值未满足和基本面恶化判断（当前NOT_ESTABLISHED）。缺源或方法不适用时，阈值状态为NOT_ASSESSABLE，未满足阈值列表为空；不能把未评估误记为失败。历史收入下滑警示是观察值，不自动证明持续性基本面恶化；暂不优先不是完整投资否定判断，继续观察也不是已通过完整质量评价。

后续S3-B扩展点只作交接：在原财务/Evidence Owner补齐带期间/口径/来源的资产负债及流动性、当前利润现金/一次性与SBC桥、持续经营变化的正反证据，再由质量评估器逐项生成有证据的通过/警示/未知及对应blockers；不得从用户布尔值或情景回报升级质量。该扩展需单独批准、方法版本及正反例验收，本轮不实现、不调整政策。

## 分档与排序

顺序规则而非分数相加：无合格方法为暂不优先且不排名；基准年化低于研发要求回报或谨慎五年回报低于研发下行阈值（默认−10%）为暂不优先；其余若仍有质量缺口或高敏感性则继续观察；保留的完整门槛通过分支才会优先考虑，但S3-A自身无法产生该分支所需的完整正向质量结果。重大未解决问题不会因便宜被升级。

档内先按基准年化、再按谨慎五年回报排列；两者分别在默认2/5个百分点带内且底线状态相同可并列。无强制Top数目，允许全部非优先。逐公司敏感性会重排全池并记录变动；敏感性回报跨度超过5个百分点标高敏感。阈值均为可配置/公开研发选择，非投资政策。失败公司保留原因，配置层固定UNKNOWN，不提供个性化增持。

## 验证与限制

[专项测试](../../tests/test_s3_quality_valuation.py)覆盖确定性、输入单位/期间/来源/股份口径、负EPS/缺季、不适用、局部失败、并列与敏感性、不可覆盖保存/读回/差异、原始文件篡改和方法漂移。独立 [stdlib复算器](../../scripts/s3_independent_recompute.py) 不导入WealthPilot，复核来源hash、所有情景/敏感性公式及分档/档内排名。

真实AAPL/NVDA使用PR66已验收原文和冻结decision_at，不刷新财报/价格；公司行动IR网页原始字节未冻结的P1限制继续保留。它证明真实输入下的本地工程闭环，不证明生产PEER、投资效果或交易资格。未运行项目Gate前不能宣称完成；实际命令/结果、最终本地SHA及工件由仓外交付报告记录，历史本地交付时远端CI未运行；本次受保护集成的exact SHA、PR/CI与独立Review见仓外交付收据。


## S3-B 本地研发方案（2026-10-31，未集成）

风险L2。需求为有来源的三底线正向/警示/UNKNOWN、资本效率资格及冻结闭环；非目标为全行业解析、正常化预测、政策/E1/PEER修改、生产接线或交易。基线main 99040f6811167ee6af2318ab2ae59d212e1ad36f；本轮仅本地，不push/merge/deploy。

复用SEC parse_companyfacts/身份与accession、Evidence独立来源证明、financial_research确定性计算和S3冻结入口。最小Owner扩展为显式opt-in的实体级US GAAP瞬时与YTD事实（另分quality记录，不改变原季度/TTM资格）；再由原财务Owner生成同期间现金勾稽及平均权益ROE输入。S3评价解释和门禁，不复制来源信任或解析。

方法升级须与S3-A区分；盈利情景公式、要求回报、下行及并列阈值不变。保留高敏感性跨度标记，新版本优先档还须既定各项压力下回报门槛通过，不能只删blockers。质量PASS限有限财务/经营证据窗口，不等于竞争优势、无持续经营风险或投资策略获批。正常化无依据则UNKNOWN，ROIC分类未闭合则UNKNOWN，不生成资本效率排名。

验收覆盖三档真实可达、逐项证据门禁、重大负面不被高回报抵消、缺数/冲突/历史时钟、局部失败、相同版本重算、真实冻结AAPL/NVDA以及旧方法工件在原源码回放。全部本地Gate沿治理第6节；不为测试启完整backend。


### S3-B 方法与有限适用性

方法 `s3-b.quality-evidence.v1.1`，质量规则 `s3-b.us-gaap-nonfinancial-quality.v1.1`，财务投影 `financial-quality-inputs.v1`。只对来源证明的美国本土US GAAP、披露分类流动资产负债的非金融窗口实施；缺SIC/身份或金融/公用事业不升级。不是银行、金融控股或全行业质量模型。

- 财务持续性：最新资产负债平衡、现金与含受限现金一致性、非流动债务，以及流动债务总额或当前长期债务+商业票据的闭包。流动资产覆盖流动负债、非受限现金覆盖当前债务且同截止CFO为正，才通过有限窗口筛查；否则警示或UNKNOWN。缺独立短债不能假设零；已披露当前偿债部分不等于完整长期到期表。未来到期/表外承诺、契约和持续经营全文审查仍未知，不承诺无风险。流动性缺口且负CFO的组合证据阻断优先。
- 利润现金：同起止/口径的GAAP利润、CFO、PP&E现金流出、SBC、回购、加权稀释股数与应收/存货/应付变化完整，CFO/正利润≥1且FCF减SBC敏感值为正才通过有限GAAP支撑规则。转换不足先警示，即便CapEx缺失仍保留已知警示。生产性资产支出较PP&E宽，单列cash_after_productive_assets，不当作PP&E FCF。WC符号按报告原义保留，不将个别变化硬凑成完整桥。正常化及一次性归因UNKNOWN，调整列表为空；SBC扣减仅经济敏感性，不是GAAP现金或正常化利润。回购现金与股数变化分别展示，不再计入估值收益。
- 核心业务：两个连续季度各有同长度同比收入与营业利润率，收入不下降、营业利润为正且压缩不超过5pp，才通过观察窗口筛查。无长期护城河因果结论。两期同比收入均下降且营业亏损为已证实指标恶化并阻断；短暂下降只是警示，原因与恢复证据仍UNKNOWN。
- ROE：同期间归母净利润÷期初期末归母权益平均；期初必须紧邻期间起点。YTD不年化，不拿AAPL九个月与NVDA六个月ROE排名。回购会压低权益并放大ROE。ROIC需平均经营投入资本、税/NOPAT、租赁及研发处理闭包，本版未造代理值；披露输入资格与UNKNOWN。

以上质量阈值是透明研发规则，非正式Investment Policy；优先档须四底线全部通过。UNKNOWN/一般警示不能自动解除blocker；重大风险在回报前阻断。classification_reasons分别列缺证、警示、重大风险、研究阈值与经营恶化。

原S3-A跨度>5pp的高敏感性标记与估值/下行/并列参数均保留。S3-B明确升级该标记的分档语义：八项计算完整性是独立资格：缺失、不可计算、重复或错误case集合均不得进入优先档，不能用剩余低跨度绕过。完整集合下，高跨度只有各项基准情景单因素年化回报仍满足对应要求回报，才可解除敏感性阻断；否则观察。这不是全面下行风险稳健性；谨慎五年累计回报门槛独立生效。原跨度硬闸会使正常盈利倍数模型的正回报候选普遍不可达，不能用修改参数或删除质量blockers解决。敏感性不是概率/全风险覆盖；排序翻转仍单列，不宣称稳定获利。

### 旧工件兼容与验收

新方法/Owner文件哈希进入新bundle，不覆盖S3-A。旧P1 manifest只接受当前方法哈希或已限定的PR66历史Owner哈希，原季/EPS/价格重核仍须与冻结assessment一致，质量事实从同批原始CompanyFacts重新经Owner解析和Registry审核。未知历史Owner版本不接受。

`read`可完整校验旧工件；`replay`要求方法文件精确一致，不把S3-B规则套到S3-A。旧S3-A完整回放使用原main `99040f6811167ee6af2318ab2ae59d212e1ad36f` 的隔离源码（开发侧git archive至临时目录，无生产启动）；独立标准库复算器同时支持旧/新版本，拒绝Python -O。实际回放与新真实样例收据留在仓外交付目录。新算法正反例为合成规则边界，真实AAPL/NVDA结果另有冻结工件，不混称生产/策略收益验收。


### S3-B PM Review 收口（本轮受保护集成候选）

原c617d38的敏感性缺口已修复为独立八项资格，保留原5pp跨度及排名变化。`sensitivity_complete`、`sensitivity_qualification`、`sensitivity_span`与单因素threshold状态分开记录；每次_rank重新核验，不信任已有布尔标志。独立标准库复算器按方法版本验证v1原语义与v1.1完整性门禁；旧S3-A/S3-B工件仍用对应原源码回放。

Review同时补充已使用余额的非负与总分一致性资格：原financial Owner拒绝负债务/现金等非负字段的负值；有限筛查不能接受流动资产超过总资产、现金超过流动资产、当前债务超过流动负债、流动负债超过总负债或非流动债务超过相应负债范围。权益允许因回购等为负，不机械判为冲突。无投资阈值变更；不一致保留UNKNOWN/blocker。

报告显示实际流动资产、流动负债、缓冲与`buffer >= 0`阈值及来源，不把很小的正缓冲写成全面财务稳健性。AAPL/NVDA仍是历史有限US GAAP非金融窗口筛查。NVDA现金警示不等于确认盈利恶化，精确PP&E CapEx/FCF仍UNKNOWN；ROE不年化且不跨不同期间排名，ROIC与正常化仍UNKNOWN。研发三档不是正式建议，同档名次不是实际配置优先级。

风险L2、仅原分支修复与受保护Git集成；原SEC opt-in、TTM/EPS、五年估值公式、10%示例要求回报、−10%谨慎累计下限及并列阈值不变。无生产部署、MCP注册、政策、E1/PEER或交易变更；S3-C只交接最小Owner缺口，不在本轮实现。


## S3-C 本地实施方案（2026-10-31）

风险L2，基线ba271d683474abd75cb805ae47eb5066376be8ac。只扩展来源驱动的方法适用性与不可比表达，不重做SEC/Evidence/财务计算/PEER，不接政策/账户/执行。原Issuer Owner最小补充SEC注册ticker到规范类股写法的精确映射与上市类别来源；实体解析不授予每股类别换算资格。

复用P1/S3-B冻结输入、SEC parse_companyfacts/parse_filing、Evidence.assess/assess_quality_facts和financial_research；新增官方来源仍由原Owner限定读取、身份/原件/时钟核验。金融/保险不能使用普通非金融筛查或直接GAAP EPS目标价；替代经营盈利/分部方法输入不齐则明确缺口，不实现第二估值引擎。

适用性分APPLICABLE、NOT_APPLICABLE、INSUFFICIENT_EVIDENCE，并与计算资格/五年USD上市股权现金回报可比性分开。暂不可比公司保留研究卡、group/rank为空，不能自动放入暂不优先或视为基本面恶化。原AAPL/NVDA数值/质量门禁、八项敏感性与谨慎门槛保留；历史工件用各自原方法回放。首验可缺估值，真实来源不足不以合成数字替代。完整Gate、四家公司实际冻结与独立复算在仓外交付留证；仅本地Commit，不Push/Merge/Deploy。
