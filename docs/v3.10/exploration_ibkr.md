# WealthPilot v3.10 IBKR 接入勘探报告

> 日期: 2026-06-05 | 执行者: Claude Code | 性质: 只读勘探，未改任何代码

---

## 1. 勘探范围与方法

### 查了的路径

| 路径 | 读取方式 | 完整/部分 |
|------|---------|----------|
| `backend/services/action/brokers/base.py` | 全文读取 | 完整 |
| `backend/services/action/brokers/tiger.py` | 全文读取 | 完整 |
| `backend/services/action/brokers/factory.py` | 全文读取 | 完整 |
| `backend/services/action/brokers/credentials.py` | 全文读取 | 完整 |
| `backend/services/action/brokers/mock.py` | 确认存在(ls) | 未读内容 |
| `backend/services/action/order_manager.py` | 全文读取 | 完整 |
| `backend/services/action/state_machine.py` | 全文读取 | 完整 |
| `backend/services/action/models.py` | 全文读取 | 完整 |
| `backend/services/action/order_poller.py` | 前40行 | 部分(确认 POLLABLE_STATUSES) |
| `backend/services/broker_sync/snowball/sync_service.py` | 全文读取 | 完整 |
| `backend/services/broker_sync/snowball/adapter.py` | 全文读取 | 完整 |
| `backend/services/broker_sync/tiger/sync_service.py` | 全文读取(上轮) | 完整 |
| `backend/core/config.py` | 全文读取 | 完整 |
| `.env.example` | 全文读取(上轮) | 完整 |
| `requirements.txt` | 全文读取 | 完整 |
| `CHANGELOG.md` | 前80行 | 部分(v3.6.1~v3.7.0) |
| `snbpy` 已安装包 | Python inspect | place_order/cancel_order 签名 |
| `git log` | action/ + broker_sync/ 变更历史 | 2026年全部 |

### 没查/查不到的

- `backend/services/action/brokers/mock.py`: 只确认存在，未读实现细节
- CHANGELOG.md v3.8.x 条目: 文件前80行止于 v3.6.1，**v3.8 系列没有 CHANGELOG 条目**（v3.7.0 之后无新版本记录）
- 无 IBKR 相关代码: `grep ib_async|ib_insync|ibapi|IBKR|ibkr` 在 `*.py/*.txt/*.toml/*.cfg` 中**零命中**

---

## 2. 逐条核实结果

### A. 下单抽象层: `backend/services/action/brokers/base.py`

#### BrokerAdapter ABC 全部抽象方法

| 方法 | 签名 | 行号 |
|------|------|------|
| `broker_name` | `@property → str` | 47-51 |
| `authenticate` | `(credentials: dict) → bool` | 53-56 |
| `place_order` | `(request: OrderRequest) → OrderStatusUpdate` | 58-70 |
| `cancel_order` | `(broker_order_id: str) → bool` | 72-80 |
| `get_order_status` | `(broker_order_id: str) → OrderStatusUpdate` | 82-89 |
| `list_open_orders` | `() → list[OrderStatusUpdate]` | 91-93 |
| `get_positions` | `() → list[dict]` | 95-98 |
| `get_account_info` | `() → dict` | 100-103 |
| `shutdown` | `() → None` (非抽象,默认空) | 106-112 |

**共 8 个抽象方法 + 1 个可选 shutdown。**

#### OrderRequest 字段 (`base.py:20-29`)

```python
symbol: str                         # LI:US / 0700:HK / 600519:SH
side: str                           # BUY / SELL
quantity: int
order_type: str                     # LIMIT / CONDITIONAL_LIMIT
limit_price: Optional[Decimal] = None
trigger_price: Optional[Decimal] = None  # CONDITIONAL_LIMIT 必填
time_in_force: str = "GTC"          # DAY / GTC
local_order_id: str = ""
```

**确认: `order_type` 注释仅列 `LIMIT / CONDITIONAL_LIMIT`，无 MARKET。** 但该字段类型是 `str`（非 Enum/Literal），运行时不校验合法值。实际合法性由各 adapter 的闸门校验（如 tiger.py:167 的 `if request.order_type.upper() not in ("LIMIT", "CONDITIONAL_LIMIT")`）。

#### OrderStatusUpdate 字段 (`base.py:33-41`)

```python
broker_order_id: Optional[str]
local_order_id: str
status: str                         # OrderStatus 9 个值之一
filled_quantity: int = 0
avg_filled_price: Optional[Decimal] = None
timestamp: int = 0                  # Unix 毫秒时间戳
raw_response: dict = field(default_factory=dict)
```

