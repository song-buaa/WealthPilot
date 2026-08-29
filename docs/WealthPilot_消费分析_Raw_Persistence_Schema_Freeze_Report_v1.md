# WealthPilot 消费分析 Raw Persistence / Schema Freeze Report v1

日期：2026-08-29
范围：消费域 Raw Layer；不含 Economic Event、分类、分析、UI、AI、Tool 或 MCP。

## 1. Baseline 与 Spike 收口

| 项目 | 结果 |
| --- | --- |
| Spike HEAD | `41fe8de37d15d0916683946e01abe201561375d2` |
| Spike 收口 | 已从 `codex/consumption-adapter-spike` 以 `git merge --ff-only` 合入 `main` |
| `main` / `origin/main` | 均为 `41fe8de37d15d0916683946e01abe201561375d2`，已 push |
| Spike 本地分支 | 已在确认 merged 后删除 |
| Raw Schema start HEAD | `41fe8de37d15d0916683946e01abe201561375d2` |
| Raw Schema branch | `codex/consumption-raw-schema` |

Spike 合并后 smoke：适配器定向测试 13 passed、消费包 compileall、`import backend.main`、`git diff --check` 均通过。Spike 不是发布版本，Tag：**NO**。

## 2. Final Schema

模型均位于 `backend/services/consumption/models.py`，由 `app.database.init_db()` 显式导入注册。它们与 `Portfolio`、`Position`、`Liability`、券商同步和 Pattern 表完全分离。

### Account (`consumption_accounts`)

| 字段 | 类型 | 空值 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID 字符串 | 否 | 主键 |
| `institution` | String(30) | 否 | 例如 CMB、CCB |
| `account_type` | String(30) | 否 | 当前语义为 CREDIT_CARD / DEBIT_CARD |
| `display_name` | String(120) | 是 | 用户展示名，不替代来源身份 |
| `masked_account_identifier` | String(64) | 是 | 仅允许来源可得的脱敏标识 |
| `base_currency` | String(3) | 否 | 默认 CNY；不代表 FX 转换已发生 |
| `status` | String(20) | 否 | 默认 ACTIVE |
| `created_at` / `updated_at` | DateTime | 否 | 审计时间 |

索引：`(institution, account_type)`。一对多关联 PaymentInstrument、ImportBatch、RawTransaction。

### PaymentInstrument (`consumption_payment_instruments`)

| 字段 | 类型 | 空值 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID 字符串 | 否 | 主键 |
| `account_id` | FK → Account | 否 | 所属消费账户 |
| `instrument_type` | String(30) | 否 | PHYSICAL_CARD / VIRTUAL_CARD / OTHER |
| `masked_identifier` | String(64) | 是 | 卡尾或来源可得的脱敏身份 |
| `status` | String(20) | 否 | 默认 ACTIVE |
| `effective_from` / `effective_to` | Date | 是 | 仅在来源/用户可证明时填入 |
| `created_at` / `updated_at` | DateTime | 否 | 审计时间 |

索引：`account_id`、`masked_identifier`。一对多关联 RawTransaction。借记卡来源没有可靠独立支付工具时，RawTransaction 的 `payment_instrument_id` 保持 NULL。

### ImportBatch (`consumption_import_batches`)

| 字段 | 类型 | 空值 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID 字符串 | 否 | 主键 |
| `account_id` | FK → Account | 否 | 导入归属 |
| `source_format` / `institution` / `statement_type` | String | 否 | 来源事实 |
| `source_file_hash` | SHA-256 String(64) | 否 | **UNIQUE**，同字节文件幂等键 |
| `parser_version` | String(120) | 否 | 产生结果的 Adapter 版本 |
| `statement_period_start/end` / `statement_period_availability` | Date / String(30) | 日期可空；availability 非空 | 只有正式来源周期才写入；availability 保留来源事实 |
| `coverage_status` | String(20) | 否 | EXPLICIT / OBSERVED_ONLY / UNKNOWN |
| `observed_transaction_start/end` | Date | 是 | 文件内可观察交易日范围，不声明完整性 |
| `imported_at` / `row_count` / `status` | DateTime / Integer / String | 否 | 导入可追溯性 |

索引：`(account_id, imported_at)`、`coverage_status`。一对多关联 RawTransaction。

### RawTransaction (`consumption_raw_transactions`)

| 字段组 | 字段 | 类型 / 空值 | 说明 |
| --- | --- | --- | --- |
| 关联 | `id`, `import_batch_id`, `account_id`, `payment_instrument_id` | UUID；前三项非空，instrument 可空 | 来源归属与可选支付工具 |
| 行来源 | `source_row_index`, `source_row_identity`, `source_row_fingerprint_candidate` | 非空 | 保留源内行位置、稳定 identity 与含 row identity 的候选指纹 |
| 跨账单候选 | `match_fingerprint`, `dedup_status` | 非空 | 非唯一、只提供候选关系 |
| 日期 | `transaction_date`, `transaction_date_availability`, `posting_date`, `posting_date_availability` | 日期可空，availability 非空 | 不用 transaction date 填充 posting date |
| 金额 | `amount`, `currency`, `settlement_amount`, `settlement_currency`, `balance` | `Numeric(20,8)`；来源依赖字段可空 | 全部金额源事实，禁止 Float source of truth |
| 原文 | `raw_description`, `raw_counterparty`, `mcc`, `parser_provenance`, `source_field_availability` | description/provenance/availability 非空，其余可空 | 不做商户或消费语义清洗，并保存来源字段可得性 |
| 审计 | `created_at` | 非空 | 写入时间 |

