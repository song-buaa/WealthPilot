# S3-A 冻结公司池质量与估值研究

2026-10-31，Research Only集成候选。基线PR #66 `1ce59ae58c234777c1e4bd9d3697acdf4f649ab3`。没有生产接线、MCP/Web入口、新Skill、模型调用或交易权限。

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
