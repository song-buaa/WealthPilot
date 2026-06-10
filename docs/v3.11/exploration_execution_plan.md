# Execution Plan Engine v1.2 — §10.0 勘探报告

> 日期: 2026-06-08 | 执行者: Claude Code | 性质: 只读勘探，未改任何代码
> PRD: `docs/v3.11/WealthPilot_ExecutionPlan_v1.2_PRD.md`

---

## §10.0 清单逐项核实

### 1. Decision → Action 入口的具体文件与调用链

**现状确认（v3.10 勘探已覆盖，此处补精确行号）:**

```
Decision.tsx:462  handleGenerateAction(msgId)
  → 构建 conversation_context + expressing_output
  → api.ts:465  actionApi.generateDraft(POST /api/action/drafts/generate)
  → api/action.py:115  generate_draft()
  → action_planner.py:89  plan_actions(ActionPlannerInput)  ← LLM 产出含价格
  → OrderManager.create_draft()  → action_drafts 表
```

**前端按钮**: `ActionListGenerateButton.tsx:23`（三态组件），由 `Decision.tsx:933` 条件渲染。

**对实现的影响**: PRD §8.1 要收编此入口。按钮从调 `action_planner.plan_actions()` 改调 `invoke_skill("wp-generate-execution-plan")`。旧按钮组件可复用，只改 onClick handler。

**分类: ✅ 可复用**（按钮组件、endpoint 结构）

---

### 2. action_planner.plan_actions() 当前返回结构

`backend/services/action/action_planner.py:58-75` — `ActionListDraft`:
```python
@dataclass
class ActionListDraft:
    conversation_id: str
    decision_summary: str
    allocation_intents: list[AllocationIntentDraft]
    symbol_strategies: list[SymbolStrategyDraft]  # symbol/side/quantity/order_type/limit_price
    risk_notes: list[str]
    missing_fields: list[MissingField]
```

`SymbolStrategyDraft` (`action_planner.py:44-54`): `symbol/side/quantity/quantity_pct/order_type/trigger_price/limit_price/value_sources`。

**对实现的影响**: 新 Skill 输出结构是 `ExecutionPlan`（含 tranches），不是 `ActionListDraft`。但最终确认后拆成 `SymbolStrategy` 行（复用）。两套输出结构并存：新 Skill → `ExecutionPlan` → 确认 → 拆 `SymbolStrategy`；旧 `action_planner` → `ActionListDraft`（被收编后退役）。

**分类: ✅ 下游可复用**（SymbolStrategy → OrderManager）；🟡 上游需新增（ExecutionPlan 数据模型）

---

### 3. SymbolStrategy 当前字段与状态枚举

`backend/services/action/models.py:114-164`:
```python
symbol, side, target_quantity, target_quantity_pct, cumulative_filled_quantity,
order_type (LIMIT/CONDITIONAL_LIMIT),  # L146-147
trigger_price (Numeric, nullable),      # L148 — 早就有但从未被评估引擎填充
limit_price, status (active/paused/completed/discarded)
```

**关键发现**: `trigger_price` 和 `CONDITIONAL_LIMIT` 字段**已存在但从未被使用**（PRD §8.2 提到的"补活遗留条件单字段"）。触发评估循环（M6）可直接写入这些字段。

**分类: ✅ 可复用**（字段已定义，无需 schema 迁移）

---

### 4. OrderManager / order_records 可复用字段与九态

`backend/services/action/state_machine.py:101-118` — 九态确认:
```
created → submitted_to_broker → broker_pending
  → partially_filled → filled
  → cancelled / rejected / expired / unknown
```

`order_records` (`models.py:171-220`): `broker_order_id / quantity / filled_quantity / order_type / limit_price / stop_price / avg_filled_price / status / raw_broker_response`。

**对实现的影响**: ExecutionTranche 的 `linked_order_record_id` 直接关联此表。九态与 Tranche 状态机（§6.2）有映射关系但不同一——Tranche 有 `pending/armed/triggered` 等 plan 级状态，order_records 是券商级状态。

**分类: ✅ 可复用**（OrderManager / order_records / 九态状态机全部复用）

---

### 5. tiger_kline_service.py 可获得的 K 线周期、字段、市场覆盖

