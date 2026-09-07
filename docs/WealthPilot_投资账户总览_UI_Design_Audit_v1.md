# WealthPilot「投资账户总览」UI Design Audit v1

> 性质：只读设计审计。代码与本地 `#/dashboard` 页面是本报告的 Source of Truth；不以历史截图或归档文档推断实现。
>
> 审计基线：`codex/consumption-ui-v1` / `c0207900b2702c8987c5288e83f7b5356a4d6c48`。

## 1. Executive Summary

投资账户总览的设计语言是“**低饱和背景上的高密度金融工作台**”，其成熟感主要来自以下可验证规律：

1. 用浅灰页面底色承托白色内容卡，只有一个深色主 KPI 卡承担第一视觉焦点。
2. 以明确的 KPI 主次比例（`2fr 1fr 1fr`）组织第一屏，而非平均分配所有指标。
3. 所有数据承载面都使用同一组容器信号：白底、`#E5E7EB` 细边、`12px` 圆角、`--shadow-sm`。
4. 数值采用 `tabular-nums` / `fontVariantNumeric: tabular-nums` 并右对齐，使密集数据可扫描。
5. 颜色是语义通道：蓝色表示选择与结构、绿/橙/红表示风险或状态，色彩不用于无意义装饰。
6. 图表只解释结构（配置区间、平台分布）；高密度逐项信息进入可滚动表格，不拆成大量小卡。
7. 标题、辅助说明、计数和状态逐级减弱（ocean 深色 → `#374151` → `#6B7280` → `#9CA3AF`）。
8. 当前实现的“设计系统”以共享 token 和重复 inline style 为主，组件化程度并不完全一致；消费页应先对齐视觉 token 和信息层级，不应借此次机会重构投资页。

## 2. Page Anatomy

```text
AppLayout main（padding: 28px 64px；可纵向滚动）
└─ Dashboard（无自身 max-width，使用可用内容宽度）
   ├─ Page Header（38px 图标 + 标题 + 副标题，bottom 20px）
   ├─ KPI Grid（2fr / 1fr / 1fr，gap 12px，bottom 16px）
   │  ├─ 深色总资产主卡
   │  ├─ 浮动盈亏次卡
   │  └─ 杠杆倍数次卡
   ├─ Analysis Grid（3fr / 2fr，gap 16px，bottom 16px）
   │  ├─ 大类资产配置（目标区间 + 当前点 + 状态）
   │  └─ 平台分布 donut
   ├─ 资产明细卡（标题/计数 → tabs → 可滚动表格）
   ├─ 持仓导入/导出折叠面板
   ├─ 负债明细卡
   └─ 负债导入/导出折叠面板
```

| Layer | Actual layout | Width / grid | Gap / spacing | Audit note |
| --- | --- | --- | --- | --- |
| App content | `AppLayout.tsx` main | `flex: 1`, no content max-width | `padding: 28px 64px` | Dashboard uses the available desktop width. |
| Header | flex row | icon + text | `gap: 12px`, `margin-bottom: 20px` | Dashboard owns a local duplicate rather than importing shared `PageHeader`. |
| KPI | CSS grid | `2fr 1fr 1fr` | `gap: 12px`, bottom `16px` | One primary metric, two supporting metrics. |
| Analysis | CSS grid | `3fr 2fr` | `gap: 16px`, bottom `16px` | Structure analysis has priority over platform donut. |
| Detail | full-width card | table `width: 100%` | card bottom `16px` | Detail density is solved with table + tabs, not cards. |

## 3. Design Tokens

### 3.1 Color roles

