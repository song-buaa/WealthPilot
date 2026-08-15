# WealthPilot v3.15 Multi-Asset Trade Execution
# 多标的交易执行闭环 PRD v1.0 Final

> 产品版本：WealthPilot v3.15.0
>
> PRD 版本：v1.0 Final
>
> 基线版本：WealthPilot v3.14.2
>
> 状态：FINAL / Ready for phased implementation
> 日期：2026-08-15

---

## 0. 文档定位

本 PRD 定义 WealthPilot v3.15 的核心新增能力：

> 用户在「投资决策」对话中输入明确的多标的交易需求，AI 理解交易意图，系统将其转化为可审计、可确认、可恢复的多标的交易执行计划，并在「投资行动」中经最终人工确认后提交至 IBKR。

工程依据：

- WealthPilot v3.14.2 稳定基线；
- `TRADE_EXECUTION_BATCH_CURRENT_CODE_AUDIT.md`；
- IBKR 四标的真实 read-only Contract Probe；
- Decision / ExecutionPlan / Action / OrderManager / IBKR 当前代码审计；
- Gemini Final Review；
- 产品 Owner 已冻结的执行规则。

本 PRD 确认后，不再扩大 v3.15.0 首版范围，按 Phase 1～4 分阶段开发与验收。

---

## 1. 产品目标

用户可以在「投资决策」中直接输入：

> IBKR 现在还有 $16,632 现金，全部用于补充固收：
>
> 1. IBTA：买入约 $11,350；
> 2. VDCA：买入约 $2,850；
> 3. CBU0：买入约 $1,400；
> 4. IB01：剩余资金全部买入。
>
> 全部选择 LSE 美元交易线、Acc 累积型，使用限价单。

目标闭环：

```text
自然语言交易需求
→ AI 分析与交易意图提取
→ Structured Trade Intent
→ 用户确认理解无误
→ Contract Resolution
→ Quote / Price / Quantity / Cash Calculation
→ ExecutionBatch / ExecutionLeg
→ 投资行动统一 Review
→ 最终人工授权
→ Progressive Submission
→ Broker Order Tracking
→ Batch 状态恢复
```

核心价值：

> AI 负责理解“用户想做什么”，确定性系统负责计算“到底下什么单”，用户负责最终授权。

---

## 2. v1 Scope

v3.15.0 首版固定：

```text
Broker              IBKR
Account             单个明确验证的账户
Venue               LSEETF
Trading Currency    USD
Security            ETF
Side                BUY
Order Type          LIMIT
Quantity            Whole Shares
Funding             USD Cash
Execution           Human Confirmed
Submission          Sequential
```

首版不支持：

- SELL / REDUCE；
- Market / Conditional Order；
- fractional shares；
- FX conversion；
- 多 Broker；
- 多账户联合执行；
- Options / Futures；
- 自动无人值守执行；
- 自动跨 Leg 再平衡；
- 自动将失败 Leg 的预算转移给其他 Leg。

---

## 3. 核心产品原则

### 3.1 Batch 是资产配置意图，不是订单集合

`ExecutionBatch` 表示：

> 用户确认的一次多标的资产配置与执行授权。

每个 Leg 的执行都必须持续满足：

- 当前资金约束；
- 用户原始配置意图；
- Instrument identity；
- 当前 Broker state；
- 当前 confirmation version；
- Execution Safety。

### 3.2 AI 与执行数字分离

AI 可以负责：

- 理解自然语言；
- 提取交易约束；
- 判断字段来源；
- 识别 missing / ambiguous；
- 解释投资逻辑和风险。

AI 不负责：

- conId；
- 最终 Contract；
- executable quote；
- final limit；
- tick rounding；
- quantity；
- estimated notional；
- reservation；
- commission；
- remainder；
- Broker state。

> **LLM 不生成最终可执行数字。**

### 3.3 两次人工确认

第一次：`Trade Intent Confirmation`

> 确认 AI 是否正确理解用户想做什么。

第二次：`Execution Confirmation`

> 确认当前准备执行的 Contract、Target、Quantity、Limit 和资金分配是否正确。

### 3.4 Manual Trigger Only

```text
AI 分析
→ Trade Intent Preview
→ 用户点击「生成交易执行计划」
```

