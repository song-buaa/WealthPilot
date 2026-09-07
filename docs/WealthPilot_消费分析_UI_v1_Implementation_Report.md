# WealthPilot 消费分析 UI v1 实施报告

## Baseline

- Analytics implementation `c0027b5ee091a1169910581a2e2d84c1f65386b4` 已经以 ff-only 收口至 `main` 并推送。
- UI branch：`codex/consumption-ui-v1`。
- Start HEAD：`c0027b5ee091a1169910581a2e2d84c1f65386b4`。

## API

页面只在进入时调用一次 `GET /api/consumption/analytics?months=12`。TypeScript response contract
保留后端 Decimal 字符串；每个 `MonthlySpendingPoint` 带有该自然月的二级分类明细，原有 response
顶层 `secondary_breakdowns` 仍保留为请求范围聚合，兼容既有调用。前端仅做展示格式化与已冻结的
“类别金额 / API 总额”占比呈现；不读取 Raw/Event，不自行归月、退款处理或分类聚合。

## Page structure

`/consumption` 从 shell 升级为实际的消费分析页：

1. 本月概览：总消费、当前月/as-of、分类覆盖率、数据覆盖和可用完整月均；
2. 近 12 个月 Recharts 堆叠柱状趋势，包含日常、旅行、住房和待分类；
3. 可访问的月份按钮直接切换已返回 DTO 的月度详情；
4. 四项消费结构卡；
5. 当前选中月的二级分类 progress list；
6. 覆盖/金额完整性说明，以及 Eligibility 与 Classification Review 的独立摘要；
7. skeleton loading、空态、错误提示与 retry。

图表 Tooltip 显示月份、总消费、四个分类和分类覆盖率；金额不完整时明确说明当前为已知金额。
二级分类由 API 的月度 DTO 返回；前端不会以 Raw/Event 或顶层 12 个月聚合重算月度明细。

## Coverage rendering

| API status | UI 文案 |
|---|---|
| `COMPLETE` | 数据完整 |
| `PARTIAL` | 数据未完整 |
| `SOURCE_LIMITED` | 来源无法确认完整性；说明基于已解析交易范围 |
| `UNKNOWN` | 数据覆盖未知 |

非完整状态使用“基于已接入账户”的定位，不宣称全部消费。页面将 `分析日期` 与数据覆盖状态
分别展示，明确 as-of 是统计日期而非完整覆盖承诺；API 未允许比较时不展示误导性的环比结论。

## Real-data local validation

当前 Bootstrap 的本地 `data/wealthpilot.db` 经由 Analytics API 与 `/consumption` 页面复核 **PASS**：
7 月 / 8 月切换、月度二级分类、分析日期与覆盖状态分离、四段结构、分类覆盖率和待分类摘要均按
既有口径呈现。未重新导入或修改真实账单数据，浏览器没有 fatal error。

## Privacy

- real screenshot committed: **NO**
- real amount fixture committed: **NO**
- raw data exposed: **NO**
- real source / identifier / description logged: **NO**

## Isolation

- Pattern modified: **NO**
- IA modified: **NO**
- Investment modified: **NO**
- Analytics schema modified: **NO**
- Analytics calculation semantics modified: **NO**

## UI v1 review fix

- 月份详情采用统一的 `selectedMonth` 时间上下文：7 月与 8 月切换时，二级分类标题和数据均随月度 DTO 更新，不再混入 12 个月聚合。
- 后端仅扩展 read-only response contract，为月度点附加已分类二级明细；月度总额、DAILY / TRAVEL / HOUSING、待分类、Eligibility、退款、FX 与分类口径未变。
- `分析日期：YYYY-MM-DD` 与 `数据覆盖` 独立呈现。`SOURCE_LIMITED` 继续使用信用卡账单周期不可确认的轻量说明；不会因 observed transaction date 显示“数据完整”。
- 真实本地 SQLite 页面复核通过：7 月 / 8 月二级分类切换、as-of 与 coverage 分离、主消费指标保持既有口径，浏览器无 fatal error。
- 回归：消费后端 53 passed；消费 UI 6 passed；导航 2 passed；Pattern Evidence 6 passed；full pytest 880 passed / 7 skipped；Offline M5 18/18；frontend lint/build、compile/import 与 `git diff --check` 均通过。

## Open items and readiness

- 详情级 review queue、分类编辑和规则管理仍是独立后续范围；
- AI Insight、merchant ranking、预算、预测、现金流与历史 FX 均未实现；

Next readiness: **READY_FOR_USER_UI_REVIEW**.