**与预期基线一致。**

---

### B. 镜像对象: `backend/services/action/brokers/tiger.py`

#### 4 道安全闸门

| # | 闸门 | 行号 | 实际代码 |
|---|------|------|---------|
| 1 | **paper-only 断言** | 107-113 | `if not ENABLE_TIGER_LIVE_TRADING: assert account_id == TIGER_PAPER_ACCOUNT` |
| 2 | **market 白名单** | 174-184 | `if market not in SUPPORTED_MARKETS` (US/HK, 行47) → rejected |
| 3 | **order_type 白名单** | 166-172 | `if request.order_type.upper() not in ("LIMIT", "CONDITIONAL_LIMIT")` → rejected |
| 4 | **outside_rth=False** | 200-201 | `order.outside_rth = False` |

**与预期基线完全一致。**

#### 异常透传契约

| 异常类型 | 处理方式 | 行号 |
|---------|---------|------|
| `ConnectionError / TimeoutError` | **透传**（`raise`） | place_order:205-206, get_order_status:255-256 |
| `ApiException` | **catch → rejected** (place_order) 或 **catch → unknown** (get_order_status) | place_order:207-217, get_order_status:257-264 |
| `OrphanOrderError` | 继承 `ConnectionError`，透传 | 73-80, get_order_with_retry:443-446 |

**与预期基线一致。**

#### 凭证加载方式

- 使用 `CredentialProvider` 抽象 (`credentials.py:26`)
- 具体实现: `KeyringCredentialProvider` (macOS Keychain, `credentials.py:62`)
- 测试用: `InMemoryCredentialProvider` (`credentials.py:107`)
- **用完即丢**: `del creds, private_key_pem, private_key_raw` (`tiger.py:134`)
- keyring service 命名: `wealthpilot.broker.{broker_key}` (`credentials.py:73-74`)
- 必要字段: `tiger_id / account_id / private_key_pem` (`credentials.py:19`)

**与预期基线一致。** 注意: `REQUIRED_CREDENTIAL_FIELDS` 硬编码了 Tiger 的字段名。IBKR 凭证字段不同（预计需要 host/port/client_id 等），需要决定是扩展此常量还是让 IBKR adapter 自行校验。

#### 券商状态映射表

`tiger.py:51-60`:
```python
TIGER_TO_V32_STATUS = {
    TigerOrderStatus.PENDING_NEW: "submitted_to_broker",
    TigerOrderStatus.NEW: "submitted_to_broker",
    TigerOrderStatus.HELD: "broker_pending",
    TigerOrderStatus.PARTIALLY_FILLED: "partially_filled",
    TigerOrderStatus.FILLED: "filled",
    TigerOrderStatus.CANCELLED: "cancelled",
    TigerOrderStatus.PENDING_CANCEL: "cancelled",
    TigerOrderStatus.REJECTED: "rejected",
}
```

EXPIRED 走 `_map_expired()` 二义性分类 (`tiger.py:396-414`)：关键词匹配决定映射为 rejected / expired / unknown。

---

### C. 集成点: factory.py + order_manager.py

#### factory 当前注册的 broker

`factory.py:37-47`:
```python
if broker_name == "mock" or mode == "mock":    # L37
    → get_mock_adapter()
if broker_name == "tiger":                      # L41
    → TigerBrokerAdapter(...)
raise UnsupportedBrokerError(...)               # L47
```

**只有 mock 和 tiger 两个分支。** 新增 IBKR 应在 `L41-45` 之后、`L47` 之前加 `if broker_name == "ibkr"` 分支。

#### OrderManager 怎么拿到 adapter

`order_manager.py:56-58`:
```python
class OrderManager:
    def __init__(self, session: Session, broker_adapter: Optional[BrokerAdapter] = None):
        self.broker_adapter = broker_adapter
```

**DI 注入，确认。** adapter 通过构造函数传入，OrderManager 不 import 任何具体 adapter。

#### OrderManager 调用 adapter 的位置

| 方法 | 调用 | 行号 |
|------|------|------|
| `place_order` | `self.broker_adapter.place_order(request)` | 438 |
| `sync_order_status` | `self.broker_adapter.get_order_status(broker_order_id)` | 489 |
| `cancel_order` | 仅更新本地状态，**不调 adapter.cancel_order** | 335-347 |

**关键差异**: `OrderManager.cancel_order()` (L335-347) 只做本地状态流转（`order.status = OrderStatus.CANCELLED`），**不调用 `self.broker_adapter.cancel_order()`**。这意味着取消订单只改本地记录，不通知券商。这是当前设计，与 IBKR 接入无关，但值得记录。

