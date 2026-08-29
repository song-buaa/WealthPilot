# WealthPilot 财富规划 IA / Navigation Shell Implementation Report v1

日期：2026-08-29

## 1. Baseline

| 项目 | 结果 |
| --- | --- |
| Start HEAD | `abcbeff9ade63f7af27f9126a572c4ca8126fe0f` |
| main at start | `abcbeff9ade63f7af27f9126a572c4ca8126fe0f` |
| origin/main at start | `abcbeff9ade63f7af27f9126a572c4ca8126fe0f` |
| Branch | `codex/wealth-ia-shell` |

## 2. Existing Investment Snapshot

以下是实施前当前 `main` 的可见 Sidebar 投资导航；本轮全部保留。

| Order | Existing Label | Existing Route | Action |
| --- | --- | --- | --- |
| 1 | 用户画像 | `/profile` | KEEP |
| 2 | 投资账户总览 | `/dashboard` | KEEP |
| 3 | 投资纪律 | `/discipline` | KEEP |
| 4 | 投研观点 | `/research` | KEEP |
| 5 | 投资决策 | `/decision` | KEEP |
| 6 | 投资行动 | `/action` | KEEP |

未启用的历史 Placeholder 开关不属于实施时可见投资导航，未作为本轮投资规划二级结构来源。

## 3. Navigation Before / After

实施前：Sidebar 将六项投资入口平铺显示，未建立财富规划一级 IA。

实施后：Sidebar 形成五个一级业务模块：

```text
首页
财富总览
投资规划
  └─ 当前六项投资入口（原标签、原顺序、原 route）
养老规划
消费分析

系统
  ├─ 数据管理
  └─ 设置
```

「投资规划」是可理解的分组标题，不引入 `/investment/*` route namespace，也不改变任何投资页面。当前已有 Dashboard 同时继续作为首页内容：`/` 直接渲染 Dashboard，既有 `/dashboard` route 保持不变并留在投资规划中，因此两种导航场景的 active state 不冲突。

实施前没有独立的数据管理或设置页面/route；本轮仅将它们作为系统分区入口，复用已有 `/placeholder/:name` 机制，不新增系统业务能力。

## 4. New Routes

| Route | 页面 | 本轮行为 |
| --- | --- | --- |
| `/wealth` | 财富总览 | 稳定页面壳，不读取跨域财富数据 |
| `/retirement` | 养老规划 | 稳定页面壳，无养老计算或建议 |
| `/consumption` | 消费分析 | 稳定页面壳，不读取或展示 RawTransaction |

## 5. Investment Preservation

| 项目 | 结果 |
| --- | --- |
| renamed | NO |
| reordered | NO |
| route changed | NO |
| business logic changed | NO |

Pattern Evidence 继续留在现有投资决策链路；Execution Plan 与投资行动仍在原有页面与 route 中。

## 6. Page Shells

三个新页面都只复用现有 `PageHeader` 和 `EmptyState`，未放置假数字、mock 图表或看似真实的用户信息。

- **财富总览**：说明未来逐步接入投资、消费与养老模块数据；不建立统一资产负债表或新 API。
- **养老规划**：说明未来单独建设退休目标、养老资产与测算；不做 readiness、养老金或情景计算。
- **消费分析**：说明后续在此接入 Event、分类与分析；不展示 RawTransaction，不做消费金额、导入或 AI Insight。

## 7. Isolation

| Boundary | Result |
| --- | --- |
| Pattern modified | NO |
| Consumption backend modified | NO |
| Backend schema / migration | NO |
| New UI dependency | NO |

## 8. Tests

| Gate | Result |
| --- | --- |
| Navigation shell Playwright contract | 2 passed |
| Pattern Evidence Playwright regression | 6 passed |
| Frontend lint | PASS |
| Frontend build | PASS (existing non-blocking large-chunk warning only) |
| Backend compile / import | PASS |
| Full pytest | 880 passed, 7 skipped |
| `git diff --check` | PASS |

## 9. Open Items

1. 消费分析的 Economic Event 设计、分类、聚合与正式 UI 仍需按消费专项 PRD 分阶段实施；本页 route 已稳定，不构成业务入口承诺。
2. 财富总览的跨领域资产/负债视图与养老规划数据模型仍需独立设计；本轮没有建立 Unified Account。
