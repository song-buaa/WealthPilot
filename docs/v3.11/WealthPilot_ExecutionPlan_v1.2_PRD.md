# WealthPilot · Execution Plan Engine v1.2 — PRD

> 版本: v1.2 | 日期: 2026-06-08 | 关联: v3.10 (IBKR 买入恢复)
> 勘探依据: `docs/v3.10/exploration_execution_plan.md` | 评审依据: GPT v1(8.7)→ v1.1(9.2)
> 用途: ① 可再次提交评审 ② 交 Claude Code 先勘探(§10.0)再拆任务实现

---

## v1.2 变更摘要(相对 v1.1)

| # | 变更 | 来源 |
|---|------|------|
| A | 新增**实现优先级与分阶段交付**(M0–M4 为第一阶段 MVP,其余分阶段) | 评审 v1.1 ①/五 |
| B | 新增**用户可微调字段边界表**(§6.4) | 评审 v1.1 ② |
| C | 明确**快速单笔 N=1 如何豁免 `min_batches_required`**(§5.2 C') | 评审 v1.1 ③ |
| D | 港股 price ladder **允许 v1 简化覆盖主流价格段 + fallback degraded**(§5.2 B) | 评审 v1.1 ④ |
| E | 数字来源断言**只校验结构化 `plan_summary_block`;文案禁止新增计划外数字**(§9) | 评审 v1.1 ⑤ |
| F | 默认参数集中到 `execution_plan/config.py` 并落入 `constraints_applied`(§5.2 尾 / §12) | 评审 v1.1 三-2 |
| G | 新增**开发前勘探清单**,先产出 `exploration_execution_plan_v1.1.md`(§10.0) | 评审 v1.1 四 |

---

## v1.1 变更摘要(相对 v1)

| # | 变更 | 来源 |
|---|------|------|
| 1 | 触发价生成写成具体三重约束规则 + 港股报价档位(tick)适配 | 评审①必改 |
| 2 | 新增**用户锚点价**机制,优先级高于系统自动生成;并据此简化批数 N 逻辑 | 评审②必改 |
| 3 | FactorSnapshot 新增 `data_source_meta`(数据来源/时间/缺失项),支撑可信度解释 | 评审③必改 |
| 4 | 新增**手动事件锁** `manual_event_lock`(不自动接财报数据,但允许用户手动暂停) | 评审④必改 |
| 5 | v1 默认**不**自动提交券商条件单;IBKR 服务端条件单降为**显式开启的 v1.1 beta** | 评审⑤必改 |
| 6 | 补全 ExecutionPlan / Tranche / Order 状态机(含 rejected/partial/cancel)+ 计划版本管理 | 评审⑥必改 |
| 7 | 前端区分**快速单笔**与**完整分批计划**两种体验,底层同一套 ExecutionPlan | 评审⑦必改 |
| 8 | 复盘 v1 埋点**一次性买入基准价** + override 事件(分析延后) | 评审⑧必改 |
| 9 | 触发评估增加**宕机补扫** + 看周期 high/low(非仅当前价) | 评审 Q2 |

未变(v1 已确认):产品定位/内核、三条铁律、US+HK、个股分批、新入口收编旧入口、`function_call` orchestrator 而非 `llm_dispatch`、born-activated、两条验收断言。

---

## 0. 一句话定义

在「投资决策」给出持仓建议之后、真正下单之前,新增「执行计划」环节:把一句偏判断的建议翻译成一份**有纪律、有节奏、可触发、可复盘**的分批执行计划。计划里所有数字由规则引擎确定性产出,AI 只解释。

---

## 1. 产品定位与内核

### 1.1 定位边界
WealthPilot 是理性投资决策辅助工作台。本功能**不是**量化平台:不做 Alpha 因子(不预测涨跌)、不做回测、不做全自动交易。只做"执行因子"——回答**怎么买/卖**(分几笔、什么价位、什么节奏、何时该停下复盘),不回答**买不买**(决策模块的事)。

### 1.2 内核
把纪律从一句话变成一个可执行、可触发、可复盘的对象:用户在情绪平稳时把纪律预先固化成计划,价格真到、情绪上头那一刻,驱动行动的是计划而非冲动。这是**反情绪化交易**的落地形态。

### 1.3 三条不可动摇的原则
1. **数字由规则引擎算死,AI 只解释。** 价格/数量/批次/触发价全部确定性产出;AI 拿到已定死的计划,只写 `rationale`/`risk_notes`,无权改任何数字。
2. **约束派生自现有 13 条纪律手册,不新建。** 直接读 `app/discipline/config.py`。
3. **born-activated,杜绝"定义了没人调"。** 第一个 commit 起接入真实调用链,验收用断言证明(§9)。

---

## 2. v1 范围(已锁定)

| 维度 | v1 做 | 不做(延后) |
|------|-------|------|
| 计划类型 | 个股分批(BUY/ADD/REDUCE/SELL) | 组合再平衡 |
| 市场 | 美股 + 港股 | A 股(无 K 线、QMT 网关当前不支持下单) |
| 因子 | 价格分位、波动率/ATR、回撤、均线位置/趋势(本地复算) | 自动事件窗口/财报识别(改为**手动事件锁**,见 §5.4) |
| 数字生成 | 规则引擎(确定性,含锚点价优先) | 策略模板、择时策略、回测 |
| 执行 | 应用侧统一评估 + **默认提醒人确认** | 自动提交券商条件单(降为 §8.3 beta) |
| 入口 | 统一"执行计划",收编旧"加入投资行动";前端分快速/完整两态 | 两套并行后端 |

一次性单笔 = 执行计划的**退化形态**(N=1、立即触发),走同一后端、同一约束。

---

## 3. 用户旅程

```
投资决策(给出 BUY/ADD/REDUCE/SELL 建议)
   │  用户点击入口(可附带 user_anchor_prices)
   ▼
┌──────────────────────────────────────────────────┐
│ wp-generate-execution-plan (type: function_call)    │
│  1. 因子 service → FactorSnapshot(含 data_source_meta)│
│  2. 规则引擎 → 读纪律 + 因子 + 锚点价 → 出权威 plan   │
│  3. LLM → 接收已定死 plan,只写 rationale/risk_notes  │
│  4. validator → 比对 LLM 文案数字 == 规则引擎数字     │
└──────────────────────────────────────────────────┘
   │  ExecutionPlan 草案(draft)
   ▼  用户审阅/纪律边界内微调 → 确认
计划 active → 拆成 N 条 SymbolStrategy(复用现有下单轨道)
   ▼  触发评估循环(盘中定时 + 宕机补扫)
评估各批触发(看周期 high/low + 纪律间隔 + 事件锁)→ armed
   ├─ 默认:提醒用户 → 用户确认 → 下单
   └─ beta(显式开启):IBKR 美/港买入下服务端条件单
   ▼  成交回流 order_records → 更新批次/计划进度
完成 / 触发回撤复盘线 → FactorSnapshot + 结果 + 基准 → Reviewing 复盘
```

---

## 4. 数据模型

挂载: `backend/services/execution_plan/models.py`。

### 4.1 ExecutionPlan(主对象,计划状态的唯一权威)

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 主键 |
| `symbol` / `market` | str | `TICKER:MARKET` / US·HK |
| `side` | enum | BUY/ADD/REDUCE/SELL |
| `plan_status` | enum | draft/active/paused/completed/cancelled/superseded |
| `plan_version` | int | 每次确认修改 +1(§6.3) |
| `source_decision_ref` | str | 关联触发它的决策 |
| `target_basis` / `target_value` | enum/float | QUANTITY 或 POSITION_PCT + 目标值 |
| `user_anchor_prices` | JSON[] | **用户给的心理价位**(可空),优先级最高(§5.2) |
| `one_shot_baseline_price` | float | 计划生成时刻现价,作复盘基准(§7) |
| `manual_event_lock` | JSON | 手动事件锁(§5.4) |
| `factor_snapshot` | JSON | 执行因子快照 + `data_source_meta`(§5.1) |
| `constraints_applied` | JSON | 实际套用的纪律参数及取值(可审计) |
| `rationale` / `risk_notes` | text | **AI 写的**解释 / 风险提示 |
| `created_at/activated_at/completed_at` | datetime | — |

### 4.2 ExecutionTranche(批次,执行子任务)

| 字段 | 类型 | 说明 |
|------|------|------|
| `id`/`plan_id`/`sequence` | — | 主键/外键/批次序号 |
| `quantity` | float | 本批数量 |
| `trigger_type` | enum | IMMEDIATE/PRICE_BELOW/PRICE_ABOVE/MANUAL |
| `trigger_price`/`limit_price` | float | 触发价 / 限价(= 触发价 ± buffer,已按 tick 取整) |
| `order_type` | enum | MARKET/LIMIT/CONDITIONAL_LIMIT |
| `min_interval_days` | int | 距上批成交最小间隔(来自纪律) |
| `status` | enum | pending/armed/triggered/submitted/partial_filled/filled/rejected/failed/skipped/cancelled(§6.1) |
| `linked_symbol_strategy_id`/`linked_order_record_id` | str | 映射现有轨道 |
| `triggered_at/filled_at` | datetime | — |

> ExecutionPlan 确认后向下拆成现有 `symbol_strategies` 行,复用 `OrderManager`/`order_records`/`audit_logs`,不重建执行链路。

---

## 5. 因子与规则引擎

### 5.1 因子 service(`execution_plan/factors.py`,非 Skill,确定性)

| 因子 | 来源 | 状态 |
|------|------|------|
| MA/RSI/MACD/均线位置/趋势 | `tiger_kline_service.py` | ✅ 复用 |
| 52 周高/低 | `market_data/schema.py` QuoteData | ✅ |
| 价格分位、ATR(14)、波动率(年化)、从高点回撤 | Tiger 日线复算 | 🟡 新增(低) |
| 分析师目标价 | 美股 AV;**港股无源** | ⚠️ 降级 |

新增 **`data_source_meta`**(评审③),与因子同存于 `factor_snapshot`:
```
data_source_meta: {
  price_source, kline_source, kline_period, kline_points,
  latest_price_time, is_realtime, delayed_minutes?,
  degraded_fields: [], degraded_reason?
}
```
用途:AI 解释可写"本计划基于 Tiger 日线、行情时间 xxx;港股目标价缺失故未用目标价因子"。**降级原则:** 缺失只标 `null` + 记 `degraded_fields`,绝不 block 计划生成。

### 5.2 规则引擎(`execution_plan/rule_engine.py`,非 Skill,确定性)

读 `discipline/config.py` 的 `get_rules()` + FactorSnapshot + 决策 side + 当前持仓 + `user_anchor_prices`,产出权威 plan。

**(A) 触发价 / 批数的优先级阶梯(评审②核心)**
```
1. 用户给了明确锚点价 → 批次触发价 = 锚点价(排序去重),N = 锚点数
2. 用户只给目标区间 [low, high] → 在区间内按规则铺 N 档
3. 用户没给价位 → 用 ATR/波动率/价格分位自动生成
三种情况产出的计划都必须过同一套纪律硬约束(D)。
```

**(B) 自动生成触发价的三重约束(评审①,取代 v1 的 "f(ATR,波动率)")**
以买入/加仓为例(现价 P,买入档铺在 P 下方;减仓/卖出对称铺在上方):
```
基础步长  base_step% = clamp(1.5 * ATR%, 3%, 8%)        # 单档间距下/上限
档 i 触发价 = P * (1 - (i-1) * base_step%)               # i=1 可设 IMMEDIATE 或贴近 P
约束:
  · 最大偏离:最远一档不得偏离现价超过 max_total_deviation%(默认 25%)
  · 最小价差:相邻档价差 ≥ max(交易所最小报价单位, min_spread%)
  · tick 取整:每档价格按标的报价档位取整
       - 美股:$0.01
       - 港股:按 HKEX 报价单位表(price ladder)取整,不可只按百分比
  · 分位保护:price_percentile 过高(默认 >0.8)时,首档延后或跳过,避免在高位密集挂买单
```
港股 price ladder **v1 允许简化**(评审 v1.1 ④):先实现 HKEX 主流价格段映射(覆盖腾讯/美团/小米/阿里港股等常见标的所在区间)即可;标的价格超出覆盖区间时 **fallback 到保守 $0.01/$0.05 取整并标 `degraded`**,不为全市场完美覆盖阻塞主功能。Claude Code 勘探时先确认是否已有可复用的 tick 工具(§10.0)。

**(C) 批数 N(评审②简化后)**
```
有锚点价     → N = 锚点数
无锚点价     → N_min = ceil(target_position_pct / max_single_add_pct)
              N = max(N_min, min_batches_required)
              若年化波动率 > vol_high_threshold(默认 40%) → N += 1
              N 封顶 max_batches(默认 5)
退化单笔     → N = 1、IMMEDIATE,仍过纪律校验
```
(注:不采用模糊"波动×仓位矩阵",改为上述确定性规则,保证可复现可审计。)

**(C') 退化单笔 N=1 对 `min_batches_required=2` 的豁免(评审 v1.1 ③,解决逻辑互搏)**
`min_batches_required=2` 与"快速单笔 N=1"会冲突,故 N=1 只在**同时满足**下列条件时允许,作为纪律的显式例外:
```
1. 决策类型为 BUY/SELL(一次性买卖),而非 ADD/REDUCE(加/减仓);
2. 本次仓位增量 < max_single_add_pct(0.10);
3. 用户明确选择"快速行动"(前端轻量模式,见 §10 前端两态);
4. 系统记录这是一次 min_batches_required 例外(写入 constraints_applied + audit)。
```
不满足以上任一条 → 回落到常规分批(N ≥ min_batches_required)。这样规则引擎不会自我矛盾。

**(D) 纪律硬约束(全部来自纪律,违反则拦)**
```
最终仓位 ≤ max_position_pct(0.40)
每批     ≤ max_single_add_pct(0.10)
相邻批成交间隔 ≥ min_interval_between_adds_days(1)
当前回撤 ≥ soft_stop_review_trigger_pct(0.30) → 标 requires_review,不自动加仓
```

> **不在 v1:** 任何预测性逻辑(目标价模型、择时、胜率)。规则引擎只在"用户已决定买"的前提下,按纪律把这笔买得更有节奏。

**默认参数集中管理(评审 v1.1 三-2):** 上述阈值(`base_step` ATR 倍数 1.5、上下限 3%/8%、`max_total_deviation` 25%、`vol_high_threshold` 40%、`max_batches` 5、高分位阈值 0.8 等)**不硬编码在 rule_engine 里**,集中放 `backend/services/execution_plan/config.py`;v1 不做前端配置。每次生成计划时,实际取值必须落入 `constraints_applied`,否则复盘时无从得知当时用的是哪套规则。

### 5.3 AI 角色(严格框死)
`wp-generate-execution-plan` 的 `type` 必须为 **`function_call`(确定性 orchestrator)**,**不是 `llm_dispatch`**。这是与被收编的 `wp-action-planner`(LLM 直接出价)的本质区别。内部顺序固定:`因子 → 规则引擎(出 plan dict)→ 才调 LLM`。LLM 被调用时数字已是入参既成事实,只填 `rationale`/`risk_notes`,**不返回任何数字字段**。

### 5.4 手动事件锁(评审④,替代被砍的自动事件因子)
系统不自动识别财报/发布会日期,但 plan 上提供手动锁字段:
```
manual_event_lock: { enabled: bool, reason: str, until_date?: date, scope: "all_remaining"|"after_seq_N" }
```
用途:用户可手动标"财报前不执行后两笔""等发布会后重新评估"。触发评估循环遇到 `enabled=true` 且命中 scope 的批次时跳过(置 skipped 或保持 pending 至 `until_date`)。符合理想发布会、美团/COIN 财报等真实场景,且不需要任何外部数据源。

---

## 6. 状态机与计划版本(评审⑥)

### 6.1 三层状态,主从清晰
```
ExecutionPlan(主状态) > SymbolStrategy(执行子任务) > OrderRecord(成交记录)
SymbolStrategy / Order 不得反向主导 Plan 状态。
```

### 6.2 批次状态机(含异常路径)
```
pending → armed → triggered → submitted → filled
                                  ├→ partial_filled → (剩余继续 submitted,直至 filled 或用户取消余量)
                                  ├→ rejected → armed(重试,上限 N 次)→ 超限则 failed
                                  └→ cancelled
armed/pending → skipped(事件锁/用户跳过)
任意态 → cancelled(计划取消或用户取消该批)
```
v1 部分成交策略(从简):partial_filled 保持 open 直至补满或用户取消余量,缺口记 audit。

### 6.3 计划版本管理
```
active 计划:允许修改未触发批次(价格/数量/取消某批)
已 submitted/filled 批次:不可改
每次确认修改 → plan_version += 1 + audit log
修改若超出纪律边界 → 强制重新 validate(走规则引擎硬约束 D)
```

### 6.4 用户可微调字段边界(评审 v1.1 ②,供前端 + validator 共用)

| 字段 | 可改? | 规则 |
|------|-------|------|
| 锚点价 | 可改 | 改后重新 tick 取整 + 重跑硬约束 D |
| 每批数量 | 可改 | 不得超过 `max_single_add_pct` |
| 批次数 | 可改 | 不得低于 `min_batches_required`,除非走快速单笔(§5.2 C') |
| 目标仓位 | 可改 | 不得超过 `max_position_pct` |
| 事件锁 | 可改 | 可开/关,写 audit |
| 已 submitted/filled 批次 | 不可改 | 只能取消后续批次 |
| `rationale`/`risk_notes` | 不建议改 | 系统生成,改动不影响计划数字 |

任何"可改"字段被改动后,后端 validator 必须以改后值重跑 §5.2(D) 硬约束;越界则拒绝并提示。

---

## 7. 复盘闭环(内核要求 + 评审⑧)
`factor_snapshot` 是决策日志。计划完成或触发回撤复盘线时,连同结果进入 PEER 的 **Reviewing**。

**v1 只埋数据(分析延后):**
- `one_shot_baseline_price`(计划生成时现价)——日后可对照"若一次性买入"
- override 事件、跳过/取消批次、因纪律暂停事件
- 计划触发价 vs 实际成交价、分批实际成交均价

**延后(不在 v1):** 节省/多付金额计算、7/30/90 日表现、投资假设变化追踪。

---

## 8. 触发评估与执行(收编 + 补活 + 评审⑤⑨)

### 8.1 收编旧入口
下掉用户侧"AI 直接出价"路径:`Decision.tsx` 的 `handleGenerateAction → action_planner.plan_actions()`(LLM 产出含价格数量的 ActionListDraft)**不再作为用户入口**。统一入口走执行计划。**复用**其下游 `SymbolStrategy → OrderManager → order_records` 下单轨道作为拆解目标。存量未决 drafts 走老逻辑跑完,不做复杂迁移。

### 8.2 触发评估循环(默认人确认 + 补活遗留条件单字段)
勘探发现 `symbol_strategies` 早有 `CONDITIONAL_LIMIT`/`trigger_price` 字段但无评估引擎——本循环同时补活它。
- `main.py` APScheduler 在现有日同步外**新增盘中定时 job**(如每 15 分钟),遍历 active 计划的 pending/armed 批次。
- 触发判断(评审⑨):**不只看当前价,要看本周期 high/low**,捕捉盘中短暂触达;并校验纪律间隔、回撤复盘线、事件锁。
- **宕机补扫(评审⑨):** job 恢复后检查"上次扫描时间→当前"是否曾穿越触发价,补判遗漏。
- 命中 → 置 `armed`。**v1 默认:提醒用户 → 用户确认 → 下单(人签字,符合内核)。**

### 8.3 IBKR 服务端条件单(评审⑤:降为显式开启的 v1.1 beta)
- **v1 默认关闭。** 主闭环只跑"应用侧评估 → 提醒 → 人确认下单"。
- **beta(用户 per-plan 显式开启):** 对 IBKR(美/港买入)下服务端条件单(`PriceCondition`/`bracketOrder`/OCA,勘探已确认可用)。
- 推迟理由:产品验证期、paper 账户(DUQ629797)尚未验证、条件单的取消/修改/部分成交/失败异常会显著增加应用侧与券商侧状态同步复杂度。
- Tiger/Futu 港股当前 sell-only,买入一律走 IBKR。

---

## 9. 验收断言(硬性,防 C0 覆辙)
脚本验证须含两条,任一不过即未完成:
1. **真被调用断言:** trace/日志证明走的是 `invoke_skill("wp-generate-execution-plan")` 真实调用,非 fallback / LLM 自由生成。
2. **数字来源断言(评审 v1.1 ⑤,避免误伤自然语言):**
   - 校验对象限定为结构化的 **`plan_summary_block`**(由规则引擎 plan dict 直接渲染的价格/数量/批次字段),与 plan dict **逐一一致**。
   - `rationale`/`risk_notes` 自由文案**禁止新增任何 plan dict 之外的价格、数量、批次数字**;若需引用波动率/回撤/分位等,必须取自 `factor_snapshot`。这样像"不要在 5% 波动内频繁调整"这类合法表述不会被误判。
   - 由扩展后的 `wp-output-validator`(新增 `plan_value_mismatch` 规则,约 50 行)拦截违例。

---

## 10. 实现里程碑(供 Claude Code 拆任务)

### 10.0 开发前勘探(先做,勿直接写代码 — 评审 v1.1 四)
进入 M0 前,Claude Code 先做一次只读勘探,产出 `docs/v3.10/exploration_execution_plan_v1.1.md`,逐项摸清后再拆任务:
```
1.  Decision → Action 入口的具体文件与调用链(Decision.tsx → /api/action/plan → action_planner)
2.  action_planner.plan_actions() 当前返回结构(ActionListDraft 字段)
3.  SymbolStrategy 当前字段与状态枚举(order_type/trigger_price/status)
4.  OrderManager / order_records 可复用字段与九态
5.  tiger_kline_service.py 可获得的 K 线周期、字段、市场覆盖(美/港)
6.  QuoteData 里 52w high/low 是否可靠、是否实时
7.  discipline/config.py 的 13 条规则结构与 get_rules() 取值
8.  APScheduler 现有 job 注册方式(main.py)
9.  wp-output-validator 当前扩展方式(ValidationFailure 结构)
10. 是否已有港股 tick/price ladder 工具可复用;无则规模多大
11. 前端 Decision.tsx 与投资行动模块 UI 的具体插入点
```

### 10.1 分阶段交付优先级(评审 v1.1 ①/五,避免一口气做太重)
不承诺 M0–M8 一次做完,按四阶段推进,每阶段独立脚本验证后再进下一阶段:

| 阶段 | 里程碑 | 阶段目标 |
|------|--------|---------|
| **第一阶段(MVP,必须交付)** | M0–M4 + M7 最小版(生成/审阅入口) | 证明:投资建议能生成一份**可信、确定性、可解释**的 ExecutionPlan 草案 |
| **第二阶段** | M5 + M7 完整 | 计划确认 → 拆 SymbolStrategy → 进投资行动模块;收编旧入口;前端两态完整 |
| **第三阶段** | M6 + M8 | 触发评估(可先脚本/手动触发,再接 APScheduler)、提醒、成交回流、复盘埋点落库 |
| **第四阶段** | Mβ | IBKR 服务端条件单 beta(显式开启) |

弱化交付提示:M5 第一步先做到 `draft → active`,异常状态机可随第二阶段补全;M6 触发可先手动/脚本触发验证逻辑,再接定时 job;M8 先落库,Reviewing UI 后置。编号不等于强制顺序——M6 实际可放在 M7 计划确认之后。

### 10.2 全量里程碑清单

| M | 内容 | 验收 |
|---|------|------|
| **M0** | 数据模型与迁移:Plan/Tranche 表;新增 `user_anchor_prices`/`manual_event_lock`/`plan_version`/`one_shot_baseline_price`/`data_source_meta` | 读写正常 |
| **M1** | 因子 service:ATR/波动率/价格分位/回撤 + `data_source_meta`;港股目标价降级 | 美+港真实持仓出快照,缺失标 degraded 不报错 |
| **M2** | 规则引擎:§5.2 优先级阶梯 + 三重触发价约束(含 tick/港股档位)+ N 逻辑 + 纪律硬约束 | 给定输入输出可复现,约束不被违反,锚点价优先生效 |
| **M3** | `wp-generate-execution-plan` Skill(function_call orchestrator):因子→规则→LLM 仅解释→validator | LLM 输出不含数字字段;按钮 born-activated |
| **M4** | validator 数值比对扩展(`plan_value_mismatch`) | 故意改 LLM 数字 → 被拦 |
| **M5** | 计划确认拆 SymbolStrategy + **收编旧入口** + **状态机(§6,含 rejected/partial/cancel)** + 计划版本 | 旧入口不可达;异常路径有定义;退化单笔同路径 |
| **M6** | 触发评估循环:盘中 job + 周期 high/low + 宕机补扫 + 事件锁;默认提醒人确认 | active 计划被定时评估、到价 armed、补扫生效 |
| **M7** | 前端:统一入口收编旧按钮;**快速单笔 vs 完整分批两态**(按决策类型自动路由);锚点价输入、计划审阅/微调、因子+数据来源展示 | 简单买卖走轻量卡,加减仓走完整计划 |
| **M8** | 复盘埋点:`one_shot_baseline_price` + override/跳过/暂停事件 + 计划价 vs 成交价进入 Reviewing | 完成/触发复盘线的计划可在复盘中回看原始数据 |
| **Mβ** | (v1.1 beta,可后置)IBKR 服务端条件单显式开启路径 | 开启后到价自动下条件单;默认关闭 |

节奏:M0–M2(确定性内核)先行并独立脚本验证 → M3–M4(Skill+约束)→ M5–M7(执行/收编/前端)→ M8(复盘)→ Mβ(beta)。

### 前端两态(评审⑦)
底层同一套 ExecutionPlan,体验分层:
- **快速行动卡:** N=1、立即触发、最少字段。简单 BUY/SELL。
- **完整执行计划:** N≥2、展示触发价/批次/因子/约束/数据来源。ADD/REDUCE/逢低买入/逐步减仓。
- 路由:按决策类型默认自动判断,用户可手动切换。

---

## 11. 明确不做(v1 out of scope)
A 股执行;组合再平衡;**自动**财报/事件识别(保留手动事件锁);Alpha/预测类因子;策略模板与回测;无人值守全自动交易;非等分"越跌越买";HK 分析师目标价数据源补齐;复盘的节省金额/多周期表现分析(仅埋点)。

---

## 12. 评审遗留与仍开放项

**已解决(v1.1 + v1.2):** 触发价具体化、锚点价、数据可信度、手动事件锁、条件单默认关闭、状态机、前端分层、复盘基准、宕机补扫与周期高低;分阶段交付、可微调边界、N=1 豁免、港股档位简化、validator 防误伤、默认参数集中化。

**已采纳为实现默认(GPT v1.1 建议,无需再争论,Claude Code 照此实现):**
1. 港股 price ladder:先勘探有无现成工具(§10.0-10);无则自建主流价格段映射,超区间 fallback 保守 tick + degraded。优先"主流港股正确"而非"全市场完美"。
2. 默认参数:集中于 `execution_plan/config.py`,不做前端配置,实际取值落入 `constraints_applied`(§5.2 尾)。
3. 纪律间隔 vs 价格机会:**纪律优先**——到价但间隔未满则暂缓并提示;用户可手动 override,但记 audit"主动突破纪律"。

**仅剩需你/再评审拍板(非阻塞):** 以上三条的默认取值(如 `max_total_deviation` 25%、`vol_high_threshold` 40%)是否就用这组初值起步——倾向先用默认值跑起来,复盘时按 `constraints_applied` 回看再调。