真实 Broker Mutation 还必须经过：

```text
Batch Review
→ Final Confirmation
```

### 3.5 Progressive Revalidation

Batch 不采用：

```text
一次算好 4 张订单
→ 机械提交
```

而采用：

```text
提交一个 Leg
→ Broker state 明确
→ 刷新资金 / reservation / quote
→ Revalidate remaining legs
→ 进入下一 Leg
```

### 3.6 Authorization Boundary

> **Execution-time recalculation may reduce authorized exposure without re-confirmation, but may never increase or reallocate exposure beyond the confirmed allocation intent without a new confirmation.**

产品语义：

> 执行现实可以导致少买；未经重新确认，不能扩大 Fixed Target，也不能把一个 Fixed Leg 的预算自动转移给另一个 Fixed Leg。

---

## 4. 目标架构

保留现有：

```text
ExecutionPlan
└── ExecutionTranche
```

继续负责单标的、多批次执行。

新增：

```text
ExecutionBatch
├── ExecutionLeg
├── ExecutionLeg
├── ExecutionLeg
└── ExecutionLeg
```

负责多标的资产配置与统一执行授权。

下游继续复用：

```text
ExecutionLeg
→ SymbolStrategy
→ OrderRecord
→ OrderManager
→ IBKRBrokerAdapter
```

不重构现有 Decision、Single-Symbol ExecutionPlan、SymbolStrategy、OrderRecord、OrderManager、Poller、AuditLog。

---

## 5. Structured Trade Intent

每个字段保存：

```text
value
provenance
source_text
status
```

Provenance：

```text
USER_EXPLICIT
AI_INFERRED
PORTFOLIO_FACT
BROKER_RESOLVED
CALCULATED
```

Status：

```text
CONFIRMED
MISSING
AMBIGUOUS
MANUAL_VERIFICATION_REQUIRED
```

---

## 6. Canonical Trade Intent

```text
broker              IBKR
funding_source      CASH
funding_currency    USD
budget_mode         ALL_AVAILABLE_CASH
stated_cash         16632 USD

venue               LSE
trading_currency    USD
share_class         Acc

side                BUY
order_type          LIMIT
```

Legs：

```text
IBTA  APPROX_AMOUNT  11350 USD
VDCA  APPROX_AMOUNT   2850 USD
CBU0  APPROX_AMOUNT   1400 USD
IB01  REMAINDER
```

`stated_cash` 仅为用户陈述，不是最终资金 authority。

---

## 7. Trade Intent Preview

Decision 页面展示：

```text
资金：
IBKR · USD Cash
使用全部可执行 USD 现金

约束：
LSE / USD / Acc / BUY / LIMIT

配置：
IBTA   ≈ $11,350
VDCA   ≈ $2,850
CBU0   ≈ $1,400
IB01   Remainder
```

存在 `MISSING / AMBIGUOUS / MANUAL_VERIFICATION_REQUIRED` 时，不得进入 READY Batch。

CTA：

> **生成交易执行计划**

---

## 8. BrokerInstrumentResolver

Resolver 必须同时支持：

```text
symbol
+
localSymbol
```

真实 Probe 已确认：

```text
User Alias   CBU0
IBKR symbol  CSBGU0
localSymbol  CBU0
```

v1 Contract 必须满足：

```text
exchange   = LSEETF
currency   = USD
stockType  = ETF
```

并最终：

```text
Unique Qualified conId = 1
```

0 candidate → `UNRESOLVED`

>1 candidate → `AMBIGUOUS`
均 fail closed。

---

## 9. Canonical UAT Contracts

| Alias | conId | symbol | localSymbol | exchange | currency | ISIN |
|---|---:|---|---|---|---|---|
| IBTA | 272686955 | IBTA | IBTA | LSEETF | USD | IE00BYXPSP02 |
| VDCA | 354532794 | VDCA | VDCA | LSEETF | USD | IE00BGYWSV06 |
| CBU0 | 79000139 | CSBGU0 | CBU0 | LSEETF | USD | IE00B3VWN518 |
| IB01 | 354802220 | IB01 | IB01 | LSEETF | USD | IE00BGSF1X88 |

同时保存：

```text
secType
stockType
primaryExchange
tradingClass
longName
marketRuleId
resolution_time
resolver_version
```