#### OrderManager 是否对具体 broker 无感知

**确认: 完全无感知。** `order_manager.py` 不 import 任何 `tiger`/`mock` 模块，只 import `base.py` 的 `BrokerAdapter / OrderRequest / OrderStatusUpdate`。新增 IBKR **只需改 factory.py + 新建 adapter 文件**，不动 OrderManager。

---

### D. 状态机: `backend/services/action/state_machine.py` + `models.py`

#### OrderStatus 全部合法值

`state_machine.py:101-115`:
```python
class OrderStatus:
    CREATED = "created"                    # L102
    SUBMITTED_TO_BROKER = "submitted_to_broker"  # L103
    BROKER_PENDING = "broker_pending"      # L104
    PARTIALLY_FILLED = "partially_filled"  # L105
    FILLED = "filled"                      # L106
    CANCELLED = "cancelled"               # L107
    REJECTED = "rejected"                 # L108
    EXPIRED = "expired"                   # L109
    UNKNOWN = "unknown"                   # L110
    TERMINAL = {FILLED, CANCELLED, REJECTED, EXPIRED}  # L118
```

**确认九态，与预期基线完全一致。**

#### 状态流转校验

`validate_order_transition()` at `state_machine.py:157-168`。流转表 `_ORDER_TRANSITIONS` at `state_machine.py:121-154`。

关键设计: `UNKNOWN` 可转到任何非 unknown 的非终态+终态 (L146-153)，用于网络异常恢复。

#### 订单相关 ORM 表

`models.py` 定义 5 张表:

| 表 | 类 | 行号 | 说明 |
|---|---|------|------|
| `action_drafts` | `ActionDraft` | 47-71 | 行动清单草稿 |
| `allocation_intents` | `AllocationIntent` | 78-107 | 资产配置调整意图 |
| `symbol_strategies` | `SymbolStrategy` | 114-164 | 标的策略 |
| `order_records` | `OrderRecord` | 171-220 | 券商订单 |
| `audit_logs` | `AuditLog` | 227-255 | 审计日志(append-only) |

`OrderRecord.broker_name` 字段 (`models.py:190-191`): `Column(String(20), default="mock")`，注释列 `mock / tiger / futu / gjzq`。IBKR 接入时此字段值为 `"ibkr"`。

---

### E. 持仓拉取范式: `backend/services/broker_sync/`

#### 统一范式（以 snowball 为代表）

`snowball/sync_service.py:85-137`:

```
1. repo.create_run(broker, account_id, sync_source="api", triggered_by)  # L91-97
2. positions = self.fetch_positions()                                     # L102
3. repo.persist_positions(run_id=run.id, positions=positions)             # L103
4. snapshots = db_session.query(PositionSnapshot).filter_by(run_id=...)  # L105
5. upsert_service.upsert_from_snapshots(snapshots)                      # L107
6. 检查 upsert_report["errors"]                                          # L109
```

重试分流: 网络错误 `(ConnectionError, TimeoutError, OSError)` 重试 (L113)；数据错误 `(ValidationError, KeyError, AttributeError, ValueError)` 立即失败 (L126)。

**与 tiger 完全一致的三步范式。** 但注意: snowball 的 `fetch_positions()` (L65-83) 没有空结果守卫（返回空列表不抛异常）。tiger 也没有。只有 guojin（v3.9 新增）有空结果守卫。

#### snowball 只读包装器

`snowball/sync_service.py:13-29`:
```python
WRITE_METHOD_KEYWORDS = ("place_order", "cancel_order")

class ReadOnlySnowballClient:
    def __getattr__(self, name: str):
        if settings.snowball_read_only_mode:
            for kw in WRITE_METHOD_KEYWORDS:
                if kw in name.lower():
                    raise RuntimeError(f"SNOWBALL_READ_ONLY_MODE 已开启,拒绝调用: {name}")
        return getattr(self._inner, name)
```

**确认存在只读包装器。** 与 tiger 的 `ReadOnlyTradeClient` 结构镜像（tiger/sync_service.py:25-38 拦截的是不同的关键词列表）。

#### snbpy SDK place_order / cancel_order 签名

通过 Python inspect 确认（实际已安装的 snbpy 包）:

```python
# place_order 签名:
SnbHttpClient.place_order(
    self, order_id: str, security_type: SecurityType, symbol: str,
    exchange: str, side: OrderSide, currency: Currency,
    quantity: int, price: float = 0,
    order_type: OrderType = LIMIT, tif: TimeInForce = DAY,
    force_only_rth: bool = True, stop_price: float = 0,
    parent: str = None, order_id_type: OrderIdType = CLIENT,
    trading_hours: TradingHours = None
)

# cancel_order 签名:
SnbHttpClient.cancel_order(
    self, order_id: str, origin_order_id: str,
    order_id_type: OrderIdType = CLIENT
)
```

**确认 snbpy 有 place_order 和 cancel_order 方法。** 但当前 WealthPilot 未接入 snowball 下单（只有持仓同步），且 `ReadOnlySnowballClient` 会拦截这些方法。

---

### F. 依赖与环境

#### requirements.txt IBKR 相关

`requirements.txt` 全文（34行）**无** `ib_async` / `ib_insync` / `ibapi`。grep 确认零命中。

**与预期一致: 都没有。**

#### Python 版本

```
$ python3.11 --version
Python 3.11.15
```

**满足 ib_async ≥ 3.10 要求。** 但注意系统默认 `python3` 是 3.7.1（`/usr/local/bin/python3`），实际项目用的是 Homebrew 的 3.11。

#### 配置项命名规范

`core/config.py` 现有命名模式:
```
TIGER_{字段}:    TIGER_ID / TIGER_ACCOUNT / TIGER_PRIVATE_KEY_PATH / TIGER_ENV / TIGER_READ_ONLY_MODE
FUTU_{字段}:     FUTU_ACCOUNT / FUTU_OPEND_HOST / FUTU_OPEND_PORT / FUTU_READ_ONLY_MODE
SNOWBALL_{字段}: SNOWBALL_ACCOUNT / SNOWBALL_SECRET_KEY / SNOWBALL_ENV / SNOWBALL_READ_ONLY_MODE
GUOJIN_{字段}:   GUOJIN_GATEWAY_URL / GUOJIN_GATEWAY_SECRET (.env, 不在 config.py)
```

IBKR 应遵循: `IBKR_{字段}`，如 `IBKR_HOST / IBKR_PORT / IBKR_CLIENT_ID / IBKR_ACCOUNT / IBKR_READ_ONLY_MODE`。

注意: Tiger 下单走 `CredentialProvider`/keyring 管理凭证，不在 `config.py` 里。IBKR (TWS/Gateway) 走 TCP 连接而非 API Key，凭证模型不同——不需要私钥，但需要 host:port + client_id。

---

### G. v3.7~v3.8 变更核对

#### CHANGELOG.md 覆盖范围

CHANGELOG.md 最新条目是 `[3.7.0] - 2026-05-19`。**v3.8 系列没有 CHANGELOG 条目**。

#### git log 核查: action/brokers/ 和 broker_sync/ 变更

自 2026-01-01 以来动过 `backend/services/action/` 的 commit:
```
089bb46 release(v3.4.0): broker integration + symbol standardization
b48ac25 feat(symbol): unified TICKER:MARKET format across full system
055cb78 feat(v3.4): tiger broker integration M0-M5
894d9f5 feat(action): 投资行动模块完整实现（M1-M6）
```

自 2026-01-01 以来动过 `backend/services/broker_sync/` 的 commit:
```
6b72930 feat(guojin): 主动拉网关 sync_service + 港股通汇总行
db57873 fix(broker-sync): remove stale positions after sync
59cf318 fix(broker-sync): handle ticker with dots correctly + dedupe
089bb46 release(v3.4.0): broker integration + symbol standardization
b48ac25 feat(symbol): unified TICKER:MARKET format across full system
13b2142 feat(broker-sync): add Snowball Securities position sync
3ebe6a5 feat(broker-sync): add Futu position sync adapter & service
413cdef feat(broker-sync): pull tiger funds & resolve asset_class
28147bc feat(broker-sync): persist Tiger positions to time-series tables
48ad20b feat(broker-sync): implement Tiger position adapter & sync service
```

**v3.7~v3.8 期间未动过 action/brokers/ 或 state_machine.py 或 order_manager.py。** 下单抽象层自 v3.4 以来冻结。broker_sync/ 的改动仅限 guojin 新增和 stale 清理修复，不影响下单层。

#### 与预期基线的差异

**无差异。** action/ 下单层自 v3.4 release 后未被改动。

---

## 3. 与预期基线的差异清单

