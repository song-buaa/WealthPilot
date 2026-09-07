# Personal Finance Integration v1 验收报告

验收日期：2026-09-07。范围：完整消费分析与财富总览分支的 Git 集成；不是发布。

## Git 审计与来源

已执行 status、branch -vv、全分支 graph log、remote -v、rev-parse、merge-base、分支增量 log 与 diff --stat。
开始时 checkout 为 `codex/wealth-overview-v01`，tracked 文件干净，仅有未跟踪的 `data/backups/`。

| 引用 | SHA |
| --- | --- |
| main / origin/main / 远端 main（ls-remote 核对） | c0027b5ee091a1169910581a2e2d84c1f65386b4 |
| codex/consumption-ui-v1 | a6066d071d843a8743a22d6f86799201867ed82e |
| codex/wealth-overview-v01 | f03c328e43e2ad9da5cc8ced5dffed6f2cf412d2 |
| 第一次合并：完整消费分支 | 20bf93a1744dd821eae62f30f49adcccea91e219 |
| 第二次合并：完整财富分支；通过测试的代码候选 | 49f9523641520250bc75d55b0a9e241e34ab5965 |

集成分支：`codex/personal-finance-integration-v1`，从上述 main 创建。
本报告的后续文档提交不改变测试过的业务代码。两条来源分支和 main 均未移动。
两个 merge-base 都是 `c0027b5`；消费分支独有 43 个提交（50 文件），财富分支独有 17 个提交（19 文件）。

## 空页面根因

main 虽包含消费后端基础，但 `frontend/src/pages/Consumption.tsx` 仍是“消费分析正在建设中”的稳定占位 shell。
财富分支从这个 main 分叉，未包含消费分支后续的完整 UI、导入增强、分类、退款保护与测试。
路由和导航并没有被覆盖或删除；不是新旧 api.ts 覆盖造成。当前本地数据库也没有丢失或切换。
三条分支的数据库配置均为 `WEALTHPILOT_DB_PATH` 优先，否则使用项目 `data/wealthpilot.db`。
本次真实服务实际使用同一项目目录下的该数据库；不能据此推断过去其他机器的启动环境。

## 合并与冲突处理

1. 停止前后端，避免分支切换触发热重载初始化。
2. 从 main 创建集成分支，`merge --no-ff` 完整消费分支。
3. 先运行消费 274 项后端测试和 10 项浏览器测试，并打开真实消费页面、读取真实 API，全部通过。
4. 再停止服务，`merge --no-ff --no-commit` 完整财富分支，逐项检查自动合并后提交。

没有 Git 文本冲突；存在一个自动合并产生的语义冲突：`PageHeader.tsx` 重复导入 `ReactNode`，移除重复 import，保留双方需要的 ReactNode 类型。
共同修改文件仅三个：

- `app/database.py`：同时保留消费 Raw 生命周期迁移与财富外币字段迁移及其初始化调用；没有 DROP、数据复制或回放脚本操作。
- `frontend/src/lib/api.ts`：同时保留完整 consumptionApi 和 wealthApi，未删减 Portfolio API。
- `frontend/src/components/shared/PageHeader.tsx`：上述重复 import 修正。

Router、Sidebar、backend router 注册、各自 models/services 和共享财富表格样式均已核对。
消费业务文件与消费 HEAD 一致；财富页面、wealth_service 和 Wealth API 与财富 HEAD 一致。
没有 rebase、reset、amend、squash、cherry-pick、force push、分支删除、main 修改、push 或 Tag 操作。

## 数据完整性

合入前与最终启动、浏览器导航后，用 SQLite `mode=ro` 读取并按 rowid 排序，对完整行序列计算 SHA-256。
全部 consumption_*、wealth_*、portfolios、positions 表的数量与内容哈希均一致，不只是检查非空。

