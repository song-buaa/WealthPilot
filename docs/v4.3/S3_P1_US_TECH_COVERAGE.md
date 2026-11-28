# S3 P1 美国科技公司研究覆盖

状态：开发候选，Research Only；源码与离线真实来源结果不代表已部署。沿用[S3-A](S3_A_IMPLEMENTATION.md)的四底线、五年情景、敏感性和分档公式；不修改投资政策、持仓或交易权限。

## 定点缺口与复用

- META：8-K主文将同一99.1附件标题拆成多个同目标锚点。原SEC Owner的`_declared_exhibit`按精确目标去重，仍拒绝同一行包含其他目标。GAAP损益表标题位于表内，不能由前面的非GAAP一般免责声明替代其实际资格。
- MSFT：正式`INCOME STATEMENTS`标题及`$4.81`同格美元展示。原`parse_us_gaap_diluted_eps`适配排版，仍要求独立SEC季度边界、年度EPS交叉证明、正式声明附件和单季原值。不以年度减前三季制造Q4。
- GOOG：Companyfacts未承载目标类别维度。原S3采集包装对多注册证券有界补取最近三份10-Q及一份10-K，交给原`_direct_listed_class_eps`解析。已有8-K不能阻止缺失类别报表补采；确切URL去重。原件可证明Class C季度/年度EPS，不等于四季度TTM或股份转换资格。缺Q4、方法适用性仍保留UNKNOWN。
- TSLA：本轮对应正式Q4附件的财务信息为图片展示，当前Owner不解析图片。既有季度事实保留，不能用摘要、非GAAP数或估算补齐GAAP Q4；不引入OCR或第二套财报引擎。

## 安全与回归

原SEC时钟、原件hash、币种/单位、股份类别及Evidence资格门槛不变。表内标题不能覆盖明确相邻non-GAAP/reconciliation/adjusted结构标题；缺年度交叉证据、未来期间或歧义附件继续拒绝。

实现：[SEC原Owner](../../research_v2/sec_disclosures.py)、[声明表格适配](../../research_v2/official_disclosures.py)、[原池采集包装](../../backend/services/s3_pool_capture.py)。

实际原件离线回归：[测试](../../tests/test_s3_p1_us_tech_eps.py)、[公开SEC冻结来源](../../tests/fixtures/s3_us_tech_eps/README.md)。夹具只含官方原文及其hash，不包含AV受限行情、身份配置或私人账户。历史冻结工件不覆盖。

本轮六公司比较及前后对照使用同一manifest、同一decision_at及原参数生成方法。可比数量指获得合格估值输入并可执行原情景计算，不等于四项质量底线全部通过。四底线逐项UNKNOWN/警示、GAAP一次性项目、收入增速不等于未来EPS增速、终值倍数/股份/分红假设及排序不稳定必须保留。独立复算只证明冻结输入下的算术一致性，不证明预测正确或未来收益。

## 有界发布与验收

按原受保护PR/四项CI/独立Review流程；发布仅使用确认main SHA和原Mini install-github/现役Skills入口，检查现役版本无倒退及无运行中的研究Worker，再按原配置切换，保留原入口回滚。不得迁移或写业务/E1/政策库，不调整Key。限定原六公司公司池研究→冻结读回→独立复算；失败保留事实，不通过重跑PEER/交易补救。若个人SSH master失效，仅由用户按原SOP续期，不使用公司认证或替代通道。