`backend/services/market_data/tiger_kline_service.py:107-178`:
- **周期**: `period="day"`，默认 60 根日 K
- **字段**: `time / open / high / low / close / volume`（标准 OHLCV）
- **市场**: 美股 ✅ + 港股 ✅（Tiger SDK 支持 HK 代码如 0700）
- **已有指标**: MA5/MA20, RSI(14), MACD(12,26,9), ma_position, trend_signal

**需新增的因子**（PRD §5.1，基于同一 K 线数据）:
- **ATR(14)**: `(high - low)` 的 14 日 EMA，pandas ~10 行
- **波动率(年化)**: `closes.pct_change().std() * sqrt(252)`，~3 行
- **价格分位**: `(price - low_52w) / (high_52w - low_52w)`，~3 行（需 52w 数据，见 #6）
- **从高点回撤**: `(price - closes.max()) / closes.max()`，~3 行

**分类: ✅ K 线可复用；🟡 4 个新因子需新增（工作量低，均为 pandas 单行到十行级）**

---

### 6. QuoteData 里 52w high/low 是否可靠、是否实时

**两个来源**:
1. **富途 QuoteData** (`futu_quote_service.py:74-75`): `high_52w = get_field("highest52weeks_price")`、`low_52w = get_field("lowest52weeks_price")`。实时 snapshot，**美股+港股均有**。
2. **Alpha Vantage FundamentalsData** (`av_fundamentals_service.py:211-212`): `high_52w = get_f("52WeekHigh")`。日更新。**仅美股**。

`MarketDataBundle.to_snapshot_dict()` (`schema.py:138-139`): 优先取 QuoteData，fallback FundamentalsData。

**可靠性**: 富途 52w 来自交易所 snapshot，可靠。但需要富途 OpenD 在线。**降级**: 若富途不在线且 AV 无港股 → 52w 为 `None` → price_percentile 因子降级为 `null`，标 `degraded_fields`。

**分类: ✅ 可用（美股双源、港股单源富途）；⚠️ 港股无富途时降级**

---

### 7. discipline/config.py 的 13 条规则结构与 get_rules() 取值

`app/discipline/config.py:15-60` — `_DEFAULT_RULES` 结构化 JSON:

```python
"single_asset_limits": {
    "max_position_pct": 0.40,              # §5.2(D) 直接引用
    "warning_position_pct": 0.30,
    "core_holding_floor_pct": 0.10,
},
"position_sizing": {
    "max_single_add_pct": 0.10,            # §5.2(D) 直接引用
    "min_batches_required": 2,             # §5.2(C) 直接引用
    "min_interval_between_adds_days": 1,   # §5.2(D) 直接引用
},
"stop_loss_rules": {
    "soft_stop_review_trigger_pct": 0.30,  # §5.2(D) 直接引用
},
```

`get_rules()` (`config.py`): 优先从 `data/rules_config.json` 加载（用户自定义后），fallback 到 `_DEFAULT_RULES`。

**对实现的影响**: 规则引擎 `execution_plan/rule_engine.py` 直接 `from app.discipline.config import get_rules` 读取，不新建约束体系。PRD §5.2(D) 的 4 条硬约束全部有对应字段。

**分类: ✅ 完全可复用，不新建**

---

### 8. APScheduler 现有 job 注册方式

`backend/main.py:66-80`:
```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Shanghai"))
scheduler.add_job(
    lambda: [broker_sync_api._run_sync(b, "cron") for b in [...]],
    trigger=CronTrigger(hour=22, minute=0),
    id="daily_broker_sync",
    replace_existing=True,
    misfire_grace_time=3600,
)
```

**对实现的影响**: M6 触发评估循环在此新增一个 `IntervalTrigger`（如每 15 分钟盘中）。scheduler 已是 lifespan 级单例，加 job 只需一行 `scheduler.add_job()`。

**分类: ✅ 可复用（加 job 即可）；🟡 需要盘中时间判断逻辑（美股/港股交易时段不同）**

---

### 9. wp-output-validator 当前扩展方式

`backend/graph/decision_validator.py`:

**架构**:
- `ValidationFailure(rule: str, message: str, severity: str)` — 通用失败条目
- `ValidationResult(passed, failures, action, intent_type)` — 结果容器
- `validate_decision_output(result, intent_type, ...)` — 主入口，按 intent_type 分发到专项层

**扩展方式**:
1. 新增函数 `_validate_execution_plan(plan_dict, llm_output)` — 对比 plan_summary_block 中的价格/数量/批次与 plan_dict 中的权威数字
2. 新增规则名 `plan_value_mismatch` (hard severity)
3. 在主入口加 `if intent_type == "ExecutionPlan"` 分支调用
4. **约 50 行**（遍历 plan_summary_block 的数值字段，逐一 assert == plan_dict 对应值）

**PRD §9 的关键设计**:
- 只校验结构化 `plan_summary_block`（规则引擎 plan dict 直接渲染），不扫 rationale/risk_notes 文案
- rationale/risk_notes 中引用的数字必须取自 `factor_snapshot`，不允许 plan dict 之外的数字

**可行性**: ✅ **完全可行**。ValidationFailure 结构天然支持，只需加函数+规则名+分支。

**分类: ✅ 可扩展（~50 行新增，不改现有检查项）**

---

### 10. 是否已有港股 tick/price ladder 工具可复用

**结论: ❌ 未找到。**

全量 grep `tick|price.ladder|lot.size|board.lot|minimum.*price.*variation` 在整个仓库零命中（排除无关的 symbol ticker 引用）。

**需自建规模评估**:

HKEX 报价档位表（price ladder）是分段的：

| 价格区间 | 最小报价单位 |
|---------|------------|
| $0.01 – $0.25 | $0.001 |
| $0.25 – $0.50 | $0.005 |
| $0.50 – $10 | $0.010 |
| $10 – $20 | $0.020 |
| $20 – $100 | $0.050 |
| $100 – $200 | $0.100 |
| $200 – $500 | $0.200 |
| $500 – $1000 | $0.500 |
| $1000 – $2000 | $1.000 |
| $2000 – $5000 | $2.000 |
| $5000+ | $5.000 |

实现: 一个 `hk_tick_size(price: float) -> float` 函数 + 一个 `round_to_tick(price, market) -> float` 包装。**约 30 行**。

PRD 允许 v1 简化：覆盖主流价格段（腾讯 ~$300-400、美团 ~$100-200、小米 ~$20-30 均在常见区间），超区间 fallback 保守 $0.01 + 标 `degraded`。

美股固定 $0.01，无需映射。

**分类: 🟡 需新增（~30 行，低风险确定性代码）**

---

### 11. 前端 Decision.tsx 与投资行动模块 UI 的具体插入点

**Decision.tsx 按钮位置**:
- `Decision.tsx:933` — `ActionListGenerateButton` 条件渲染（`msg.actionable || actionDraftStatus`）
- `Decision.tsx:462` — `handleGenerateAction()` 点击处理
- `Decision.tsx:694` — `AiMessage` 组件传递 `onGenerateAction` prop

**收编方案**（PRD §8.1）:
1. `handleGenerateAction` 改为调新 endpoint（`POST /api/execution-plan/generate`）→ `invoke_skill("wp-generate-execution-plan")`
2. 按钮文案从"生成行动清单"改为"生成执行计划"（或根据快速/完整两态动态切换）
3. `ActionListGenerateButton.tsx` 组件可复用（三态逻辑不变）
4. 新增：执行计划审阅/微调面板（替代 `ActionDraftCard`）

**投资行动页面** (`Action.tsx`):
- 已有策略列表 + 订单列表 + 状态展示
- 执行计划确认后拆成的 `SymbolStrategy` 自然出现在此页面
- 不需要大改 Action.tsx，只需在 SymbolStrategy 卡片上加 `plan_id` 关联展示

**分类: ✅ 按钮组件可复用；🟡 需新增执行计划审阅面板（M7 前端）**

---

## 硬约束可行性确认

### 约束 A: 唯一新增 1 个 Skill (function_call 类型)

