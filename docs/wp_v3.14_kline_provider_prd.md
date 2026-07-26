# WealthPilot v3.14 PRD —— Execution Plan K线数据源解耦

> 一版一事：把"分批计划引擎拿哪里的 K线"这件事，从 broker 硬依赖改成可注册的数据源接口（Broker → AV → Seed），让 demo 环境无需安装 OpenD / 国金网关也能正常出分批计划。
>
> 状态：已完成 · Codex 接管基线（2026-07-26）
> 执行机器：**MacBook（开发）**；部署：Mac mini 仅 `git pull`
> 关联：v3.11 Execution Plan Engine、v3.13 环境隔离（PUBLIC_DEMO_MODE）

---

## 一、背景与问题

线上 demo（Mac mini，`PUBLIC_DEMO_MODE`）点「分批计划」必现报错：

```
insufficient_data: 当前无行情/K线数据（ATR/波动率/分位均不可用）
factor_snapshot: current_price=null, kline_source=none, kline_points=0
degraded_reason: Tiger K线获取失败或为空; 富途 52w 数据不可用
```

**根因不是偶发掉线，是 Mac mini 上 OpenD 与国金 QMT 网关根本未安装**，broker K线在 demo 上永远为空。

矛盾点：同一份决策报告（估值、压力测试、分析师目标价）是**正常出的**——说明持仓估值/价格走的是 AV / 持仓快照这条活路，只有 Execution Plan Engine 的 ATR/波动率/分位单独依赖 broker 日 K，所以单点失败。

当前真实状态已是"**隐性两套**"：一份代码在 dev（有 broker）和 demo（无 broker）行为分叉，且 demo 这条必崩。本版目标是消除这种隐性分叉，收敛成一份代码 + 一个配置点。

## 二、目标 / 非目标

**目标**
- Execution Plan Engine 不再直接调用 Tiger/Futu/国金，改为面向 `KlineProvider` 接口编程。
- 数据源差异收敛到**唯一一个配置点**：启动时注册哪些 provider、按什么优先级。
- demo 模式下 broker 适配器**不注册**，K线走 AV → Seed，分批计划正常产出。
- dev 模式行为不变；broker 临时不可用时自动降级到 AV，不再硬失败。
- **前端零感知**：`factor_snapshot` 字段结构与语义完全不变。

**非目标（本版不做）**
- 不动决策报告/估值链路（它已正常）。
- 不删除 broker 代码（soft：仅不注册，保留实现）。
- 不引入新的技术指标或新算法。
- 不改前端任何代码。
- 不在 Mac mini 上安装任何 broker 网关。

## 三、核心设计

### 3.1 两层拆分（关键）

把"**数据从哪来**"和"**因子怎么算**"彻底分开，杜绝按源分叉：

```
KlineProvider  →  返回 原始 OHLC bars + 元数据（谁供的、周期、时间、实时性）
       ↓
FactorComputer →  单一实现，把 bars 统一算成 factor_snapshot
                  （ATR / MA5 / MA20 / RSI14 / MACD / 52w高低 / 分位 …）
```

> 因子计算只有一份，无论 bars 来自 broker、AV 还是 seed，输出 shape 一模一样 —— 这是前端零感知的硬保证，也是"绝不写 if source == broker: ... else: ..." 的根本手段。

### 3.2 接口

```python
class KlineProvider(ABC):
    name: str  # "broker" | "av" | "seed"

    @abstractmethod
    def get_kline(self, symbol: str, market: str,
                  period: str = "day", count: int = 120) -> KlineResult | None:
        """成功返回 KlineResult；无数据/失败返回 None（不抛异常，由 registry 决定降级）"""

@dataclass
class KlineResult:
    bars: list[OHLCV]          # 至少够算 MA20/RSI14/52w，建议 ≥120 根
    source: str                # provider.name，用于回填 kline_source
    period: str                # "day"
    latest_price_time: str
    is_realtime: bool
    delayed_minutes: int | None
```

### 3.3 注册表 / 工厂（唯一配置点）

```python
class KlineProviderRegistry:
    def __init__(self, providers: list[KlineProvider]):  # 有序
        self._providers = providers

    def resolve(self, symbol, market, period="day", count=120):
        degraded = []
        for p in self._providers:
            res = p.get_kline(symbol, market, period, count)
            if res and res.bars:
                return res, degraded   # 命中即返回，degraded 记录前面失败的源
            degraded.append(p.name)
        return None, degraded          # 全空 → 上层走 insufficient_data 兜底
```

环境差异**只在这里**：

| 环境 | 注册顺序 | 说明 |
|---|---|---|
| dev（默认） | `[Broker, AV]` | broker 优先，掉了自动降级到 AV；不静默使用 Demo fixture |
| demo（`PUBLIC_DEMO_MODE=true`） | `[AV, Seed]` | broker 适配器**根本不实例化/不注册** |

```python
def build_kline_registry() -> KlineProviderRegistry:
    providers = []
    if not settings.PUBLIC_DEMO_MODE:
        providers.append(BrokerKlineProvider(...))
    providers.append(AVKlineProvider(...))
    if settings.PUBLIC_DEMO_MODE:
        providers.append(SeedKlineProvider(...))
    return KlineProviderRegistry(providers)
```