| # | 预期基线 | 实际 | 差异说明 |
|---|---------|------|---------|
| 1 | OrderRequest.order_type 合法取值: LIMIT / CONDITIONAL_LIMIT | **一致** | 但注意 `order_type` 是 `str` 而非 Enum，合法性由各 adapter 闸门校验，base.py 不强制 |
| 2 | 4 道安全闸门 | **一致** | paper-only 断言、market 白名单(US/HK)、order_type 白名单、outside_rth=False |
| 3 | ConnectionError/TimeoutError 透传 | **一致** | |
| 4 | ApiException catch → rejected | **一致** | place_order → rejected; get_order_status → unknown |
| 5 | CredentialProvider/keyring 凭证管理 | **一致** | 但 `REQUIRED_CREDENTIAL_FIELDS` 硬编码 Tiger 字段名 |
| 6 | OrderManager 对 broker 无感知 | **一致** | 纯 DI，只 import base.py |
| 7 | 九态状态机 | **一致** | created/submitted_to_broker/broker_pending/partially_filled/filled/cancelled/rejected/expired/unknown |
| 8 | requirements 无 IBKR 包 | **一致** | 零命中 |
| 9 | snbpy 有 place_order/cancel_order | **确认** | 预期基线未提及此点，但实际确认存在 |
| 10 | OrderManager.cancel_order 是否调 adapter | **差异**: 只改本地状态，不调 `adapter.cancel_order()` | 这是现有设计，非 bug。IBKR 接入后如需券商端取消，需评估是否改 OrderManager |

**总结: 与预期基线高度一致，无重大偏差。** 唯一值得注意的是 #10 (cancel_order 不通知券商) 和 #5 (凭证字段硬编码)。

---

## 4. 进 PRD 前仍需人工确认的开放问题

### 4.1 IBKR 连接模式决策

IBKR 有两种 API 接入方式:
- **TWS (Trader Workstation)**: 桌面客户端，需要 GUI 运行
- **IB Gateway**: 无头模式，更适合服务器部署

需人工确认: 用哪种？TWS 意味着需要 Mac 上跑 TWS GUI（类似国金 QMT 的 VM 方案）；IB Gateway 可以无头运行但需要单独安装。

### 4.2 ib_async vs ib_insync

- `ib_insync` 是成熟的同步/异步包装库（作者 Ewald de Wit），但 PyPI 上已标 deprecated
- `ib_async` 是 `ib_insync` 的继任者（同一作者），要求 Python ≥ 3.10
- 原生 `ibapi`（IB 官方 Python SDK）接口更底层

需人工确认: 用 `ib_async`（推荐，更现代）还是 `ibapi`（官方但低层）？

### 4.3 IBKR 模拟盘策略

Tiger 用模拟盘账号白名单做安全闸门 (`TIGER_PAPER_ACCOUNT`)。IBKR 也有 Paper Trading 账号（通常以 `DU` 开头），需要确认:
- IBKR 模拟盘的账号命名规范
- 是否需要类似 `ENABLE_IBKR_LIVE_TRADING` 环境变量

### 4.4 凭证管理适配

`CredentialProvider` 的 `REQUIRED_CREDENTIAL_FIELDS = {"tiger_id", "account_id", "private_key_pem"}` 是 Tiger 专属字段 (`credentials.py:19`)。IBKR 不用 API Key/私钥，而是 TCP 连接 (host:port + client_id)。

两个方向:
- A: 让 IBKR adapter 绕过 CredentialProvider，直接从 config.py 读 host/port/client_id
- B: 扩展 CredentialProvider 支持不同券商的字段集

需人工判断哪个更合理。

### 4.5 cancel_order 券商端通知

当前 `OrderManager.cancel_order()` (`order_manager.py:335-347`) 只改本地状态，不调 `adapter.cancel_order()`。如果 IBKR 需要真正取消券商端订单（而不是只标记本地状态），需要决定:
- 改 OrderManager 让它调 adapter.cancel_order()（影响 Tiger 现有行为）
- 还是在 IBKR adapter 层面做特殊处理

### 4.6 market 白名单扩展

Tiger adapter 的 `SUPPORTED_MARKETS = {"US", "HK"}` (`tiger.py:47`)。IBKR 支持全球市场。IBKR adapter 的 market 白名单需要单独定义，或者是否直接不设白名单（由 IBKR 自身校验）？

### 4.7 持仓同步是否纳入本期

IBKR 通过 `ib_async` 也能拉持仓。本期是否只做下单（BrokerAdapter），还是同时做持仓同步（broker_sync/ 下新增 ibkr/）？需明确范围。

### 4.8 【待确认】mock.py 内容

`backend/services/action/brokers/mock.py` 只确认存在，未读取实现细节。如果 IBKR 开发中需要参考 mock adapter 的模拟逻辑（如模拟成交延迟），需要后续读取。
