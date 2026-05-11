# 盈米 MCP 接入决策备忘

> **文档目的**:在 v2.0 完成、v2.1 启动盈米接入工作时,无需回溯讨论过程,直接进入工程 PRD 阶段。
>
> **决策时间**:2026-04-25
> **关联 PRD**:`investment_research_module_v2_business_prd_final.md`
> **下一步触发点**:v2.0 验收通过 + v2.1 Perplexity 迁移完成(预计 v2.0 上线 + 10 天后)

---

## 1. 背景前提

WealthPilot 数据源拼装方案,按资产类别选最合适的供应商:

| 资产类别 | 数据源 | 状态 |
|---|---|---|
| 美股 | Alpha Vantage | v2.0 接入中 |
| 境内基金 | 盈米 MCP | v2.1 接入(本备忘范围) |
| 港股 | 暂用 Alpha Vantage(`.HK` 后缀)兜底,后续视情况升级 | 不在本备忘范围 |

**为什么选盈米**:首批基金投顾试点机构(第三方仅 3 家)、72 个工具粒度刚好是"方法论模块级"、API Key 免费申请、工具自带买方投顾方法论沉淀。

---

## 2. 三个已锁定的核心决策

### 决策 1:盈米工具按"投研类 / 决策类"两路接入

盈米 MCP 跨越投研和决策两层。**不要全部塞进 ViewpointCard 流程**。

- **投研类**(产出观点和事实):走 Research v2 的 `InfoAdapter → ViewpointProcessor → ViewpointCard` 流程
- **决策类**(产出决策建议、组合分析):**不走** ViewpointCard,直接注册到决策模块 / 资产配置模块作为 Tool

判断层(stance / confidence / endorsement)只对"判断性观点"有意义。把 `GetAssetAllocationPlan` 这种"决策动作型"工具硬塞进 ViewpointCard 会扭曲 Research v2 的定位。

### 决策 2:不全接 72 个,采用"最小可用集 + 增量补"

按需接入,不追求覆盖全。每次补一个工具都是增量,不需要重新设计架构。

### 决策 3:`extracted_kpi` 按资产类别分 schema

基金 KPI 体系和股票完全不同(净值、回撤、夏普、规模、持仓集中度…)。

- **facts 层不强制归一化**(原 PRD 原则不变)
- **`extracted_kpi` 按资产类别走不同 schema**:股票/基金各自一套
- 工程 PRD 里需要写清楚"asset_class → kpi_schema"的对应规则

---

## 3. v2.1 接入范围(6 个纯数据类工具)

### 子 Adapter 拆分

```
yingmi_fund_data
├── BatchGetFundsDetail        # 基础资料(名称、类型、规模、经理、成立日)
├── BatchGetFundNavHistory     # 净值历史
├── GetBatchFundPerformance    # 业绩指标(收益率、波动、夏普,多时段)
└── GetFundDiagnosis           # 单基金全维度诊断

yingmi_market_content
├── SearchFinancialNews        # 财经资讯
└── GetLatestQuotations        # 市场温度与情绪
```

### v2.1 完成后的系统能力

- 用户新增基金持仓 → 自动拉基础资料、净值曲线、业绩指标、诊断报告
- 用户可看到基金侧的资讯流
- **不会**自动产生"判断性观点"(SearchManagerViewpoint 在 v2.2)
- 所有判断仍来自用户上传或对系统建议的 endorse

### v2.1 工程范围速览

| 项 | 内容 |
|---|---|
| 新增 source_type | `yingmi_fund_data`、`yingmi_market_content` |
| 新增 Symbol 后缀 | `:FUND`(6 位数字基金代码) |
| 新增子 adapter | 2 个 |
| 新增工具数 | 6 个 |
| 新增 Router 规则 | `:FUND → Yingmi*`,无降级(盈米是基金侧唯一源) |
| 新增 event_type | 0(基金资讯映射到现有 14 项即可) |
| extracted_kpi 改动 | 增加基金类 KPI schema(候选字段见下) |
| 判断层影响 | 无 |
| 决策模块改动 | 无(v2.1 只动投研侧) |
| 前端改动 | 用户基金持仓页面展示自动拉取数据;Tab 1 候选卡区域出现基金资讯类卡 |

### 基金类 extracted_kpi 候选字段

```
nav_latest                # 最新净值
return_ytd                # 年初至今收益
return_3y_annualized      # 近三年年化
max_drawdown_1y           # 一年最大回撤
sharpe_3y                 # 三年夏普比率
fund_size                 # 规模
top_holdings              # 前十大持仓
manager_name              # 基金经理
turnover_rate             # 换手率
```

工程 PRD 阶段确定:是否在 ViewpointCard schema 上加 `asset_class_kpi` 字段,还是按 source_type 分别处理。

---

## 4. v2.2 接入范围(6 个工具,两条路径)

### 路径 A:进 Research v2 的 ViewpointCard 流程(1 个)

```
SearchManagerViewpoint    # 基金经理观点
                          # 判断性内容,需要用户 endorse
                          # 角色对应 Alpha Vantage 的 NEWS_SENTIMENT
```

### 路径 B:不进 ViewpointCard,模块直接注册(5 个)

```
DiagnoseFundPortfolio     → PortfolioReview 意图
GetAssetAllocationPlan    → 资产配置模块(对接五资产类别框架)
AnalyzePortfolioRisk      → PortfolioReview 意图
GetFundsBackTest          → 投资纪律回测(13 条纪律的反事实模拟,差异化点)
GetFundsCorrelation       → PortfolioReview 意图
```