| Role | Actual token / hex | Current use | Consumption decision |
| --- | --- | --- | --- |
| Page background | `#F4F6FA` / `--color-ocean-50` | `body` | REUSE |
| Main dark / title | `#1B2A4A` / ocean-800 | titles, PageHeader gradient | REUSE |
| Dark surface | `#1F2937 → #111827` | total-assets primary card | ADAPT, use only for one primary consumption KPI if hierarchy needs it |
| Card surface | `#FFFFFF` | all ordinary cards | REUSE |
| Primary text | `#374151` | section labels, body | REUSE |
| Secondary / muted | `#6B7280` / `#9CA3AF` | helpers, counts, table headings | REUSE |
| Border | `#E5E7EB`; soft separator `#F3F4F6` | card/table boundaries | REUSE |
| Selection / structure blue | `#3B82F6`, `#EFF6FF`, `#BFDBFE` | active tabs, range indicators, buttons | REUSE |
| Positive / in-range green | `#16A34A`, `#059669`, pale `#DCFCE7` / `#D1FAE5` | safe / in-range | REUSE for coverage or confirmed-good states only |
| Warning | `#D97706`, `#EA580C`, pale `#FEF3C7` / `#FFF7ED` | moderate deviation | REUSE |
| Alert | `#DC2626`, pale `#FEE2E2` | risk / high deviation | REUSE only for genuinely alerting consumption states |
| Neutral tag | `#F3F4F6` + `#374151` | asset-class tag | REUSE |

The page feels restrained because its dominant surfaces are neutral; saturated colors occupy dots, pills, selected controls, and one main action. There is no decorative color block outside the primary KPI.

### 3.2 Typography

| Role | Example / source | Size / weight | Color / line-height |
| --- | --- | --- | --- |
| Page title | local `Dashboard` PageHeader | `20px / 700` | `#1B2A4A`, tracking `-0.3px` |
| Page subtitle | `账户总览 · 持仓分析` | `12px / 400` | `#9CA3AF`, top `1px` |
| Section title | `资产明细`, `平台分布` | `13px / 600` | `#374151` |
| Section helper / count | `当前 vs 目标区间`, `201 只持仓` | `11px / 400` | `#9CA3AF` |
| KPI label | `总资产（投资）`, `浮动盈亏` | `11px / 500–600`, uppercase when applicable | muted white or `#9CA3AF`, tracking `0.5–0.6px` |
| Primary KPI number | total assets | `28px / 700` | white, `line-height: 1.1`, tracking `-1px` |
| Secondary KPI number | P&L / leverage | `18–20px / 600–700` | semantic state color |
| Table header | asset columns | `11px / 600` | `#9CA3AF`, uppercase, tracking `0.4px` |
| Table body | positions | `13px`, key number `600` | `#374151`; amounts use tabular figures |
| Tag / badge | platform, asset class | `11px / 500` | semantic foreground on pale surface |
| Empty state | `EmptyState.tsx` | title `14px / 500`; description `13px` | `#6B7280` / `#9CA3AF`, description `1.6` |

The global font stack is `PingFang SC`, `Noto Sans SC`, system sans-serif in `frontend/src/index.css`; `.tabular-nums` is the shared numeric utility.

### 3.3 Spacing, radius, shadow, border

| Token-like pattern | Actual value / source | Recommended consumption use |
| --- | --- | --- |
| Layout rhythm | 4 / 6 / 8 / 10 / 12 / 14 / 16 / 20 / 24 | Match the existing near-4px rhythm; do not invent a parallel scale. |
| Page padding | `28px 64px` in `AppLayout.tsx` | Inherited automatically. |
| Section gap | `16px` | REUSE between top-level cards/grids. |
| Tight control gap | `6px` or `8px` | tabs, legend items, inline actions. |
| Card padding | common `16–20px`; primary card `20px 24px` | use `16–20px` for analysis/detail cards; reserve larger padding for a hero KPI. |
| Radius | ordinary card `12px`; small control/table scroller `6px`; button `8px`; PageHeader icon `10px` | REUSE by role. |
| Border | `1px solid #E5E7EB`; row divider `#F3F4F6` | REUSE. |
| Light shadow | `--shadow-sm`: two low-alpha ocean shadows | REUSE for white cards and secondary buttons. |
| Hero shadow | `--shadow-dark`: `0 6px 20px rgba(15,30,53,.28)` | keep exclusive to an intentionally primary dark surface. |

## 4. Component Patterns

### 4.1 Page Header — REUSE