## 四、三个 Provider 实现要点

### 4.1 BrokerKlineProvider
- 把**现有** Tiger/Futu/国金 K线获取逻辑原样搬进来，不重写，只是包一层接口。
- 失败/超时/空 → 返回 `None`（不要在这里抛到引擎层）。

### 4.2 AVKlineProvider
- 用 AV `TIME_SERIES_DAILY`（已是项目内主源，复用现有 client）拿完整 OHLC。
- **覆盖能力（重要）**：
  - 美股：可用 ✅
  - 港股：部分可用，需实测 symbol 映射 ⚠️
  - **A 股：基本不可用 ❌ → 由 Seed 兜底**
- symbol 转换走现有 `TICKER:MARKET` 标准化，不可用市场直接返回 `None`，把降级交给 registry。

### 4.3 SeedKlineProvider（demo 关键兜底）
- 为 demo 持仓中 **AV 覆盖不到的标的（主要是 A 股、部分港股）** 提供 OHLC bars。
- bars 来源：用真实脱敏数据**一次性生成**并提交进 seed（与现有"AV 为主 / seed 兜底"demo 数据集同一套理念）。
- 至少提供 ≥120 根日 bars，保证能算出 MA20/RSI14/52w/分位。
- 非 Demo 环境不注册它；单元测试只能通过显式 fixture 参数注册。

## 五、降级与上报（前端零感知）

`factor_snapshot` 现有字段全部保留，回填规则：

| 字段 | 来源 |
|---|---|
| `kline_source` | 命中的 `provider.name`（broker/av/seed） |
| `price_source` | 同上（current_price 取 bars 最新收盘或现有现价逻辑，保持不变） |
| `kline_points` | `len(bars)` |
| `current_price/high_52w/low_52w/ma5/ma20/rsi14/macd*/ma_position/trend` | 由**单一** FactorComputer 从 bars 计算 |
| `is_realtime/delayed_minutes/latest_price_time` | 来自 KlineResult 元数据 |
| `degraded_fields/degraded_reason` | registry 返回的 degraded 列表组装；**全空时**保持现有 insufficient_data 行为 + 人工锚点价兜底入口不变 |

## 六、验收标准

在 **MacBook** 上即可全部验证：

1. **dev 正常起（broker 在）**：AAPL:US 出分批计划，`kline_source="broker"`，行为与改动前一致。
2. **dev + 模拟 broker 挂**（断网关/mock 返回空）：自动降级，`kline_source="av"`，`degraded_fields` 含 broker，计划正常出。
3. **`PUBLIC_DEMO_MODE=true` 起**：
   - AAPL:US → `kline_source="av"`，**不再报 insufficient_data** ✅
   - 某 A 股 demo 标的 → `kline_source="seed"`，计划正常出 ✅
4. **前端零感知**：前端代码 0 改动；`factor_snapshot` JSON 结构逐字段比对一致。
5. **全空兜底**：构造一个三源都无的标的，确认仍回退到现有"填锚点价"人工兜底，不崩。

## 七、风险与回滚

- **唯一回归风险**：决策报告/估值链路是否被误改。→ 约束 Claude Code 只动 Execution Plan 取 K线这一段，估值链路不碰；改前 `git` 建分支 + 备份。
- **回滚**：本版纯增量（接口+实现+注册），dev 默认顺序把 broker 放第一，最坏情况行为等价于改动前。出问题直接 revert 分支。

## 八、工程纪律（沿用既有约定）

- 探索先行：动手前先定位现有 broker K线调用点，再写。
- 一版一事：本版只解耦 K线源，不夹带其他改动。
- soft over hard：broker 代码保留，仅按环境不注册。
- 备份/分支先行，再做改动。
- 前端零感知为硬约束。

## 九、v3.15 Known Issue（本版不修）

- **trigger_evaluator 的 K线源同样依赖富途**：`evaluate_triggers` 用 `_fetch_kline_high_low_default`（富途 15 分 K线），`backfill_missed_triggers` 用 `_fetch_history_klines_default`（富途日 K）。demo 环境下同样会空。待 v3.15 按同样的 KlineProvider 模式解耦。

## 十、2026-07-26 实施收口

- `app.database.init_db()` 现显式注册 `ExecutionPlan` / `ExecutionTranche`，全新 SQLite 可建表。
- Provider 使用 `backend.utils.symbol`；Demo 且 `DEMO_ALLOW_MARKET_DATA=false` 时，AV 在任何网络调用前返回不可用。
- Seed 改读版本受控的 `demo_seed/demo_seed_kline_ohlcv.csv`。其日期、价格及 OHLCV 均固定，`source=seed`，不再由当前日期或持仓现价合成。
- fallback 会把失败源写入 `degraded_fields`、可读原因写入 `degraded_reason`，且把最终 `kline_source` 和 `delayed_minutes` 原样回填。
- 定向离线测试覆盖 Demo 网络短路、fallback 元数据、静态 fixture、注册表边界及新库建表；未连接真实行情、LLM、券商或交易接口。