`primaryExchange` 不等于 selected direct exchange。CBU0 已证明：

```text
exchange         LSEETF
primaryExchange  EBS
```

---

## 10. Acc Verification

IBKR metadata 无法可靠提供：

```text
shareClass=Acc
accumulating=true
distributionPolicy
```

因此禁止根据名称后缀自动判定 Acc。

v1 使用：

```text
ISIN
+
发行人 / 权威资料人工核验
+
Trusted Instrument Mapping
```

状态：

```text
VERIFIED
MANUAL_VERIFICATION_REQUIRED
```

只有 `VERIFIED` 才允许真实提交。

---

## 11. Trusted Instrument Mapping

至少保存：

```text
user_alias
ISIN
conId
symbol
localSymbol
secType
stockType
exchange
primaryExchange
currency
tradingClass
longName
share_class
verification_status
verification_source
verified_at
resolver_version
```

v1 不建立完整证券主数据系统。

---

## 12. Executable Quote

真实 Batch 不复用单标 ExecutionPlan 的日线 OHLCV。

必须获取：

```text
bid
ask
last
quote_timestamp
market_data_type
source
marketRuleId
minTick
```

---

## 13. Limit Price Model

### Reference Price

```text
Reference Price = Fresh Best Ask
```

Fresh Best Ask 是 BUY LIMIT 的执行基准价，不是最终订单价格。

### Suggested Limit

```text
Fresh Best Ask
→ Quote Quality Guard
→ Spread Guard
→ IBKR MarketRule
→ Tick Normalization
→ Suggested Limit
```

仅所有 Guard 通过时，系统才生成可直接 Review 的 Suggested Limit。

---

## 14. Quote Quality / Spread Guard

至少识别：

```text
LIVE
DELAYED
FROZEN
MISSING
STALE
```

freshness 默认 30 秒，配置化。

Spread：

```text
(ask - bid) / mid
```

Spread Guard threshold 配置化，PRD 不冻结固定百分比；Phase 2 用 IBTA / VDCA / CBU0 / IB01 的真实运行数据校准。

如果 quote 不合格：

> 不自动生成可直接提交的 Suggested Limit。

用户可以手工输入 Limit，但仍需 tick 校验、偏离展示和最终确认。

---

## 15. MarketRule

禁止使用固定 `$0.01` tick。

必须根据：

```text
selected conId
+
selected exchange
+
marketRuleId
+
limit price tier
```

计算合法 tick。

UAT：

```text
IBTA → 1874
VDCA → 98
CBU0 → 983
IB01 → 1874
```

---

## 16. Cash Authority

最终资金 authority 必须来自 Broker 最新事实。

原则：

> 只使用真实 USD Cash / Settled Cash 范围，不使用 BuyingPower 扩大投资能力。

Phase 2 必须实测并冻结 authoritative cash metric。

候选：

```text
CashBalance
TotalCashValue
SettledCash
AvailableFunds
```

---

## 17. Cash Accounting Invariant

正式冻结：

> **任何一美元的资金占用，在 usable-cash 计算中只能被扣除一次。**

禁止：

> Broker cash metric 已经反映 working order，同时 WealthPilot 又将同一 reservation 再减一次。

Phase 2 必须建立统一 `CashAllocationSnapshot` 语义。

---

## 18. Cash Authority Evidence Gate

Phase 2 Mandatory Acceptance 必须实测：

```text
A. 无挂单
B. BUY order OPEN
C. PARTIAL_FILL
D. FILLED
E. CANCELLED / REJECTED
```

观察：

```text
CashBalance
TotalCashValue
SettledCash
AvailableFunds
```

最终冻结：

- 哪个字段作为 cash pool authority；
- 哪些 reservation 已包含在 Broker metric 中；
- 哪些 reservation 仍需本地扣减；
- commission 如何计入；
- 如何避免 double counting。

未完成该 Gate：

> Phase 2 不得宣称 Cash Model ready。

---

## 19. Cash Safety Formula

```text
usable_cash
=
authoritative_cash
- reservations_not_already_reflected_by_authoritative_cash
- estimated_commissions_and_fees
- safety_cushion
```

---

## 20. Reservation

至少识别：

### External Broker Reservation

