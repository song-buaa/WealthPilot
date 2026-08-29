# WealthPilot 消费分析 Adapter Spike Report v1

日期：2026-08-29
范围：三类本地真实账单的确定性 Source Adapter 验证，以及可提交的脱敏 Golden fixture。
不在范围：ORM/数据库、migration、Economic Event、分类、分析、API、前端、AI、MCP。

## 15.1 Baseline

| Item | Value |
| --- | --- |
| Start HEAD | `f3c47080c4a6a875f5e74db4c4d6873ac04b3843` |
| main / origin/main at start | `f3c47080c4a6a875f5e74db4c4d6873ac04b3843` |
| Branch | `codex/consumption-adapter-spike` |
| Stable tag | `wealthpilot-pattern-evidence-v1.0` |
| Real-source handling | local-only, read in memory; no source file copied into the repository |

## 15.2 Adapter Architecture

新增独立、无 ORM 的 `backend/services/consumption/` bounded context：

```text
Source bytes
  → source adapter
  → ParsedStatement
       ├─ StatementMetadata
       └─ tuple[NormalizedRawTransaction]
  → canonical JSON fixture / fingerprint experiment
```

关键文件：

- `contracts.py`：冻结 `ParsedStatement`、`StatementMetadata`、`NormalizedRawTransaction`、`FieldAvailability`，并定义稳定 JSON、SHA-256 file hash 与 fixture-level raw-row fingerprint。
- `adapters/cmb_credit_card_pdf.py`：仅从 PDF 文本层提取招行信用卡原始行。
- `adapters/ccb_credit_card_eml.py`：`EML → MIME → HTML table`；使用项目既有 BeautifulSoup 依赖，不使用浏览器、网络或 LLM。
- `adapters/cmb_debit_card_pdf.py`：仅从 PDF 文本层提取招行借记卡流水。
- `tests/fixtures/consumption/`：三套 synthetic/de-identified fixture、expected statement、expected transactions 与 field availability Golden files。

所有金额均使用 `Decimal`，JSON 固定为两位小数文本；所有日期固定为 ISO-8601；row order 与 JSON key order 均确定。Adapter 不推断消费分类、退款、还款、转账、资金归集或 Economic Event。

## 15.3 Field Availability Matrix

以下矩阵是实际 Adapter 可安全输出的字段能力，不将缺失或不可可靠拆分的内容伪造为事实。

| Field | CMB Credit PDF | CCB Credit EML | CMB Debit PDF | Target Contract |
| --- | --- | --- | --- | --- |
| institution / statement type / source format / parser version | AVAILABLE | AVAILABLE | AVAILABLE | AVAILABLE |
| statement period | SOURCE_UNAVAILABLE | AVAILABLE | AVAILABLE | explicit availability state |
| account masked | AVAILABLE | AVAILABLE | AVAILABLE | masked only |
| instrument/card tail | AVAILABLE | AVAILABLE | SOURCE_UNAVAILABLE | separate optional field |
| transaction date | AVAILABLE | AVAILABLE | AVAILABLE | `date` + availability |
| posting date | AVAILABLE | AVAILABLE | SOURCE_UNAVAILABLE | never copied from transaction date |
| amount | AVAILABLE | AVAILABLE | AVAILABLE | `Decimal` |
| currency | AVAILABLE | AVAILABLE | AVAILABLE | source currency only |
| raw description | AVAILABLE | AVAILABLE | AVAILABLE | source text only |
| balance | SOURCE_UNAVAILABLE | SOURCE_UNAVAILABLE | AVAILABLE | optional Decimal |
| counterparty | SOURCE_UNAVAILABLE | SOURCE_UNAVAILABLE | SOURCE_UNAVAILABLE | optional; no unsafe text split |
| settlement amount / currency | AVAILABLE | AVAILABLE | SOURCE_UNAVAILABLE | independent optional fields |
| MCC | SOURCE_UNAVAILABLE | SOURCE_UNAVAILABLE | SOURCE_UNAVAILABLE | optional only |
| source row identity / provenance | AVAILABLE | AVAILABLE | AVAILABLE | stable adapter/row contract |

### CMB credit-card date note

The real CMB credit PDFs expose an issue date/payment date but no explicit, labelled statement-cycle range in the PDF text layer. The adapter consequently marks `statement_period` as `SOURCE_UNAVAILABLE`; it does not mistake the issue/payment dates for a statement period. Transaction `MM/DD` values are deterministically assigned a year using the dated statement anchor, with `parser_provenance.date_year_resolution = statement_date_year_anchor`. Explicitly labelled periods, when present, take precedence.

## 15.4 Parsing Results