| 数据 | 集成前 | 集成后 |
| --- | ---: | ---: |
| 消费账户 | 8 | 8 |
| 消费导入批次 | 25 | 25 |
| 消费 Raw | 3444 | 3444 |
| 消费 Economic Event（含历史行） | 6372 | 6372 |
| 消费 projection revisions | 4179 | 4179 |
| 消费 interpretations | 14324 | 14324 |
| 消费备注 | 63 | 63 |
| wealth_items | 19 | 19 |
| wealth_item_snapshots | 22 | 22 |
| wealth_snapshots | 25 | 25 |
| portfolios | 1 | 1 |
| positions | 200 | 200 |
| positions 中 broker/account 组合（含空值组合） | 3 | 3 |

Portfolio UI 为 201 项，是 200 条持仓加现有虚拟现金展示，不代表新增数据库持仓。
未读取、整理、删除或提交 `data/backups/` 的内容。消费分支原有 `.gitignore` 新增了该路径，因此集成后它由未跟踪显示改为 ignored，仍未纳入版本控制。

## 验收与测试

| Gate | 结果 |
| --- | --- |
| Consumption（classification / normalization / refund / ingestion / API） | 274 passed |
| Wealth（totals / portfolio / liability / pension / snapshots） | 12 passed |
| Portfolio corrections 与 broker_sync 回归 | 54 passed，2 个真实外部 integration 用例按原规则 deselected |
| 上述定向测试合计 | 340 passed，2 deselected |
| 项目默认全量 pytest | 892 passed，7 skipped，0 failed |
| Offline M5 | 18/18，公网连接尝试 0 |
| frontend lint | 通过 |
| frontend build | 通过；已有大 chunk 非阻断提示 |
| 全部现有 Playwright | 20 passed |

默认 pytest 的 testpaths 不包含消费与 broker_sync 目录，故二者另行完整运行；以上测试计数存在重叠，不能简单相加。
Python 使用 wealthpilot 环境，测试使用隔离临时 SQLite、禁用 dotenv、Public Demo/Mock broker 配置。没有对真实券商执行同步或下单。
a6066d0 的 source-explicit refund 候选列表/确认边界保护、退款重放纠正旧消费事件用例均在消费定向测试中通过。

真实浏览器完成 Investment → Wealth → Consumption → Wealth，无需手工重载；三页数据非空，API/UI 金额一致，核心资产分类之和、资产减负债等式成立。
消费月度明细、滚动窗口、分类结构及原有已确认记录恢复；财富负数净资产、养老保障权益、外币/负债明细、CSV 入口与投资页配置/平台分布/持仓保留。
同步后金额变化采用现有临时 SQLite + 真实 service 的浏览器 fixture 验证，不污染真实账户：同步提交后 Portfolio/Wealth 都更新，SPA、focus、visibilitychange、portfolio-updated 均触发刷新；没有新增财富页投资同步入口。

模块结论：消费分析 PASS；财富总览 PASS；投资账户总览 PASS。

## 保留限制与下一步

- 个人养老金仍按已登记金额去重，不是账户/holding 映射；底层净值变化可能产生小额偏差，沿用用户已接受的 Known Limitation。
- 不重建、不改写历史财富快照；旧口径测试阶段快照的既有边界保持原状。
- 消费测试在合入财富前已出现 React borderBottom/borderBottomColor 样式提示；不是此次财富合并新增，未扩大范围做 UI 修改。
- 当前 main 保护规则按治理文档要求 linear history，而候选保留了用户明确要求的 merge 历史。进入远端 main 前，需仓库所有者确认允许普通 merge 历史的治理/规则调整；不应擅自 bypass 或改写提交规避。

建议后续流程（本轮未执行）：用户确认后推送集成分支，等待三个 CI Gate 通过；再次 fetch 并核对 main 未推进、候选仍包含最新 main。上述线性历史规则经所有者处理后，才在 main 执行 `git merge --ff-only codex/personal-finance-integration-v1` 并推送 main。如果 main 已推进，停止重新评估，不能 reset/rebase。
本轮候选留在集成分支，前后端已恢复运行；等待用户确认发布步骤。