其他已有 BUY open orders 的未成交资金占用。

### Current Batch Reservation

当前 Batch 已提交但未完全成交的资金占用。

OPEN / PARTIAL_FILL：

```text
remaining_reservation
=
remaining_quantity × order_limit_price
+
remaining fee reserve
```

若已被 authoritative cash metric 完整反映：

> 不得再次扣除。

---

## 21. Commission / Fee / Safety Cushion

Commission / Fee 优先使用：

```text
IBKR WhatIf / Broker commission estimate
```

Safety Cushion 始终独立存在。

v1 默认：

```text
$25 USD
```

配置化：

```text
BATCH_CASH_SAFETY_CUSHION_USD=25
```

不使用百分比 buffer。

---

## 22. Approx Amount Policy

`≈ $11,350` 表示：

> 在不超过 Target Amount 的条件下，购买最大合法整股数量。

```text
quantity
=
floor_to_lot(
  target_amount / suggested_limit
)
```

必须：

```text
estimated_notional <= target_amount
```

`quantity = 0` → `NOT_EXECUTABLE`，不得强制买 1 股。

---

## 23. Fixed Target Legs

```text
IBTA  $11,350
VDCA  $2,850
CBU0  $1,400
```

属于 `APPROX_AMOUNT`。

`target_amount` 是用户确认的资产配置授权。

Progressive Revalidation 可以使实际执行金额下降，但不能未经新确认：

- 增加 Fixed Target；
- 跨 Fixed Leg 自动再分配；
- 将被 Skip / Rejected / Cancel 的 Fixed Leg 预算自动转入 REMAINDER。

---

## 24. REMAINDER Leg

IB01：

```text
allocation_mode = REMAINDER
```

授权语义：

> 吸收当前确认资产配置意图内，由正常执行差异产生的剩余可执行资金。

它不是固定 `$1,032`。

---

## 25. Remainder Authorization Envelope

取消：

> 初始预计 IB01 金额 = 固定 ceiling

改为：

> **Remainder Authorization Envelope**

### 可以自动流入 IB01

`NORMAL_EXECUTION_VARIANCE`：

- 整股取整少用资金；
- 更优执行价格；
- commission 低于预估；
- tick normalization 导致少买；
- 正常 reservation 尾差释放。

无需重新确认。

### 不可以自动流入 IB01

`INTENT_LEVEL_ALLOCATION_RELEASE`：

- CBU0 REJECTED 后用户 Skip；
- 用户主动削减某个 Fixed Target；
- 用户取消某个 Fixed Leg；
- Fixed Target 配置被人工改变。

必须产生新的 confirmation version。

正式原则：

> **Normal execution variance may flow into a confirmed REMAINDER leg. Intent-level allocation release may not flow across legs without a new confirmation.**

---

## 26. Initial IB01 Preview

Batch Review 初始可展示：

```text
IB01
预计 8 股
预计 $xxx
Dynamic Remainder
```

必须提示：

> 最终数量将在真正提交 IB01 前根据最新资金状态重新计算。

---

## 27. Progressive Submission

```text
Batch Confirmed
↓
Validate IBTA
↓
Submit IBTA
↓
Broker state stable & reconcilable
↓
Refresh cash / reservations
↓
Revalidate remaining batch
↓
Validate + Submit VDCA
↓
Refresh / Revalidate
↓
Validate + Submit CBU0
↓
Refresh / Revalidate
↓
Calculate final IB01 remainder
↓
Validate + Submit IB01
```

---

## 28. 不等待 FILLED

进入下一 Leg 不要求前一 Leg `FILLED`。

允许：

```text
SUBMITTED
OPEN
PARTIAL_FILLED
FILLED
```

不允许：

```text
UNKNOWN
TIMEOUT
```

OPEN / PARTIAL_FILL 未成交部分继续作为 committed/reserved capital。

例如：

```text
IBTA 100 shares @ Limit $10
30 filled
70 OPEN
```

则：

```text
30 shares → executed exposure
70 × $10 → remaining reservation
```

---

## 29. Dynamic IB01 Calculation

提交 IB01 前重建：

```text
CashAllocationSnapshot
```

得到：

```text
authoritative cash
- reservations not already reflected
- estimated IB01 commission / fees
- $25 safety cushion
=
IB01 executable budget
```