### v2.2 需要单独写 PRD

v2.2 不是 Research v2 的延伸。建议命名:**"MCP 能力全模块接入 PRD"**,覆盖盈米决策类工具在以下模块的接入:

- 资产配置模块
- 组合诊断意图(PortfolioReview)
- 投资纪律引擎
- 用户画像与投资目标(家庭财务 Analyze* 系列,见决策 5)

### v2.2 上线后的产品体感

同时:
- 基金侧观点开始自动生成(`SearchManagerViewpoint`)
- 基金组合开始有专业级诊断(其他 5 个工具)

是一次有产品感的发布。

---

## 5. 暂不接入(约 60 个工具,有需求再加)

按"有需求再加"原则处理。三个主要类别:

### 5.1 细粒度持仓穿透工具

`getStockAllocationAndMetricsByFundCode`、`getFundIndustryAllocation`、`getFundIndustryConcentration` 等几十个 `getFund*Allocation` / `getFund*Indicator`。

- 大部分场景 `GetFundDiagnosis` 已经覆盖
- 等用户真的问到"我这只基金的行业集中度"再加

**注意**:这类穿透数据**不应进 ViewpointCard 流程**。它们是"决策引擎做组合诊断时调用的实时数据",类似"现在去问基金底仓是什么"。否则会产生海量无用的卡。

### 5.2 基金筛选工具

`filterBondFundByCreditRating`、`filterBondFundByBondType`、`filterStockFundByStockTurnover`、`SearchFunds`、`GetPopularFund` 等。

- WealthPilot 定位是"已持有资产的管理",不是选基工具
- 不是当前核心场景

### 5.3 家庭财务 Analyze* 系列

`AnalyzeIncomeExpense` / `AnalyzeCashFlow` / `AnalyzeFamilyMembers` / `AnalyzeFinancialIndicator` / `AnalyzeAssetLiability`。

- 这是"用户画像 + 投资目标"模块的东西
- 独立于投研和决策
- 等那个模块单独规划时再接,不要混在 v2.1/v2.2 里

---

## 6. 启动 v2.1 前的两个前置工作

### 前置 1:技术 spike(1-2 小时)

进入工程 PRD 之前用一次小 spike 确认:

- API Key 申请流程和额度(免费期限、QPS 限制、月度 quota)
- 6 个目标工具的实际返回 schema 是否和文档一致(实测验证 payload 结构)
- 延迟表现(用 `GetFundDiagnosis` 测一次,其他工具一般)
- 错误处理行为(超限返回什么、参数错误返回什么)

spike 结论用于工程 PRD 的 RateLimits、错误处理、timeout 设置。

### 前置 2:盈米工具完整分类表(可选,半天)

如果想要更系统的视图:对盈米 72 个工具逐一标注"投研类 / 决策类 / 穿透数据类 / 不接"四类。

价值:
- 一次性产出,后续每次决定要不要补新工具时直接查表
- 对外规划产品路线图时很有用

不是必须 —— v2.1 范围已经锁定 6 个,前置 1 完成就可以启动 PRD 了。

---

## 7. 关键架构原则(易忘,提醒自己)

1. **薄 Adapter + 厚 Processor**:盈米子 adapter 只 fetch,加工统一由 ViewpointProcessor 做(原 PRD §3.1 原则,继续遵守)
2. **基金没有 Entity 跨市场聚合**:基金一个代码不会跨市场上市。但有"基金经理 / 基金公司 / 主题"等聚合维度,v2.0 阶段 `entity_id=null` 即可,后续视需求扩展 entity_type
3. **盈米基金侧无降级源**:Router 规则里 `:FUND → Yingmi*` 没有备选 adapter(不像 `:US` 有 Alpha Vantage 失败时降级 Perplexity)。盈米限额时直接返回错误,UI 提示
4. **资产类别识别不能依赖 LLM**:`:FUND` / `:US` / `:HK` 后缀的判断必须在 Router 层用规则做(6 位数字 = FUND,字母 = 股票),不能让 LLM 来分类,会拖慢响应

---

## 8. 风险提示(写工程 PRD 时不要忘)

1. **盈米 TOS 风险**:盈米自己有且慢、启明星 APP,WealthPilot 在产品定位上有重合可能。接入前看清服务条款,确认是否有"不得用于构建竞品"类条款
2. **商业化变化**:目前免费公测期。工程实现时**必须在 Adapter 层做缓存**,以防未来计费按调用次数收费导致成本失控。建议 Redis 缓存,基础资料类长期缓存,行情类短期缓存
3. **数据精度**:盈米的基金诊断、回测等是"产品化输出",不是原始数据。底层口径变化时用户看到的结果可能跟着变。在 ViewpointCard 的 `notes` 字段里要明确"数据由盈米 MCP 提供,口径以盈米为准"

---

## 9. 一句话总结

**v2.1 接 6 个纯数据工具,基金侧"有数据但不自动产观点";v2.2 接 6 个工具(1 进 ViewpointCard + 5 进决策模块),基金侧"开始有判断性观点 + 组合级诊断"。两阶段都不动 v2.0 的核心架构。**

---

## 附录:决策日志

- **2026-04-25**:本备忘创建。三个核心决策锁定。v2.1 范围确定为 6 个纯数据工具(原计划 7 个,`SearchManagerViewpoint` 移至 v2.2 以保持 v2.1 复杂度)