约束：`UNIQUE(import_batch_id, source_row_identity)`；索引 `(account_id, transaction_date)`、`match_fingerprint`。外键指向 ImportBatch、Account、PaymentInstrument；删除 ImportBatch 会级联删除其来源行。`parser_provenance` 保存 canonical JSON，不含原始文件内容。

## 3. Coverage Semantics

| 状态 | 规则 |
| --- | --- |
| `EXPLICIT` | 来源明确给出起止账单周期，两个 period 字段均保存。 |
| `OBSERVED_ONLY` | 没有正式账单周期，但可从已解析行得到 `min(transaction_date)` 与 `max(transaction_date)`；这只是文件观察范围。 |
| `UNKNOWN` | 没有可证明正式周期，也没有足以解释的交易日范围。 |

来源映射：

| 来源 | 最终覆盖语义 |
| --- | --- |
| CMB Credit Card PDF | `OBSERVED_ONLY`；`statement_period_start/end = NULL`。issue date/payment date/statement date 均不被冒充为周期。 |
| CCB Credit Card EML | `EXPLICIT`；来源给出的周期原样保存。 |
| CMB Debit Card PDF | `EXPLICIT`；来源给出的流水查询范围原样保存。posting date 保持 unavailable/NULL。 |

## 4. Dedup Semantics

1. **File-level idempotency**：`SHA-256(source_bytes)` 进入 `ImportBatch.source_file_hash` 并有唯一约束。相同字节文件再次 persist 会返回已有 batch，不新增任何 RawTransaction。
2. **In-batch row identity**：`(import_batch_id, source_row_identity)` 唯一。服务会在写入前拒绝 adapter 输出内重复 identity，数据库约束继续兜底。
3. **Cross-batch match**：`match_fingerprint` 由 institution、持久化 account/instrument identity、transaction/posting date、amount、currency，以及仅做空白/case 规整的 raw description 构成。它不含 source row index/identity，也**不是 UNIQUE**。

`dedup_status` 的含义：

- `UNIQUE`：当前没有相同跨账单候选；
- `CANDIDATE_DUPLICATE`：不同 batch 各有一条相同候选键；两条都保留，尚未判断同一经济事件；
- `AMBIGUOUS`：同一 batch 出现完全相同的候选键，例如无时间的同日同商户同金额真实两次消费；所有原始行都保留。

该层不会创建 event link、不会静默删除、不会合并、不会按商户语义归一。描述轻微差异（例如“支付宝-去哪儿网”与公司全称）也不会在 Raw Layer 被语义匹配。

## 5. Overlap Fixture Results

| Case | 结果 |
| --- | --- |
| A：相同文件重传 | 同一 hash 返回已有 ImportBatch；仍是 1 batch、N rows。 |
| B：不同文件含同一交易 | 两行均存储，均标记 `CANDIDATE_DUPLICATE`。 |
| C：真实两笔同日同商户同金额，无时间 | 两行均存储，均标记 `AMBIGUOUS`，不合并。 |
| D：描述轻微差异 | 两行的 candidate fingerprint 不同，均为 `UNIQUE`；留待 Economic Event/normalization。 |

## 6. SQLite Evolution Decision

本轮**不引入 Alembic**。当前项目的 SQLite 演进规范是：新 first-version 表由 SQLAlchemy model + `Base.metadata.create_all()` 创建；只在已有表增加列时，才在 `app.database.py` 中添加具名、幂等的 SQLite helper 或使用有版本的 `backend/scripts/`。

四张消费表为首次创建，不存在消费旧表升级负担，也未新增单独 schema-version 表。后续对已发布消费表的变化应提供对应的幂等 helper（简单列补充）或有版本、可重复执行的 `backend/scripts/` 演进脚本（复杂变化），并在空库、既有 WealthPilot DB、重复 init 三种临时 SQLite 场景验证；不在真实个人数据库上试验。

本轮 integration 测试已覆盖：空 SQLite 初始化、含已有 `portfolios` 的 SQLite 兼容初始化、重复 `init_db()` 幂等，以及四张消费表存在。

## 7. Privacy 与 Pattern Isolation

| Gate | 结果 |
| --- | --- |
| real source committed | NO |
| raw source logged | NO |
| raw source sent to LLM/MCP | NO |
| production upload API | NO |
| Pattern modified | NO |

测试只使用既有脱敏 Adapter fixture 和 synthetic overlap rows；断言不输出 RawTransaction 全表或未脱敏来源 payload。

## 8. Open Items 与 Next Readiness

真正留给下一阶段的事项：

1. 设计 EconomicEvent 及 RawTransaction ↔ Event linking，才能决定候选重复、还款、内部转账、退款与分期的业务含义；
2. 决定多币种 CNY conversion、FX source 与 base amount 的 Event 层规则；
3. 设计分类、用户规则、Travel Context、分析与任何 UI/AI 入口。

**Next Readiness：`READY_FOR_ECONOMIC_EVENT_DESIGN`**。Raw Layer 已能稳定保存、重传幂等、保留不确定性并避免伪造覆盖或日期事实。