`frontend/src/components/shared/PageHeader.tsx` is the canonical reusable implementation: 38×38px, 10px radius ocean gradient icon container; 20px/700 title; 12px muted subtitle; 12px internal gap; 20px bottom margin. `Dashboard.tsx` currently duplicates the same geometry locally. Consumption should use the shared component rather than copying Dashboard's local function.

### 4.2 KPI card system — ADAPT

- **Primary card:** one deep `#1F2937 → #111827` gradient surface, `12px` radius, `20px 24px` padding, 28px figure, muted white label and two supporting figures. Its size comes from `2fr`, not just a larger font.
- **Secondary cards:** white / `#E5E7EB` / `12px` / `16px 18px` / `--shadow-sm`; they show one metric and one small state line.
- **Rule:** promote one decision-anchor metric; attach context and state to neighboring restrained cards. Avoid four equally loud summary cards.

For Consumption, the main number can use this hierarchy, but a dark card must not imply that spending is a positive investment result. It is a visual anchor, not a gain/loss signal.

### 4.3 Card and section header — ADAPT

No generic `Card` component is used by Dashboard. The recurring inline container is white + `#E5E7EB` + 12px + light shadow, with title row `13px/600/#374151`, optional emoji, and right-aligned `11px` muted metadata. Treat this as a documented common token set, not evidence that a refactor is authorized.

### 4.4 Tabs, pills, badges — REUSE / ADAPT

| Pattern | Active | Inactive | Radius / padding | Usage |
| --- | --- | --- | --- | --- |
| Detail category tab | `#3B82F6` surface + white text | `#F3F4F6` + `#6B7280` | `6px`, `4px 12px` | mutually exclusive table scope |
| Import tab | `#EFF6FF` + `#1E40AF` + `#BFDBFE` border | transparent + `#6B7280` + `#E5E7EB` border | `6px`, `6px 14px` | low-emphasis workflow mode |
| Data tag | pale blue (`#DBEAFE`) or neutral gray | n/a | `4px`, `1px 7px` | compact categorical values in table cells |
| State badge | pale semantic background + colored text | n/a | `5–8px`, `2–6px` or `4px 10px` | status, deviation, coverage—not every descriptor |

Use **tabs** for one exclusive data partition; use a **badge** for a compact classification/status; use a **pill** for an evaluative state. Do not turn each transaction attribute into a colorful chip.

### 4.5 Empty, loading, collapsed — REUSE

- `EmptyState.tsx` centers a 40px low-opacity icon, then title and helper within `48px 24px`.
- Dashboard loading is centered at `height: 300px` with an 18px spinner and 13px muted label.
- Import/export sections begin collapsed: one complete white-row affordance, optional tiny export action while closed, and a connected body only after expansion. The same pattern is appropriate only for optional workflow tooling—not for month detail data.

## 5. Table Specification

The asset table in `frontend/src/pages/Dashboard.tsx` is the direct reference for a future Consumption Monthly Detail Table.

| Concern | Current investment rule | Consumption v1.1 adaptation |
| --- | --- | --- |
| Container | parent card; table scroller `maxHeight: 494px`, `overflowY: auto`, `6px` radius | Keep the table inside one section card; provide a bounded vertical scroller when records are numerous. |
| Header | white, sticky top, `8px 10px`, 11px/600 muted uppercase, bottom border | Same geometry; Chinese column headers may omit uppercase transformation if it adds no value. |
| Body row | `9px 10px`, `#F3F4F6` separator, no zebra striping, hover `#F9FAFB` | Reuse. It supports dense scanning without visual noise. |
| Columns | explicit `colgroup`, fixed table layout, text left; numeric columns right after the first categorical columns | Date/name/category/account left; amount right; status remains compact. |
| Numbers | `tabular-nums`; major amounts 600 weight; percentages muted | Amount must be right-aligned and tabular; avoid red/green merely for spend size. |
| Text overflow | name cell uses `overflow: hidden`, ellipsis, `title` | Merchant/display text must truncate and must not reveal raw source text via a browser title. |
| Tags | compact 11px tags in cells | Use primary/secondary category or classification-state tags sparingly. |
| Empty state | uses shared `EmptyState` | REUSE. |

