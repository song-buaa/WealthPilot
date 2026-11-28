# S3-A 冻结公司池质量与估值研究

S3-A已由PR #67以Research Only集成main `99040f6811167ee6af2318ab2ae59d212e1ad36f`；原基线PR #66 `1ce59ae58c234777c1e4bd9d3697acdf4f649ab3`。以下S3-A段落保留v1.1方法的历史能力边界，后续版本及一期可选MCP/Skill接入见文末，不覆盖旧方法或工件。尚未生产发布或真实ChatGPT线上验收，没有交易权限。

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

### S3-C 方法与内部入口

新增薄适配 `backend/services/s3_method_research.py`，复用原 `compare` 的质量门禁、五年公式、八项敏感性、谨慎门槛及档内排名；没有新增估值引擎。来源驱动规则 `s3-c.method-applicability.v1` 将有限质量筛查、盈利模型适用性、输入资格及回报可比性分开。默认原S3-A/B入口不切换方法；新入口须显式 `--method-applicability`，新工件不能冒用旧方法哈希。

`python scripts/s3_quality_valuation.py run --method-applicability --request REQUEST.json --p1-root P1_ROOT --official-root OFFICIAL_ROOT --archive ARCHIVE`。补充原始SEC资料由显式 `scripts/sec_dev.py run -- python scripts/s3_capture_sec.py --output NEW_ROOT TSLA:US BRK.B:US` 限定捕获，仍调用原Owner的身份、URL、限速、重定向与错误诊断；不获取价格/模型/持仓。`official_manifest_sha256`必须固定manifest，包含URL、原件路径、SHA和真实fetch时钟；HTTP200仅原件可读，不是财务资格。没有新来源时可省略official-root，缺失公司仍留卡并标UNKNOWN，不能宣称真实首验通过。

新共同截止不得早于P1固定时钟；原数据在后续截止重新经Evidence审核，原发布时间/fetch时钟保留，不通过移动时钟修复缺证。金融保险SIC6000–6999不进入普通质量/GAAP盈利倍数计算；经营盈利、投资资产、保险浮存金及分部/类股桥不齐时，替代方法保留INSUFFICIENT_EVIDENCE。公用事业/范围外及外资口径也不能自动套用有限非金融规则。SEC目录与Submissions共同解析点号/连字符类股身份；这不授予EPS换算资格。多上市类别缺少合格每股桥不能进入盈利倍数比较。

无方法/输入/假设的企业 `comparison_status=NOT_COMPARABLE`、`group/rank=null`，不是第四档投资意见，也不等于“基本面差”。普通企业可保留质量观察但不虚构情景。可比较回报仅限现有五年USD上市股权、现金分红不再投资的条件研究口径。未来其他方法须另有可复算输入和同口径回报合同，本轮没有实现替代估值。未正常化GAAP、分部/长期竞争优势和远期预期仍需单独证据，不把模型参数当成事实。

最新main的XPEV Owner变更影响旧P1整文件哈希；适配仅接受精确已验收PR66 `official_disclosures.py` 哈希，原字段重核仍须一致，不广泛放行未知历史版本。旧工件读回不变，完整回放用其原源码；新方法独立标准库复算验证适用性、质量算术、情景及排名。实际来源验收、方法矩阵、兼容性与测试收据在仓外交付，不以合成规则测试冒充TSLA/BRK.B真实验收。

### S3-C实际补源续验（SEC私有配置已初始化）

