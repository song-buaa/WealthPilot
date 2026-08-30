# WealthPilot 消费分析 UI v1 实施报告

## Baseline

- Analytics implementation `c0027b5ee091a1169910581a2e2d84c1f65386b4` 已经以 ff-only 收口至 `main` 并推送。
- UI branch：`codex/consumption-ui-v1`。
- Start HEAD：`c0027b5ee091a1169910581a2e2d84c1f65386b4`。

## API

页面只在进入时调用一次 `GET /api/consumption/analytics?months=12`。新增强类型
TypeScript response contract，保留后端 Decimal 字符串，前端仅做展示格式化与已冻结的
“类别金额 / API 总额”占比呈现；不读取 Raw/Event，不自行归月、退款处理或分类聚合。

## Page structure

`/consumption` 从 shell 升级为实际的消费分析页：

1. 本月概览：总消费、当前月/as-of、分类覆盖率、数据覆盖和可用完整月均；
2. 近 12 个月 Recharts 堆叠柱状趋势，包含日常、旅行、住房和待分类；
3. 可访问的月份按钮直接切换已返回 DTO 的月度详情；
4. 四项消费结构卡；
5. 近 12 个月的二级分类 progress list；
6. 覆盖/金额完整性说明，以及 Eligibility 与 Classification Review 的独立摘要；
7. skeleton loading、空态、错误提示与 retry。

图表 Tooltip 显示月份、总消费、四个分类和分类覆盖率；金额不完整时明确说明当前为已知金额。
二级分类是 API 当前请求范围的已有 breakdown，未伪造月度级二级分类数据。

## Coverage rendering

| API status | UI 文案 |
|---|---|
| `COMPLETE` | 数据完整 |
| `PARTIAL` | 数据未完整 |
| `SOURCE_LIMITED` | 来源无法确认完整性；说明基于已解析交易范围 |
| `UNKNOWN` | 数据覆盖未知 |

非完整状态使用“基于已接入账户”的定位，不宣称全部消费。当前月会显示 as-of 截止日期，且 API
未允许比较时不展示误导性的环比结论。

## Real-data local validation

三类用户提供账单只在独立临时 SQLite 中运行：Adapter → Raw persist → Event normalize →
Classification resolve → Analytics API → `/consumption`。本地页面验证 **PASS**：趋势、四段结构、
分类覆盖率、待分类金额、当前月 partial、SOURCE_LIMITED 覆盖文案、Review 分离及月份切换均正确。
借记卡 OTHER 未进入消费结构，且页面未出现退款导致的负消费月。验证完成后临时数据库和本地服务已关闭并删除。

## Privacy

- real screenshot committed: **NO**
- real amount fixture committed: **NO**
- raw data exposed: **NO**
- real source / identifier / description logged: **NO**

## Isolation

- Pattern modified: **NO**
- IA modified: **NO**
- Investment modified: **NO**
- Analytics backend/schema modified: **NO**

## Open items and readiness

- 详情级 review queue、分类编辑和规则管理仍是独立后续范围；
- AI Insight、merchant ranking、预算、预测、现金流与历史 FX 均未实现；
- 二级分类的月度切片需由未来专用 read contract 提供，当前页面忠实展示 API 返回的请求范围 breakdown。

Next readiness: **READY_FOR_USER_UI_REVIEW**.