There is no zebra stripe and no horizontal-scroll implementation in Dashboard. Fixed widths and `tableLayout: fixed` are the current desktop-first behavior; a future consumption detail design must validate a narrow viewport before claiming mobile parity.

## 6. Chart Specification

| Chart | Actual implementation | Transferable rule | Do not infer |
| --- | --- | --- | --- |
| Asset allocation range | `AssetAllocationCard.tsx`: 44px rows, 7px rail, translucent blue target range, midpoint line, color dot, compact status badge | Explain a target/range relationship through a compact row chart and a three-item micro legend. | Consumption categories have no target range in current product. |
| Platform distribution | `PieChart`: 52/82px donut, 2px padding angle, 8-color palette; hover enlarges segment by 8px; 11px vertical legend | Put chart inside the card, keep legend quiet, use tooltip for amount + share, reserve colors for distinct groups. | A donut is not automatically useful for spending. |
| Tooltip | white, `#E5E7EB`, 8px radius, 8×12px padding, 12px, small shadow | Reuse tooltip surface and type treatment. | Do not copy investment P&L labels or currency assumptions. |

The current Consumption stacked bar already uses a card and a compact legend, which aligns with the product. Its v1.1 work should align card padding/title hierarchy and semantic palette discipline, not replace the bar with the investment donut.

## 7. Responsive Rules Observed

The current Dashboard has **no Dashboard-level media query or Tailwind responsive variant**. It is desktop-first:

- KPI grid remains `2fr 1fr 1fr`; analysis grid remains `3fr 2fr`.
- App content retains `28px 64px` padding.
- Tables use fixed columns and only vertical scrolling; their headers are sticky.
- The import tab row does enable `flexWrap: wrap`; this is the only local narrow-space accommodation observed.

Therefore Consumption should reuse existing responsive behavior only where it already exists. Any new mobile stacking/table strategy requires a dedicated future design decision, not an invented “existing standard.”

## 8. Reuse Matrix

| UI capability | Current component / code | Consumption decision |
| --- | --- | --- |
| Page header | `frontend/src/components/shared/PageHeader.tsx` | REUSE |
| Main / secondary KPI geometry | `frontend/src/pages/Dashboard.tsx` KPI grid | ADAPT token pattern; no current shared KPI component |
| White section card | repeated inline dashboard style | ADAPT shared token values; do not refactor in this audit |
| Allocation structure rows | `frontend/src/components/allocation/AssetAllocationCard.tsx` | ADAPT only if a consumption-specific comparator exists |
| Tabs | Dashboard local `posTab` styles | REUSE visual pattern; scope to exclusive views |
| Cell tags | Dashboard local `tag()` | ADAPT visual pattern; no exported component |
| Empty state | `frontend/src/components/shared/EmptyState.tsx` | REUSE |
| Tooltip | `frontend/src/components/shared/DataTip.tsx`; chart-local Recharts tooltip | REUSE DataTip for explanatory hover, chart tooltip surface for charts |
| Formatting | `frontend/src/lib/fmt.ts` | REUSE numeric conventions; use existing consumption decimal behavior where required |
| Chart palette | Dashboard `CHART_PALETTE` and allocation colors | REUSE semantic role discipline, not every palette assignment |

## 9. Consumption UI Mapping

| Consumption current section | Investment reference | Recommended v1.1 pattern |
| --- | --- | --- |
| 本月总消费 | total-assets primary card | One primary KPI surface; month and analysis date remain visible but separate from coverage. |
| 分类覆盖率 | secondary KPI card | White supporting metric with muted label and semantic state line. |
| 数据覆盖 | leverage/state secondary card | White supporting metric or compact state card; coverage badge and explanation remain distinct from as-of. |
| 12-month trend | analysis card / chart container | Retain stacked bar; align card shell, header, legend density and tooltip surface. |
| 月度消费结构 | asset allocation card | One dominant analysis card with concise category rows; colors remain category identifiers, not gain/loss semantics. |
| 月度消费明细 | asset detail table | Full-width card → selected-month scope → sticky table header → right-aligned amount → status badge. |
| Review status | liability auxiliary / empty pattern | Keep as auxiliary governance information, visually subordinate to selected-month spend and detail table. |