然后：

```text
fresh best ask
→ quote/spread guard
→ suggested limit
→ marketRule
→ whole-share quantity
```

---

## 30. Authorization Change Rules

无需重新确认：

```text
NORMAL_EXECUTION_VARIANCE
→ IB01 remainder 增加或减少
```

必须重新确认：

```text
INTENT_LEVEL_ALLOCATION_RELEASE
```

例如：

```text
CBU0 rejected
→ user chooses Skip
→ $1,400 released
```

该 $1,400 不得自动增加 IB01。

---

## 31. ExecutionBatch

至少保存：

```text
id
broker
account_ref
funding_currency
budget_mode
source_conversation
source_trade_intent

stated_cash
authoritative_cash_snapshot
cash_accounting_model_version
usable_cash
safety_cushion
estimated_fees
reserved_amount

estimated_total
estimated_residual

status
confirmation_version
confirmation_hash

created_at
updated_at
confirmed_at
```

---

## 32. ExecutionLeg

至少保存：

```text
id
batch_id
sequence

user_alias
allocation_mode
target_amount
authorization_class

resolved_con_id
symbol
local_symbol
sec_type
stock_type
exchange
primary_exchange
currency
trading_class
isin
long_name

share_class_requirement
share_class_verification

quote_bid
quote_ask
quote_last
quote_as_of
quote_quality
market_rule_id

reference_price
suggested_limit
final_limit

estimated_quantity
final_quantity
estimated_notional

execution_variance_amount
released_intent_amount

status
linked_strategy_id
linked_order_id
```

---

## 33. Execution Safety Validator

独立于 Portfolio Risk。

Hard Validate：

```text
contract verified
Acc verified
account correct
exchange correct
currency correct
quote acceptable
limit legal
quantity > 0
fixed target ceiling respected
cash accounting consistent
no reservation double count
cash sufficient
no order conflict
confirmation valid
idempotency valid
```

失败：

```text
BLOCK
```

不得人工 override。

---

## 34. Portfolio Risk

继续复用当前 RiskEngine：

```text
concentration
discipline
portfolio exposure
single order size
```

可以 `PASS / WARNING`。

WARNING 可人工风险确认。

Execution Safety FAIL 永远不可绕过。

---

## 35. Batch Confirmation

投资行动新增：

```text
BatchConfirmationDialog
```

展示：

### Batch

```text
Broker
Masked Account
Authoritative Cash
Cash Accounting Model
Reservations
Estimated Fees
Safety Cushion
Estimated Total
Estimated Residual
```

### Legs

```text
Instrument
Resolved Contract
LSEETF / USD / Acc VERIFIED
Target Amount
Allocation Mode
Quantity
Reference Price
Limit
Estimated Notional
Quote Timestamp
Execution Safety
Portfolio Risk
```

必须提示：

> 本交易计划由多笔独立券商订单组成，并非原子交易。部分订单可能成功而部分失败。

---

## 36. Confirmation Version / Hash

每次确认产生：

```text
confirmation_version
confirmation_hash
```

至少覆盖：

```text
contracts
fixed target amounts
allocation modes
account
currency
budget mode
safety policy
execution policy
trusted mapping version
cash accounting model version
```

REMAINDER 最终 quantity 不作为 immutable hash 核心字段。

确认的是：

> REMAINDER allocation semantics + authorization envelope。

以下变化必须重新确认：

```text
contract changed
fixed target changed / increased
account changed
currency changed
rejected leg skipped
fixed leg cancelled
intent-level released budget reallocated
user modifies allocation intent
```

正常 execution variance 不自动使 confirmation 失效。

---

## 37. Submission Order

默认：

```text
用户原始顺序
```

REMAINDER 永远最后：

```text
1 IBTA
2 VDCA
3 CBU0
4 IB01
```

---

## 38. UNKNOWN / TIMEOUT

任一 Leg：

```text
UNKNOWN
TIMEOUT
```

必须：

```text
HARD STOP
```

禁止：

- 继续下一 Leg；
- 用户简单 override；
- 盲目重新提交。

恢复：

```text
STOP
→ orderRef reconciliation
→ Broker state determined
→ rebuild execution state
→ rebuild CashAllocationSnapshot
→ revalidate
→ resume
```