**可行。** 注册链路：
1. 新建 `skills/wp-generate-execution-plan/SKILL.md`（frontmatter: `type: function_call`, `tool_name: generate_execution_plan`）
2. 新建 `backend/graph/tools.py` 里加 `execute_generate_execution_plan` 函数 + 注册到 `TOOL_EXECUTORS`
3. 该函数内部按固定顺序调用 service：`factors.py → rule_engine.py → LLM(只写文案) → validator`
4. 前端按钮调 `invoke_skill("wp-generate-execution-plan", ...)` — 实际走 `SkillsLoader._invoke_function_call → call_tool`

**不需要碰 Planning/Executing agent 的路由分支**——与 `wp-action-planner` 一样是 PEER 链路外的旁路调用。

### 约束 B: 数字由规则引擎确定性产出，LLM 只写文案

**可行。** 因子 service 和规则引擎都是纯 Python 函数，不涉及 LLM。LLM 被调用时接收已定死的 plan dict 作为入参，prompt 模板严格约束只输出 rationale/risk_notes 文本字段。validator 扩展校验 plan_summary_block。

### 约束 C: born-activated

**可行。** function_call 类型的 Skill 注册后，`invoke_skill("wp-generate-execution-plan")` 立即可调用。第一个 commit（M3）就接按钮 → invoke_skill → 真实调用链。不存在 C0 那种"llm_dispatch 但白名单没放行"的问题。

### 约束 D: 约束全部读 discipline/config.py 派生

**可行。** `get_rules()` 返回的 dict 包含 §5.2(D) 的全部 4 条硬约束参数。规则引擎直接 import 读取。

---

## 可复用 / 需新增 / 有风险 汇总

### ✅ 可复用

| 能力 | 位置 | 复用方式 |
|------|------|---------|
| MA/RSI/MACD/均线/趋势 | `tiger_kline_service.py` | 因子 service 直接 import |
| 52w high/low | `futu_quote_service.py` + `av_fundamentals_service.py` | QuoteData/FundamentalsData |
| 纪律约束参数 | `discipline/config.py get_rules()` | 规则引擎直接读取 |
| SymbolStrategy 表 + trigger_price 字段 | `action/models.py:146-148` | 已定义，直接填充 |
| OrderManager + order_records + 九态 | `order_manager.py` + `state_machine.py` | 下单轨道全复用 |
| APScheduler | `main.py:66-80` | 加 job 即可 |
| Skill invoke 机制 | `skills/__init__.py + loader.py + graph/tools.py` | 注册 TOOL_EXECUTORS |
| wp-output-validator 结构 | `decision_validator.py` | 加新规则函数 |
| 前端按钮组件 | `ActionListGenerateButton.tsx` | 改 onClick handler |

### 🟡 需新增

| 能力 | 建议位置 | 规模 |
|------|---------|------|
| ExecutionPlan / ExecutionTranche 数据模型 | `backend/services/execution_plan/models.py` | 新建 |
| 因子: ATR/波动率/价格分位/回撤 | `backend/services/execution_plan/factors.py` | ~60 行 |
| 规则引擎: 优先级阶梯 + 三重约束 + N 逻辑 | `backend/services/execution_plan/rule_engine.py` | ~200 行 |
| 执行计划默认参数 | `backend/services/execution_plan/config.py` | ~30 行 |
| 港股 tick/price ladder | `backend/services/execution_plan/hk_tick.py` | **~30 行** |
| wp-generate-execution-plan SKILL.md | `skills/wp-generate-execution-plan/SKILL.md` | frontmatter |
| Tool executor 函数 | `backend/graph/tools.py` 新增条目 | ~50 行 |
| validator plan_value_mismatch 规则 | `decision_validator.py` 扩展 | ~50 行 |
| 前端执行计划审阅面板 | `frontend/src/components/` | M7 |
| 触发评估循环 | `main.py` APScheduler 新 job | M6 |

### ⚠️ 有风险需进一步验证

| 项 | 风险 | 建议 |
|----|------|------|
| 港股 52w 数据无富途时 | 富途 OpenD 离线 → 52w 为 null → price_percentile 降级 | 降级标 degraded，不 block |
| K 线数据延迟 | Tiger 日线 TTL 4h，非实时 | 触发评估用实时行情，因子快照用日线，可接受 |
| 宕机补扫 | APScheduler misfire_grace_time 能否覆盖补扫场景 | 先验证 misfire_grace_time 行为，可能需自建补扫逻辑 |
