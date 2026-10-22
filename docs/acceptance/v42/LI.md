# LI 4.2 脱敏开发验收报告

**SYNTHETIC_FIXTURE / NOT_PRODUCTION_ACCEPTANCE**

全部资产、公司目标、账户ref、行情与指标均为隔离合成数据，不是本人账户或实际研究结论。`asset-view:v1:58`在本文仅为夹具标识，不表示读取了生产58。日期固定为2026-10-22；本报告未调用真实专业模型、券商或行情。

本轮生产只读观察仅确认64固定视图存在且partial/已到期/存在来源时间未知；没有建立当前可信资产输入，未重新同步。真实行情、财报、技术数值及精确减仓能力的设备验收仍为NOT_RUN。

①目标配置 / 当前敞口
受限分析：不提供可执行限价或股数。
固定快照：asset-view:v1:58；公司：LI_AUTO。
公司跨账户参考市值 ¥303,800.00；投资资产分母 ¥1,213,000.00。当前参考占比 25.0%；阶段目标 25.0%。
冻结估值不代表实时可交易报价；来源过期时，金额仅作示意。

| 账户 | 市场证券 | 本账户数量 | 参考人民币市值 |
|---|---|---:|---:|
| account_tiger | LI:US | 1000 | ¥120,000.00 |
| account_ibkr | LI:US | 1000 | ¥90,000.00 |
| account_guojin | 2015:HK | 1000 | ¥90,000.00 |
| account_guojin | 2015:HK | 1000 | ¥3,800.00 |

②经营与估值事实
缺少当前可核验的经营或估值事实。旧观点和Seed仅保留在来源记录，不作当前估值。

③技术趋势与关键位
TECHNICAL_UNAVAILABLE：技术观察位为空，先补可核验的日线、周线及来源。

④事件催化
事件证据不足，需核验正式披露和公告日期。

⑤多情景减仓建议
原专业链路方向：减仓研究（分析推断，不能独立授予交易资格）。
用户确认目标版本：fixture-v1，阶段：transition。
- 阶段 1：复核最新正式经营披露、配置目标和账户限制后，人工评估下一阶段；目标不自动强平。目标占比 25.0%，并非无条件卖出要求。
确认的资金去向规则：account_tiger → 境外固收；account_guojin → 偿还人民币贷款。

静态情景测算（示意、证据资格不足，不是收益预测或交易指令）：
- MEITUAN / account_guojin：假设卖出 ¥161,400.00，投资分母 ¥1,051,600.00，固收占比 21.7%，负债 ¥88,600.00，净资产 ¥1,250,000.00；假设：Synthetic static CNY marking; zero costs assumed, transfer eligibility unverified。
- PDD_HOLDINGS / account_tiger：假设卖出 ¥81,500.00，投资分母 ¥1,051,600.00，固收占比 29.4%，负债 ¥88,600.00，净资产 ¥1,250,000.00；假设：Synthetic static CNY marking; zero costs assumed, transfer eligibility unverified。

⑥失效 / 复核条件
- 反弹未出现：在复核期限重新评估配置与机会成本，不无限期等待成本价。
- 经营恶化：先验证正式披露与原论据失效，再人工复核退出方向。
- 贷款到期或流动性压力：核实余额、期限和可划转条件后优先复核偿债。
最迟复核：2026-10-29T07:30:00+00:00；采用默认七日复核政策，不是必须卖完的日期。

⑦来源、日期、质量警告
| 类型 | 来源 / 记录 | 数据日期 | 状态 |
|---|---|---|---|
| fundamentals | alphavantage | seed:2026-06-11 | 冲突 |
| technical | broker:LI:US:day:2026-10-21 | 2026-10-21 | 未核验 |

规划器：fallback；规划降级与研究缺证分别判断。
缺口 / 告警：ASSET_AMOUNT_QUALIFICATION_REQUIRED、CAN_PROPOSE_REBALANCE_FALSE、EVENT_UNAVAILABLE、EVENT_VERIFIED_EVIDENCE_REQUIRED、FUNDAMENTALS_FETCH_TIME_UNKNOWN、FUNDAMENTALS_PERIOD_UNKNOWN、FUNDAMENTALS_PROVENANCE_UNVERIFIED、FUNDAMENTALS_SOURCE_DATE_UNKNOWN、FUNDAMENTALS_SYNTHETIC、FUNDAMENTALS_VERIFIED_EVIDENCE_REQUIRED、PLANNER_FALLBACK、PRICE_OUTSIDE_52W_RANGE、PRINCIPLES_RETRIEVAL_UNAVAILABLE、QUOTE_UNAVAILABLE、QUOTE_VERIFIED_EVIDENCE_REQUIRED、RESEARCH_UNAVAILABLE、RESEARCH_VERIFIED_EVIDENCE_REQUIRED、TARGET_DISCIPLINE_CASH_CONFLICT、TECHNICAL_SYNTHETIC、TECHNICAL_UNAVAILABLE、TECHNICAL_VERIFIED_EVIDENCE_REQUIRED、VALUATION_CURRENCY_UNVERIFIED
L4未授权；本报告未创建、准备、确认或提交订单。

## 技术算法的独立合成验证

下列为合成OHLCV的重算结果，仅验证指标计算与参数/区间，不授予真实证据资格。业务报告正确拒绝Mock指标作为当前事实。

| 周期 | 最后完成区间 | MA20 | RSI14 | MACD |
|---|---|---:|---:|---:|
| day | 2025-06-02—2026-10-21 | 20.8000 | 53.6745 | -0.0394 |
| week | 2025-06-06—2026-10-16 | 20.7950 | 48.9725 | -0.0463 |

方向性研究：可用但受限。可信价格/数量：不可提供。真实交易/交易准备：均未执行。