---

## 39. REJECTED

Broker 明确 REJECTED：

```text
DEFAULT STOP
```

用户可选：

- Retry；
- Skip；
- Terminate。

Skip 表示用户接受最终资产配置偏离原计划，因此必须：

```text
new confirmation_version
```

系统不得：

```text
CBU0 rejected
→ 自动 skip
→ 自动扩大 IB01
```

---

## 40. Partial Fill

Partial Fill：

- 不自动补量；
- 不增加其他 Fixed Leg；
- 不把 intent-level release 自动转入 IB01；
- 未成交部分继续 reservation；
- 由 Poller 持续同步。

是否补足必须由新的明确用户动作决定。

---

## 41. Idempotency / orderRef / Retry

稳定身份：

```text
batch_id
confirmation_version
leg_id
order_record_id
```

同一 submit request 重放：

> 返回已有结果，不再次调用 Broker。

继续使用：

```text
OrderRecord.id
→ IBKR orderRef
```

UNKNOWN / TIMEOUT：

> 先按原 orderRef reconcile。

只有：

```text
Broker 明确 NOT_FOUND
+
状态机允许
+
用户明确 Retry
```

才允许创建新 attempt。

---

## 42. Batch / Leg Status

Batch：

```text
DRAFT
READY
CONFIRMED
SUBMITTING
PARTIALLY_SUBMITTED
SUBMITTED
ATTENTION_REQUIRED
COMPLETED
CANCELLED
```

Leg：

```text
DRAFT
READY
SUBMITTING
SUBMITTED
OPEN
PARTIAL_FILLED
FILLED
REJECTED
UNKNOWN
CANCELLED
NOT_SUBMITTED
```

Broker 真状态仍以 `OrderRecord` 为 authority。

---

## 43. Stop Remaining Execution

Action 页面提供：

> **Stop Remaining Execution**

只停止：

> 尚未提交的 Legs。

不自动：

- cancel 已提交 Broker order；
- rollback 已成交订单；
- 修改 Broker open order。

撤销真实 Open Order 必须走独立明确 mutation 流程。

---

## 44. IBKR Resolved Contract Submission

现有：

```text
ticker + market
→ Stock()
```

升级为：

```text
persisted resolved Contract
→ conId
→ direct exchange
→ currency
→ Broker submission
```

提交时不得重新从 ticker 猜 Contract。

v1 direct exchange：

```text
LSEETF
```

不得用 US、SMART 或 primaryExchange 替代。

---

## 45. Live Safety Layers

### Layer 1 — System / Broker

```text
ENABLE_IBKR_LIVE_TRADING
IBKR_READ_ONLY_MODE
Gateway Read-Only
```

### Layer 2 — Batch

```text
Resolver
Quote
Cash
Reservation
Execution Safety
```

### Layer 3 — Human

```text
confirmation_version
```

### Layer 4 — Per Order

```text
OrderManager
BrokerAdapter
orderRef
state machine
```

四层缺一不可。

---

## 46. Public Demo / Self-use

### Public Demo

- Mock Trade Intent；
- Mock Batch；
- Progressive Revalidation 演示；
- Failure / Recovery UI；
- 永远 Mock Broker；
- 不访问真实 IBKR；
- 不真实下单。

### Self-use

可以：

- IBKR read-only；
- Resolve Contract；
- Quote；
- 真实 ExecutionBatch。

但：

```text
Live Trading remains OFF by default
```

Phase 4 开发完成也不等于授权 Live Mutation。

---

## 47. API Capability

需要能力：

```text
Parse Trade Intent
Generate / Update Intent
Resolve Instruments
Generate Batch
Refresh Batch
Revalidate Batch
Confirm Batch
Submit Next Leg
Resume Batch
Stop Remaining Execution
Get Batch
List Batches
Reconcile Leg
Retry Leg
Skip Leg
Terminate Batch
```

本 PRD 不冻结具体 endpoint URL。

---

## 48. Phase 3 Mock Strategy

Phase 3 不深度 Mock `ib_async`。

使用：

```text
FakeIBKRExecutionAdapter
FakeCashLedger
FakeQuoteProvider
ControllableClock
```

Scenario：

