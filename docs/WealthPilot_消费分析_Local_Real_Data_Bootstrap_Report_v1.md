# WealthPilot 消费分析 Local Real-data Bootstrap Report v1

日期：2026-08-30

范围：本地、离线的三类消费账单 Bootstrap Runner。它只编排既有 Adapter、Raw persistence、Economic Event normalizer、Classification resolver 与 Analytics service；不新增业务规则、网络调用或产品导入 UI。

## Baseline

| Item | Value |
| --- | --- |
| Branch | `codex/consumption-ui-v1` |
| Start HEAD | `8c8425f26f808f11a8b176acc5580ceda84f5fd6` |
| Final HEAD | 包含本报告与真实 Bootstrap 验证的本地 Bootstrap commit |
| `main` / `origin/main` | `c0027b5ee091a1169910581a2e2d84c1f65386b4`（本轮未合并、未 push） |

## Runner

入口：`python -m backend.scripts.import_consumption_statements`。

支持的显式来源：

- `--cmb-credit <PDF-or-directory-or-source-specific-ZIP>`
- `--ccb-credit <EML-or-directory-or-source-specific-ZIP>`
- `--cmb-debit <PDF-or-directory-or-source-specific-ZIP>`
- `--archive <ZIP-or-directory>`：单个显式归档内（目录可递归）的 EML 交给 CCB Adapter；PDF 只有在现有 CMB Credit / Debit Adapter 中恰好一个可验证解析时才接受。

例如：

```bash
python -m backend.scripts.import_consumption_statements --archive <path-to-local-statement-archive.zip>
```

也支持 `--dry-run`（只解析、验证并打印安全计数）和 `--no-backup`。默认非 dry-run 会在既有目标 DB 的 `data/backups/` 下创建一次一致性 SQLite backup；该目录已被 Git 忽略。

目标 DB 完全沿用 `WEALTHPILOT_DB_PATH`，未设置时为 `data/wealthpilot.db`。控制台只输出目标 DB 的安全路径、输入 basename、状态和聚合计数，不输出金额、原始行、对手方、姓名或完整账户标识。

## Existing Services Reused

| Layer | Reused implementation |
| --- | --- |
| Adapter | `parse_cmb_credit_card_pdf` / `parse_ccb_credit_card_eml` / `parse_cmb_debit_card_pdf` |
| Raw import | `ConsumptionImportService.persist` |
| Event | `EconomicEventNormalizer.normalize` |
| Classification | `ClassificationResolver.replay` |
| Analytics | `ConsumptionAnalyticsService.summary` |

所有来源先完成解析验证，再在一个 SQLite transaction（调用方已有 transaction 时使用 savepoint）中持久化。文件解析失败不会产生 DB 写入；写入或下游处理失败会回滚本次原子单元。

## Account Mapping 与 Idempotency

| Source | Account | PaymentInstrument |
| --- | --- | --- |
| CMB Credit | `CMB / CREDIT_CARD / source masked identity`，存在则复用 | 有来源 masked card 时 upsert `PHYSICAL_CARD` |
| CCB Credit | `CCB / CREDIT_CARD / source masked identity`，存在则复用 | 有来源 masked card 时 upsert `PHYSICAL_CARD` |
| CMB Debit | `CMB / DEBIT_CARD / source masked identity`，存在则复用 | `NULL` |

文件级幂等继续由 `ImportBatch.source_file_hash` 的 SHA-256 唯一键实现。重复相同 fixture 三来源运行的验证结果为：batch、RawTransaction、active EconomicEvent 与 active ConsumptionInterpretation 数量均保持不变；第二次只返回复用 batch。

## Validation

| Gate | Result |
| --- | --- |
| Three-source synthetic full pipeline | PASS |
| Idempotency | PASS |
| Existing Portfolio table isolation | PASS |
| SQLite backup | PASS |
| Console privacy | PASS |
| Consumption tests | `161 passed` |
| Full pytest | `880 passed, 7 skipped` |
| Frontend lint / build | PASS |
| Consumption + navigation browser tests | `5 passed` |
| Pattern Evidence frontend regression | `6 passed` |
| Offline M5 | `18/18 passed`, public network attempts `0` |
| Compile/import check | PASS |
| `git diff --check` | PASS |

Synthetic pipeline analytics 为 non-empty，且每个月都验证：`DAILY + TRAVEL + HOUSING + unclassified = known eligible total`。

## Real Local Bootstrap 与 UI Validation

用户随后明确提供了本地 `docs/statement/` 目录。该目录顶层没有来源文件，但在用户明确授权的目录内递归检查到 25 个支持文件；Runner 因此扩展为只递归处理显式传入的 source directory，不会扫描其他本地路径。

真实 Bootstrap 使用：

```bash
python -m backend.scripts.import_consumption_statements --archive docs/statement
```

结果（无真实金额、交易明细或账户标识）：

| Item | Result |
| --- | --- |
| Target DB | `data/wealthpilot.db` |
| Backup | PASS，首次真实写入前创建一份 ignored SQLite backup |
| Parsed statements | 25 |
| Raw rows | 3,430 |
| Active Economic Events | 3,430 |
| Eligibility | eligible 2,318; ineligible 556; needs review 556 |
| Classification | classified 408; needs review 1,910; not applicable 1,112 |
| Analytics non-empty | PASS |
| 12-month invariant | PASS |

以 `--no-backup` 安全复跑相同来源后，25 个 ImportBatch 与 3,430 条 RawTransaction 全部复用，新增 batch / RawTransaction 均为 0；active EconomicEvent 与 active ConsumptionInterpretation 未增加。

本地 `/consumption` 已刷新并实际检查：

- Empty State：**NO**
- 真实消费分析 Dashboard：**YES**
- 12 个月趋势、日常 / 旅行 / 住房 / 待分类四段、覆盖状态和两个 Review 状态：**YES**
- 月份切换：**PASS**
- Console fatal error：**NO**

## Privacy 与 Isolation

- real source committed: **NO**
- real source copied to repo: **NO**
- raw logged: **NO**
- LLM / MCP / network: **NO**
- Pattern modified: **NO**
- IA / Investment modified: **NO**
- Analytics / Classification semantics modified: **NO**

## Open Items / Next Readiness

无真实数据 Bootstrap 或 UI Review 阻塞项。

**Next Readiness：`READY_FOR_USER_UI_REVIEW`**。
