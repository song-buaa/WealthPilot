# WealthPilot 消费分析 UI v1.1 月度明细与房租规则报告

## Baseline

- Start HEAD: `c0207900b2702c8987c5288e83f7b5356a4d6c48`
- Final HEAD: `HEAD` at this closeout commit
- Branch: `codex/consumption-ui-v1`

## UI Design Alignment

消费分析页面按已完成的投资账户总览审计结论对齐 WealthPilot 视觉语言。

- KPI: PASS — 2fr 主深色 KPI 加两个 1fr 白色次 KPI。
- Card: PASS — 统一白底、边框、圆角与轻阴影。
- Typography: PASS — 使用既有 PageHeader 和数值层级。
- Table: PASS — 紧凑、可滚动、表头固定、金额右对齐。
- Chart: PASS — 保留 12 月堆叠趋势并使用同一 Card 处理。
- Badge: PASS — 覆盖状态与分类状态使用语义胶囊标签。

## Monthly Detail

- Endpoint: `GET /api/consumption/events?month=YYYY-MM&limit=&offset=`
- Columns: 日期、安全消费名称、一级分类、二级分类、安全账户标签、人民币金额、分类状态。
- Ordering: 活动投影金额降序、分析日期降序、事件 ID 升序。
- Bounded query: PASS — `limit` 为 1–200，默认前端请求 200 条。
- Privacy: PASS — 响应不含 Raw 描述、对手方、证据或完整账号；显示名称为确定性安全语义标签。

## Rent Rule

local user rule applied: YES

matched historical event count: 4

HOUSING / RENT replay: PASS

规则与其匹配文本仅保存在本地 SQLite；未进入 Git、源代码或本报告。

## Real-data Validation

rent appears in expected historical months: PASS

housing updated: PASS

monthly detail rendered: PASS

真实页面验证未记录真实消费金额、账单原文或对手方。

## Validation

- Consumption UI targeted tests: 6 passed
- Consumption detail / rule API tests: PASS
- Consumption backend tests: 165 passed
- Full pytest: 880 passed, 7 skipped
- Frontend lint: PASS
- Frontend build: PASS
- Navigation tests: 2 passed
- Pattern Evidence frontend regression: 6 passed
- Offline M5: 18/18 passed
- Compile / import: PASS
- `git diff --check`: PASS

## Isolation

Investment modified: NO

Dashboard modified: NO

IA modified: NO

Pattern modified: NO

Analytics semantics modified: NO

Classification semantics modified: NO

## Next Readiness

READY_FOR_USER_UI_REVIEW