```text
1. all_success
2. open_then_continue
3. partial_fill_with_remaining_reservation
4. rejected_stop
5. unknown_hard_stop
6. timeout_reconcile_found
7. timeout_reconcile_not_found
8. duplicate_submit
9. normal_execution_variance_to_remainder
10. intent_release_requires_reconfirmation
11. quote_stale_during_batch
12. cash_double_count_starvation_guard
```

禁止依赖真实 `sleep()`。

Phase 3 验证 WealthPilot execution state machine；真实 IBKR 行为留到 Phase 4 Paper。

---

## 49. Canonical UAT

真实 Case 必须覆盖：

1. 4 Legs 正确解析；
2. provenance；
3. IB01 = REMAINDER；
4. 四个 conId；
5. CBU0 localSymbol；
6. Acc trusted mapping；
7. LSEETF；
8. marketRule；
9. Fresh Ask Reference；
10. Spread / Quote Guard；
11. Suggested Limit；
12. whole-share calculation；
13. Fixed Target 不超预算；
14. WhatIf / fee estimate；
15. Safety Cushion；
16. Cash Accounting Invariant；
17. 无挂单 cash snapshot；
18. OPEN order cash snapshot；
19. PARTIAL_FILL cash snapshot；
20. reservation 不 double count；
21. Batch Review；
22. confirmation version；
23. Progressive Revalidation；
24. OPEN 状态可继续；
25. OPEN remaining reservation 保留；
26. normal execution variance 可流入 IB01；
27. intent-level released budget 不得流入 IB01；
28. UNKNOWN/TIMEOUT hard stop；
29. REJECTED 默认 stop；
30. Skip 重新确认；
31. duplicate submit 不重复 Broker order；
32. timeout reconcile；
33. Stop Remaining Execution 不撤销已提交 order；
34. Audit 完整可追踪。

---

## 50. Phase 1 — Typed Trade Intent

### Goal

```text
Natural Language
→ Typed Trade Intent
```

### Scope

- Parser；
- provenance；
- missing / ambiguous；
- Trade Intent Preview；
- Manual CTA。

### Not Included

- Contract；
- Quote；
- Cash；
- Quantity；
- Batch ORM；
- Broker Mutation。

### Acceptance

Canonical Case 正确表达：

```text
4 Legs
APPROX_AMOUNT
REMAINDER
LSE
USD
Acc
LIMIT
IBKR
```

---

## 51. Phase 2 — Resolution & Basket Planning

### Goal

生成真实、可执行、但绝不提交的 ExecutionBatch。

### Scope

- BrokerInstrumentResolver；
- Trusted Mapping；
- IBKR ContractDetails；
- executable quote；
- marketRule；
- Limit Price Policy；
- quote quality；
- spread guard；
- Cash Authority Evidence Probe；
- CashAllocationSnapshot；
- Cash Accounting Invariant；
- WhatIf；
- reservation；
- safety cushion；
- amount→quantity；
- ExecutionBatch / ExecutionLeg；
- Execution Safety Validator。

### Mandatory Acceptance

必须完成真实 read-only Cash Authority Evidence Gate：

```text
NO OPEN ORDER
OPEN BUY
PARTIAL FILL
FILLED
CANCELLED / REJECTED
```

冻结：

```text
cash metric semantics
reservation accounting
double-count prevention
```

不得 Broker Mutation。

---

## 52. Phase 3 — Action Review & Progressive Simulation

### Goal

完成完整用户审核和 Progressive Execution 状态模型。

### Scope

- Batch Review；
- BatchConfirmationDialog；
- confirmation version/hash；
- Progressive Revalidation；
- Remainder Authorization Envelope；
- Dynamic Remainder；
- idempotency；
- retry / skip / terminate；
- Stop Remaining Execution；
- deterministic Fake Broker / Cash / Quote / Clock。

### Acceptance

Mock 环境完整验证：

```text
success
open
partial
rejected
unknown
timeout
reconcile
duplicate submit
normal execution variance
intent release
dynamic remainder
cash double-count guard
```

不得真实下单。

---

## 53. Phase 4 — IBKR Submission & Recovery

### Goal

```text
Resolved Contract
→ IBKR LIMIT Order
```

### Scope

