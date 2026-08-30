# WealthPilot 消费分析 Local Real-data Bootstrap Report v1

日期：2026-08-30

范围：本地、离线的三类消费账单 Bootstrap Runner。它只编排既有 Adapter、Raw persistence、Economic Event normalizer、Classification resolver 与 Analytics service；不新增业务规则、网络调用或产品导入 UI。

## Baseline

| Item | Value |
| --- | --- |
| Branch | `codex/consumption-ui-v1` |
| Start HEAD | `8c8425f26f808f11a8b176acc5580ceda84f5fd6` |
| Final HEAD | 包含本报告的本地 Bootstrap commit |
| `main` / `origin/main` | `c0027b5ee091a1169910581a2e2d84c1f65386b4`（本轮未合并、未 push） |

## Runner

入口：`python -m backend.scripts.import_consumption_statements`。

支持的显式来源：

- `--cmb-credit <PDF-or-directory-or-source-specific-ZIP>`
- `--ccb-credit <EML-or-directory-or-source-specific-ZIP>`
- `--cmb-debit <PDF-or-directory-or-source-specific-ZIP>`
- `--archive <ZIP-or-directory>`：单个显式归档内的 EML 交给 CCB Adapter；PDF 只有在现有 CMB Credit / Debit Adapter 中恰好一个可验证解析时才接受。

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

此前提供的本地归档路径在本轮执行时不存在。按照任务的安全规则，未猜测路径、未扫描 Downloads 或其他磁盘位置，也未复制或保留真实账单。因此：

- Real local bootstrap：**NOT RUN**；需要用户重新提供 `--archive` 路径，或三类显式 source path。
- Real analytics validation：**NOT RUN**。
- `/consumption` real dashboard validation：**NOT RUN**；不宣称真实数据已渲染。

## Privacy 与 Isolation

- real source committed: **NO**
- real source copied to repo: **NO**
- raw logged: **NO**
- LLM / MCP / network: **NO**
- Pattern modified: **NO**
- IA / Investment modified: **NO**
- Analytics / Classification semantics modified: **NO**

## Open Items / Next Readiness

唯一阻塞项：提供当前可访问的真实账单归档路径或三个来源路径，随后可先运行 `--dry-run`、再执行默认带 backup 的真实 Bootstrap，并刷新 `/consumption` 完成 UI 验收。

**Next Readiness：`READY_WITH_OPEN_ITEMS`**。