Validation read the user-provided local archive in memory, skipped ZIP macOS metadata entries and did not extract or retain source files in the repository.

| Source | Validated statements | Parsed raw rows | Result | Known parsing characteristic |
| --- | ---: | ---: | --- | --- |
| CMB Credit Card PDF | 12 | 1,150 | PASS | PDF row dates are `MM/DD`; explicit statement period absent from text layer. Original/settlement amounts and card identity are available. |
| CCB Credit Card EML | 12 | 1,170 | PASS | Nested HTML tables have inconsistent semantic headers; structural fallback requires two ISO dates, 3-letter currency cell(s), decimal amount cell(s), and description. |
| CMB Debit Card PDF | 1 | 1,110 | PASS | One dated transaction column, amount, balance and source tail are available. No independent posting date or reliably separable counterparty is emitted. |

No parser exception occurred in the 25 valid source statements. Representative exact field checks live only in the committed de-identified fixtures, not in real-source output.

## 15.5 Determinism

- Each source was parsed twice in the archive validation; all 25 canonical `ParsedStatement` JSON outputs were identical.
- `backend/services/consumption/tests/test_statement_adapters.py` verifies deterministic output for all three fixture formats, exact Golden output, field availability, CMB cross-year date resolution, file hash and row-fingerprint stability.
- The targeted test suite passes: **13 passed**.

Canonicalisation rules:

1. Input order is retained as source row order.
2. Decimal serialization is fixed to two decimal places.
3. Dates serialize to ISO-8601.
4. `parser_provenance` and availability maps sort keys before serialization.
5. Canonical JSON uses sorted keys and compact separators.

## 15.6 Fingerprint Findings

### Source file hash

`SHA-256(source_bytes)` is stable and implemented by `source_file_hash()`. It is suitable to identify a byte-identical re-upload of a source file.

### Candidate raw-row fingerprint

The Spike candidate hashes canonical JSON of:

```text
institution
+ masked account / instrument identity
+ transaction date
+ posting date
+ exact Decimal amount
+ currency
+ normalized raw description
+ source row identity
```

Fixture tests prove stability and distinguish representative different rows.

### Not frozen

This is deliberately **not** a production unique key. Including source-row identity lowers collision risk within one statement but can prevent matching the same row when an overlapping statement renders it at a different row position. Conversely, omitting it can collide for repeated identical purchases. Formal uniqueness must be chosen only after overlapping-statement and cross-account examples are exercised in the later Raw Transaction/Economic Event design.

## 15.7 Privacy Verification

| Check | Result |
| --- | --- |
| real source committed | NO |
| real source logged | NO |
| LLM called on raw statement | NO |
| MCP called on raw statement | NO |
| database / ORM write | NO |
| network call | NO |
| tracked fixture source | synthetic / de-identified only |

`.gitignore` now blocks `*.eml`, CMB-like statement PDFs and transaction-flow PDFs, while explicitly allowing only `tests/fixtures/consumption/**/input_redacted.eml`. PDF test fixtures are text-layer fixtures (`.txt`), so real binaries cannot be introduced by normal staging. The adapters do not emit raw content to logs.

## 15.8 Open Ambiguities

1. **CMB Credit PDF statement coverage:** an explicit text-layer statement-cycle start/end is unavailable in the observed source. The future ImportBatch/Coverage design must choose a verified source metadata rule, or clearly treat it as unavailable; it must not reuse issue/payment dates as coverage.
2. **CMB Debit counterparty:** the extracted PDF line preserves the raw tail, but a standalone counterparty boundary is not reliable. The future Raw Transaction schema should keep raw description before introducing any deterministic split rule.
3. **CCB HTML layout:** current structural fallback is validated across the supplied 12 EMLs. New layouts need a fixture before changing its table-selection assumptions.
4. **Row-fingerprint uniqueness:** overlap behaviour remains intentionally un-frozen as described above.

## 15.9 Schema Freeze Readiness

**READY_WITH_OPEN_ITEMS**.

The unified contract, Decimal/date representation, raw field availability, parser determinism and all three source adapters are validated. Schema Freeze should wait for a written decision on CMB credit statement coverage and an overlap-based production row deduplication strategy. This is not a reason to expand the Adapter Spike into database or event work.

## 15.10 Recommendation

Next minimum task:

> **消费分析：Raw Transaction ImportBatch Schema Freeze 与跨账单去重样本验证**

That task should first resolve the two open items above, then introduce the first persistence model/migration in the dedicated consumption domain. It should still not build Economic Event classification, analytics, frontend, AI or MCP until Raw Transaction import and coverage semantics are proven.