- LSEETF submission；
- resolved conId；
- sequential submit；
- Progressive Revalidation；
- Dynamic IB01；
- orderRef reconciliation；
- timeout recovery；
- Poller；
- Batch aggregation。

### Acceptance Sequence

```text
Mock
→ IBKR Paper
→ Dedicated Safety Review
→ Explicit Owner Authorization
→ Live
```

PRD 通过或 Phase 4 开发完成均不代表自动授权 Live Mutation。

---

## 54. Quality Gates

所有 Phase：

```text
targeted tests
pytest
compileall
frontend lint
frontend build
GitHub Quality Gates
```

涉及 Decision 主链路：

```text
Offline M5 18/18
```

Phase 4 额外：

```text
resolved-contract tests
marketRule tests
cash/reservation tests
cash double-count tests
idempotency tests
timeout recovery tests
duplicate-order tests
stop-on-unknown tests
skip-reconfirmation tests
```

---

## 55. Success Criteria

### Intent Correctness

自然语言可稳定结构化。

### Instrument Correctness

真实提交：

```text
100% verified conId
```

### Price Safety

不存在未经 Quote / Spread / MarketRule Guard 的自动 Limit。

### Cash Safety

不存在：

```text
double reservation counting
cash overspend
use of leverage as cash
```

### Allocation Safety

Fixed Target 不被自动扩大或跨 Leg 转移。

### Remainder Safety

正常 execution variance 可进入 REMAINDER；

intent-level allocation release 不得自动进入 REMAINDER。

### Human Control

资产配置意图扩大或改变必须重新确认。

### State Safety

UNKNOWN / TIMEOUT 不继续后续订单。

### Idempotency

```text
duplicate Broker order = 0
```

### Recovery

任何 partial / rejected / timeout / unknown 都必须：

> 状态明确、可恢复、可审计。

---

## 56. Definition of Done

v3.15.0 只有同时满足以下条件才完成：

1. Typed Multi-Asset Trade Intent；
2. provenance；
3. Manual Intent Confirmation；
4. BrokerInstrumentResolver；
5. symbol + localSymbol；
6. trusted Acc mapping；
7. conId authority；
8. LSEETF；
9. executable quote；
10. Best Ask Reference；
11. Quote Quality Guard；
12. Configurable Spread Guard；
13. marketRule；
14. Suggested Limit；
15. Cash Authority Evidence Gate；
16. CashAllocationSnapshot；
17. Cash Accounting Invariant；
18. no reservation double count；
19. WhatIf commission estimate；
20. configurable $25 safety cushion；
21. whole-share amount calculator；
22. Fixed Target authorization；
23. REMAINDER semantics；
24. Remainder Authorization Envelope；
25. ExecutionBatch / ExecutionLeg；
26. Execution Safety Validator；
27. Batch Review；
28. Confirmation Version；
29. Progressive Revalidation；
30. OPEN / PARTIAL state continuation；
31. active-order reservation；
32. Dynamic IB01 calculation；
33. normal execution variance handling；
34. no automatic intent-level reallocation；
35. Sequential Submission；
36. UNKNOWN/TIMEOUT hard stop；
37. reconciliation；
38. REJECTED user decision；
39. Skip requires new confirmation；
40. Partial Fill handling；
41. Stop Remaining Execution；
42. Server idempotency；
43. orderRef recovery；
44. no duplicate Broker order；
45. Audit trail；
46. Phase 3 deterministic fake-broker acceptance；
47. Mock full-chain acceptance；
48. IBKR Paper acceptance；
49. Dedicated Safety Review；
50. Live Mutation separately authorized。

全部完成后发布：

```text
v3.15.0
```

---

## 57. Final Product Decision

WealthPilot v3.15 的核心执行模型正式冻结为：

> **Typed Trade Intent + Verified Contract + Deterministic ExecutionBatch + Progressive Revalidation + Remainder Authorization Envelope + Human Confirmation + Idempotent Broker Execution**

最核心授权原则：

> **正常执行差异产生的剩余资金，可以进入已确认的 REMAINDER Leg；资产配置意图发生变化所释放的预算，不得未经重新确认跨 Leg 自动重新分配。**

以及：

> **执行时可以少买；未经新的人工确认，不能扩大 Fixed Target、改变资产配置方向或重新分配 intent-level budget。**

---

**Document Status: FINAL**