开发子进程私有身份校验与五次限定SEC原件读取成功；身份值不入报告/Git。S3-C方法显式升为`s3-c.method-applicability.v1.1`：原SEC Owner增加晚间接受/后续官方filed的保守资格边界，见[来源说明](../V42_US_FINANCIAL_SOURCES.md#sec晚间接受与官方filing-dates3-c限定补源)。原五年估值、S3-B质量规则、敏感性与阈值不变；已有v1工件保留，用原3d52789源码回放，不覆盖旧方法哈希。

新增来源必须用晚于实际fetch的共同截止。原AAPL/NVDA价格在更晚截止可能失去Owner资格，此时估值与排名保持UNKNOWN/暂不可比，不移动旧价格时间或付费补价。其原截止的完整历史回放独立保留。TSLA仅合并集团GAAP及经营观察，缺少分部、偿债/现金桥、合格价格/假设时保留UNKNOWN；BRK.B实际SIC6331不进入普通筛查/盈利倍数，类股经济权利/经营盈利桥及替代估值仍未取得资格。无须强行完成四家公司估值或排序。

### S3-C PM定向审查及集成候选

风险L2，最新集成基线5571087，保留已批准dc7a91e及旧工件；集成分支重放原四项提交，不重写已批准历史。PM授权Push/Draft PR、独立Review及受保护main集成；未授权部署、生产MCP、券商/持仓/投资政策或付费研究。

定向审查仅扩原SEC Owner：真实accepted_at与保守published资格上界分开；正式原文分别核验SEC-HEADER ACCEPTANCE-DATETIME及FILED AS OF DATE。UTC/工作日守卫覆盖同日、跨日和缺接受时间路径；缺失采用日末上界。复用现有pandas联邦假日表，不用NYSE日历；临时关闭等无法证实的日期不推断，继续拒绝，不新建SEC时间系统。

真实Alphabet封面证明注册列表含普通股及存托优先股，故新增中性registered_tickers，旧registered_equity_tickers只兼容旧合同，不宣称其证券类型。CompanyFacts泛化EPS继续拒绝；原Owner只从同一已核验正式主文件的DEI ticker/股票标题、唯一类股轴及USD/shares直接事实恢复目标类别EPS。真实GOOG/GOOGL值有差异，不能同额或ticker特判放行；输入资格flag不可信，记录出口重新验证原件，类别投影记录ID含ticker。此为来源适配，不是新财务/TTM引擎；S3自身仍不消费未适配的多注册证券EPS，不强行产生GOOG估值。

方法升为`s3-c.method-applicability.v1.2`；旧v1/v1.1工件用原源码回放，五年估值公式/阈值、S3-B质量规则不变。真实原始回归材料只存仓外交付，仓库只加合成边界测试和来源定位，禁止真实身份/Token/runtime-env进入Git。当前四公司无同一时点合格回报仍不排名；原AAPL/NVDA截止单独复算。旧L1 200ms差异保留，不因新一轮PASS声称根因已修复。

## S3-D 本地实施方案（2026-10-31）

风险L2；基线`f232697873d7a8572f55bcb1a22ce11b4aaf3d49`。目标是在实际抓取完成后的共同决策时点，使至少两家真实公司具备合格来源、显式五年研究假设、条件回报及分档排序。非目标：生产政策、持仓/订单、PEER编排、新行情/财务引擎及BRK完整估值；本轮仅本地提交，不Push/Merge/Deploy。

Price由原AVKlineProvider/technical_evidence/Evidence负责，SEC由原私有身份入口与SEC/Evidence负责，TTM/确定性计算仍归financial_research。复用S3-B门禁、S3-C适用性、原archive与独立复算器。最小增量是原内部CLI的显式采集/冻结适配、带证据引用和不确定性的假设合同、同点重核与版本差异表达，不复制解析/TTM。已有冻结财务原件优先复用并重新资格化；不得移动抓取时钟。真实数据失败逐公司诊断，合成反例只用于边界测试。

验收覆盖真实两家以上可比、TSLA假设可变更、BRK拒绝、部分失败、质量与敏感性门禁、确定性及历史A/B/C回放，执行治理规定完整本地Gate。原始证据与工件在仓外`~/.codex/outputs/s3-d-real-pool-20261031`，身份与API凭证不入Git或报告。

### S3-D 内部入口与事实边界

`pool --symbols AAPL NVDA TSLA BRK.B --purpose ... --sources <冻结原始目录> --archive <研究目录>`会在真实运行时选择共同decision_at，逐公司经原Owner核验，生成透明候选参数并比较冻结。新采集显式改用`--capture-root <新目录>`；可给`--reuse-sources`保留既有原件的真实时钟，仅取缺项。SEC调用通过`scripts/sec_dev.py run -- ...`，AV仅使用既有环境或显式`--av-env-file`中原Owner允许的键，不载入其他配置。采集直接使用AVKlineProvider与SEC _fetch，不调broker registry/LLM/业务数据库。

`recalculate <bundle.json> --assumptions <覆盖文件> --pool-version ... --archive ...`先验证原冻结版本，再以同一源文件/决策截止重核并替换显式参数。覆盖文件须同时提供完整companies及assumption_context，不将旧自动参数理由冒充新研究结论。原read/replay/diff沿用；命令返回可比数量。1–20家为合同容量，真实通过范围以收据为准。

候选参数方法`s3-d.transparent-candidates.v1`：以最多两个合格历史季度收入同比均值为盈利增长研究锚点，基准限于−10%至25%，谨慎减8pp、乐观加10pp；不宣称收入等于盈利或未来增长。三情景相对股数变化+1%/0/−0.5%，期末PE15/20/30，未来分红建模为0仅表示保守省略未核实分配；不能解释成公司不派息。统一10%要求回报仍是研发标尺，原−10%谨慎门槛、5pp敏感性、并列规则不变。所有未来参数均为RESEARCH_ASSUMPTION；历史观察快照与原质量结果严格相等；管理层指引NOT_USED，证据仅作背景时显式标注，不能证明倍数/股份/分红假设。

S3-D固定34位Decimal上下文与原估值Owner一致，避免初步质量观察与最终计算继承不同调用方精度。新方法`s3-d.real-pool.v1`单列，原S3-A/B/C不改算法；历史工件以原Git源码回放，独立标准库核验新增原始收盘/拆股/参数链。原始DAILY与复权价格不混用；以SPLITS历史事件审核选定EPS期间内的类股可比性，存在拆股则保留UNKNOWN等待原Owner桥，不自行换算。该历史feed不保证所有公司行动覆盖。

实际TSLA材料若未提供Owner可解析的Q4 EPS，不能以全年减前三季或非GAAP数替代；仍保留可检查、可修改的五年候选参数及缺口，不捏造回报。金融保险方法不适用不会因为正EPS或有价格被纳入普通估值。未来参数与GAAP事实、正常化UNKNOWN及质量警示均分开。

## S3 一期 ChatGPT / PEER Skill 接入

S3-D 已由 PR #77 合入 `3f869982e48a26971385b43449176abebee65049`。一期产品接入只增加可选 MCP 包装，复用原 `PositionJobs`、`BusinessClient`、单次隔离 Worker、`invoke_skill` 和 S3 服务；不增加 Agent Runtime 或数据库。入口为 [company_pool](../../pocs/chatgpt_skills/company_pool.py)、[研究适配](../../backend/services/s3_research_entry.py) 及内部 [wp-compare-quality-valuation](../../skills/wp-compare-quality-valuation/SKILL.md)。原单标 Evidence Skill 不承载多公司版本化比较，因此新增 Skill 仅包装原 S3，不复制财务或估值计算、不改既有 Skill 契约。

仅当操作者显式提供 `--s3-archive-root /private/s3/archive` 时，原 ChatGPT Skills MCP 增加三个工具，默认工具集合保持不变：

- `start_company_pool_research(request_id, request={symbols, purpose})`：接收用户公司池及研究目的，异步启动原 Worker，公开来源只读采集，显式写私人研究归档；故 `read_only_hint=false`。不读持仓、不调用 prepare、不创建 E1 判断或订单。
- `get_company_pool_research_result(job_id)`：复用原有界进程内收据，读取同一个结果；断连不取消研究，不自动重新启动。结果到期或重启后 unknown，不得据此重跑。
- `read_company_pool_research(artifact_id)`：在固定私人归档根下只读哈希目录，验证原输入、来源与报告并重算。历史方法漂移保留原结果及 `METHOD_DRIFT_REPLAY_REQUIRES_ORIGINAL_VERSION`，不以新方法替换旧值；原 CLI/独立脚本仍可回放对应版本。

用户可以直接问：“帮我比较 AAPL、NVDA、GOOG、MSFT、META、TSLA，从企业质量、估值吸引力和未来五年潜在回报来看，哪些值得优先考虑？”ChatGPT 选择上述工具并维护收据，不要求用户构造 CLI 或记住内部 ID。必须展示六张卡、合格公司分档/档内排名、不可比清单、假设依据与不确定性。自动候选参数是研发假设，未证明远期盈利；公司研究优先级不等于组合配置，实际配置资格始终 UNKNOWN。

MCP 返回保留全部分档、门禁、情景、敏感性和警示；重复财务输入树、原始GAAP序列及未被引用的历史目录仅存完整冻结工件。引文以别名去重，保留原Evidence ID、公开来源URL/hash和定位；完整结果hash随返回提供，不修改计算结果。synthetic及采集/冻结重核模式随payload内容哈希冻结，读取时不得因服务配置改变而升级为真实。旧S3 CLI工件继续沿原版本回放，不冒充带一期入口provenance的新工件。

MCP 请求不接受来源路径、财务数字、研究时钟、投资政策或订单参数。可选操作者 `--s3-source-root` 用于已冻结来源重核；`--s3-reuse-sources` 用于原采集器公开原件种子。实时采集仅在一次性子进程进行，避免原采集器的临时环境/HTTP拦截影响并发服务。`decision_at` 在来源获取/读取后使用真实当前时间，旧源过期继续拒绝；不倒填历史时钟。标准 SEC 配置沿 [开发 SOP](../dev_setup.md#sec-官方来源开发配置)，现有 analysis-env JSON 仍仅按原白名单加载；冻结重核与归档读取禁止网络，不打开原业务配置/数据库。

这是确定性 Research Only Skill。`professional_analysis_complete=false` 明示未宣称完成四 Agent 模型审查，不伪造 Planning/Executing/Expressing/Reviewing PASS。有限 US GAAP 非金融筛查、条件盈利倍数情景及候选参数的全部原限制不变；无新增收益资格、生产交易或投资政策权限。

### 一期验收与生产发布边界

本地可使用真实公开来源完成六公司 MCP 协议→隔离 Worker→原 Skill→S3→冻结→新进程读回及独立脚本复算；合成协议/失败测试与真实来源验收分别留证。源码/loopback PASS 不等于 ChatGPT 线上验收。至少两家有同一合格时点的真实五年条件回报即可；其余保留精确 Owner 缺口，TSLA Q4 EPS、BRK 专用估值、DCF/全行业 ROIC 不再阻断一期。

正式生产部署、MCP 注册/工具刷新、Mini 服务重启及 ChatGPT 线上调用仍需原发布授权。本任务不执行这些动作。获授权后的最小发布步骤：

1. 依 [既有组件发布流程](../PROJECT_GOVERNANCE.md#github-集成与-mac-mini-部署)，验证最终 main exact SHA 与四项 CI，在 Mini 原 MCP 组件安装审核版本；不启动第二套 backend、不改数据库。
2. 为原服务属主配置独立私人 0700 S3 归档目录；核对 Mini 自己的 SEC 身份、原 analysis-env 中 AV 权限与网络。MacBook 私有配置不自动共享；不复制完整环境文件。必要公开冻结原件可作为种子，仍逐项重核时钟/hash/来源。
3. 只为原 MCP 启动参数增加 `--s3-archive-root`（及明确需要的公开源种子），保持既有读取/prepare/E1边界和权限。新工具 start 仅写研究目录，不能沿用“所有工具都只读”的旧描述；需要显式批准该研究归档写权限。核对原工具全集加三项（全配置原18项时共21项；其他 opt-in 另计）。
4. 经批准重启原组件、刷新连接后，在真实 ChatGPT 发起一次上面的六公司问题。保留同一 request/job ID，六家公司都应留卡，至少两家可比较；检查来源、决策时钟、参数与方法、公司/配置区别。保存 artifact_id，再读取及独立复算。不得创建订单或修改政策。
5. 如失败，撤回 S3 可选参数并恢复原组件版本；不删除原冻结研究、不迁移 DB、不重跑未知付费任务。记录真实线上结果后才可声明 RELEASED。

### 一期正式结项（2026-11-01）

**`WEALTHPILOT_S3_V1_RELEASED`**。PM 在本轮用户确认中按原冻结标准批准一期最终产品验收通过并正式结项；生产版本为 `0ce2e392e9e76f43802d749788a1ed3a9c311848`（PR #77/#78）。本节记录已完成的线上验收，不改变前述实施和发布准备阶段的历史记录。

验收依据：真实 ChatGPT 已完成 `start_company_pool_research` → `get_company_pool_research_result` → `read_company_pool_research` 全链路；AAPL、NVDA、GOOG、MSFT、META、TSLA 六家公司 6/6 保留，AAPL/NVDA 两家形成合格可比结果，其余四家保留具体 UNKNOWN 及不可比原因。生产冻结 `artifact_id`：

`ef1e8ba2d8c4c36ba747f0844513dfafdbe5c185cf2d1a9f96d96d319da8717f`

PM 确认生产读取已独立验证 `verified=true`。本次文档收口引用该确认及既有部署记录，不重新抓取来源、重算或重跑六公司研究；来源时钟、研究假设、方法哈希及质量警示继续以该不可变工件为准，不用结项日期替换原始观察时点。

边界继续为 **Research Only**：不使用持仓，不获得订单、交易、自动调仓、投资政策修改或正式组合配置授权；`professional_analysis_complete=false` 的确定性 Skill 边界不因产品结项而升级为四 Agent 专业审查。可比条件回报不是收益承诺，不将缺证公司补造成可比。

GOOG、MSFT、META、TSLA 的财务缺口，以及长期研究假设校准，记录为**非阻断后续维护事项**。保留原 Owner、字段级 UNKNOWN、具体不可比原因和假设不确定性；不据此开启新研发阶段，也不要求为一期结项补齐完整 DCF、全行业 ROIC 或专用模型。

本次仅更新既有发布记录和能力文档：不重新部署、不修改政策、不调用模型/Provider、不写业务/E1记录、不创建 Tag。

## P0 AV数据链路维护

原AV Owner将明确日额度、分钟限频与Premium端点权限分开诊断，`rate_limited`附`rate_limit_scope`，凭证无效单列`invalid_key`。正文中的Premium销售链接不改变真正拒绝原因；HTTP200仍须业务数据成功。只有明确full Premium权限拒绝沿原策略降级compact，额度或凭证错误不做该降级。

S3采集保留失败HTTP原件于请求审计，但不会把供应商提示放入可用SPLITS。复用AV日线/拆股前，原Owner解码器重核身份与数据形状；失败正文允许重新采集，原件与原抓取时钟保留，哈希冲突和股份/因子冲突继续阻断。原SEC/价格时效及质量/估值门禁不变。

本次不改变质量、估值或情景参数版本；来源处理代码的内容哈希随修复改变，历史冻结工件保持不可变，并继续在其原源码版本或自带独立脚本回放。生产配置仅经原Owner更新AV槽位，原私人配置在Mini本地备份，不依赖MacBook Shell覆盖；新AAPL/NVDA生产验收的版本与收据另行留存，不改原8公司失败工件。

## META 已知资本结构资格修复（2026-11-03 候选）

P2.1发现的资本结构资料不要求整套P2盈利模型上线。现行P1只核当前注册证券，尚不能把未来Class A+C派发、强制可转优先股/存托股份及forward结算转换成原普通股持有人的完整五年回报。此处只修研究资格，不改变盈利增长、15/20/30倍候选、10%标尺、质量四底线或交易权限。

原[适用性Owner](../../backend/services/market_data/method_applicability.py)增加有界、人工核验的CIK权益notice，复用P2.1已确认的两份SEC完整原件（accession `0001628280-26-066700`、`0001193125-26-438017`）。公开原件和manifest保存在[原SEC目录下](../../research_v2/qualified_notices/meta-capital-structure.json)，不是新事件扫描器、财务资料库或投资政策。manifest pin、解压全文hash、原SEC canonical URL/CIK/accession/acceptance/filed/fetched资格逐项验证，按合格issuer identity匹配，不靠ticker打分或猜股份价值。

公开和实际抓取时钟均不晚于新decision_at才能引用notice。早于公告不注入未来信息；公告后但notice尚未被合格抓取的历史时点保留具体资格UNKNOWN，不冒充完成全事件筛查。原件缺失、损坏、hash或CIK冲突继续拒绝，不能当“没有事件”。限制不在生效/record/payment日自动解除，必须由原Owner补齐A+C、优先分配/转换及forward股份结算权益桥，或提供有源取消证明后另行版本化。

质量筛查与财务事实不变；该notice使earnings_model为INSUFFICIENT_EVIDENCE、企业仍保留研究卡。原method/pool/MCP链一致跳过候选参数与估值，不输出旧assumptions/context、条件价、五年回报、PE比较信号或名次。估值不可比不是基本面恶化，不按A/C各半价值、股数翻倍或股价减半计算。AAPL/MSFT/NVDA无该有源限制，不受全局阻断。

方法版本为`s3-c.method-applicability.v1.3` / `s3-d.real-pool.v1.1`；原盈利结算和研发参数版本不变。现有内容寻址归档同时冻结规则、manifest和两份公开原件，标准库独立复算核验资格与来源hash。旧冻结工件不改写；当前读回在方法漂移时只声明integrity_verified，原源码/独立脚本另行复算，不把旧META回报重认证成当前可投资研究。

[专项测试](../../tests/test_s3_meta_entitlement_qualification.py)覆盖真实公开原件及合成财务边界，二者不混称真实公司估值验收。生产仍须按原start→get→read验证四公司、META拒绝/质量保留及其他三家能力，记录exact部署SHA和旧工件hash。既有main的并行4.2 Owner改动继续保留，不集成P2/P2.1盈利方法，不改账户、ACTIVE政策、E1或订单系统。