## 10. Monthly Detail Recommendation

This is a UI/contract recommendation only. Current `GET /api/consumption/analytics` returns aggregates and monthly secondary breakdowns, not transaction rows; no detail endpoint is proposed or implemented in this audit.

Recommended selected-month table, defaulted to **amount descending**:

| Column | Display rule | Safety / contract boundary |
| --- | --- | --- |
| 日期 | `analytics_effective_date`, left aligned | only active consumption event date |
| 消费名称 | a future curated `display_description`, ellipsized | never expose full `RawTransaction.raw_description`; do not put raw text in `title` tooltip |
| 一级分类 | neutral/compact tag | active classified interpretation only |
| 二级分类 | compact text/tag, or `待分类` | retain classification state rather than fabricating category |
| 账户 | `Account.display_name` or controlled masked label | never return full account number; `masked_account_identifier` only if policy permits |
| 金额 | resolved base net amount, tabular and right aligned | CNY amount; unresolved FX must be explicit and must not silently become zero |
| 分类状态 | state badge | e.g. classified / needs review; non-consumption events excluded |

The future read model must use only active `EconomicEvent`, active projection revision, and active `ConsumptionInterpretation`, following the existing analytics adapter’s read-only boundary. Refund, transfer, repayment, eligibility-review and other non-consumption events must remain excluded under current semantics.

## 11. DO NOT COPY

1. Do not copy investment P&L red/green semantics: Dashboard intentionally renders positive P&L red and negative P&L green for the local market convention; spending has no equivalent “good/bad” direction.
2. Do not copy the platform donut unless a specific spending decision requires a part-to-whole view; the existing 12-month trend is more useful for time-series spending.
3. Do not copy allocation target ranges, over/under-allocation language, or “目标中值” rails: Consumption currently has no approved budget/target model.
4. Do not copy asset table columns, foreign-currency columns, holdings quantity, or P&L fields into spending detail.
5. Do not make every spending metric a KPI card; use one main metric, two restrained supporting states, then denser analysis/detail surfaces.
6. Do not expose raw statement description, full account identifier, import provenance, or normalizer evidence merely to imitate the asset table’s density.
7. Do not copy Dashboard’s local PageHeader function; use the shared component.
8. Do not treat the current desktop-first fixed-grid implementation as an established mobile responsive system.

## 12. Proposed Consumption UI v1.1 Skeleton

```text
Page Header

Selected-month KPI Summary (2fr / 1fr / 1fr)
├─ 本月总消费 + analysis date
├─ 分类覆盖率
└─ 数据覆盖 status

Trend Card
└─ 12-month stacked bar + selected-month control

Selected Month Detail Grid (3fr / 2fr when desktop evidence supports it)
├─ 月度消费结构 + selected-month secondary categories
└─ 覆盖 / 金额完整性 / review summary

Monthly Transaction Detail Card
└─ selected-month, amount-descending, sticky-header table

Optional Import / governance utilities
└─ collapsed only when they are workflow actions rather than primary analysis
```

This hierarchy makes the selected month the page’s single detail context, while retaining the long-range trend as context—not as a competing second time scope.

## 13. Audit Evidence and Isolation

- Route: `frontend/src/App.tsx` maps both index and `/dashboard` to `Dashboard`; sidebar label is defined in `frontend/src/components/layout/Sidebar.tsx`.
- Page: `frontend/src/pages/Dashboard.tsx`.
- Shared/theme evidence: `frontend/src/index.css`, `frontend/src/components/shared/PageHeader.tsx`, `frontend/src/components/shared/EmptyState.tsx`, `frontend/src/components/shared/DataTip.tsx`, and `frontend/src/lib/fmt.ts`.
- Chart/configuration evidence: `frontend/src/components/allocation/AssetAllocationCard.tsx`.
- Current page was read locally at `http://127.0.0.1:5173/#/dashboard`; visual structure matched code. No screenshot is stored or committed.
- Investment, Pattern, Consumption source, sidebar, shared UI, CSS, backend, route, and test code were not modified.
