# WealthPilot v3.10 PRD — 盈透证券 (IBKR) 下单接入

> 日期: 2026-06-05 | 版本: **v1.1**(纳入 GPT 对抗评审 6 项 + 4 验证用例)
> 角色分工: 本 PRD 由产品/架构定稿,交 Claude Code 实现
> 前置产出: `docs/v3.10/exploration_ibkr.md`(本地勘探报告,已确认下单层自 v3.4 冻结)
> 节奏: exploration(已完成)→ **PRD(本文档)** → 实现 → 脚本验证

---

## 0. 一句话目标

让 WealthPilot 的"决策→行动→执行→复盘"闭环重新具备**买入**能力:新增一个 IBKR `BrokerAdapter`,镜像现有老虎(tiger)下单适配器接入既有 `OrderManager`,先 paper 验证;并顺带修复一个会导致"假取消"的 broker 无关重大 bug。

**背景**:老虎/富途因监管,2026-06-12 后境内只能单向卖出、不能买入;同一个 IB 账户(前端现称"雪盈证券",实质即盈透)仍可正常买入,故下单通道迁往 IBKR 原生 API。

---

## 0.1 v1.0 → v1.1 变更(评审纳入)

| # | 变更 | 节 |
|---|------|----|
| 1 | cancel 成功语义收严:`adapter.cancel_order()=True` 只代表"撤单请求被受理",**不立即置 cancelled**,须 `get_order_status` 确认 `Cancelled/ApiCancelled` 才置终态;未确认保持 `broker_pending` + `cancel_requested` 标记,交 poller 收敛 | §3.1 |
| 2 | IBKR v3.10 **仅支持 `LIMIT`**;收到 `CONDITIONAL_LIMIT` 一律 `rejected`(刻意区别于老虎) | §1, §3.3 |
| 3 | `broker_order_id` 主存 `permId`,但 `raw_response` 须同存 `orderId/clientId/account/orderRef/conId`;撤单按 permId 反查 Trade 对象 | §3.6 |
| 4 | **新增幂等约束**:每笔 IBKR 订单强制 `orderRef = local_order_id`;超时/断连/unknown 禁止盲目重下,先按 orderRef 反查 | §3.6.1(新增) |
| 5 | 措辞修正:除 M0 的 cancel 共享逻辑外,IBKR 接入不改 `OrderManager` 的 place/sync 主链路 | §1, §3.8 |
| 6 | 显示统一"盈透证券",但 audit/raw/debug 须保留 channel(持仓=snowball / 下单=ibkr) | §3.9 |
| + | 验证计划新增 4 个 paper 用例 | §5 |

---

## 1. 范围

### 本期做(In)
1. **M0 — cancel 真撤单修复(broker 无关,优先)**:`OrderManager.cancel_order()` 当前只改本地状态、不调 `adapter.cancel_order()`,导致"界面显示已取消、券商端订单仍存活后续成交"的资金风险。本期修掉(语义见 §3.1),并补老虎回归。
2. **M1+ — IBKR `BrokerAdapter` 实现**:新建 `ibkr.py`,实现 `BrokerAdapter` 8 个抽象方法,逐条镜像老虎四闸门 + 异常透传契约 + 九态状态映射。
3. **factory 集成**:`factory.py` 新增 `ibkr` 分支;**除 M0 外不改 `OrderManager` 主链路**。
4. **IB Gateway 常驻连接**:ib_async 连本机 IB Gateway;本期手动起 Gateway 即可。
5. **仅 LIMIT 单**:只跑普通限价单主链路。**`CONDITIONAL_LIMIT` 在 IBKR v3.10 明确不支持,直接 rejected**(条件单留 v3.11)。
6. **先 paper 验证**:全程模拟盘,实盘仅最小额冒烟。
7. **前端改名**:用户可见标签"雪盈证券"→"盈透证券";内部代码标识 `snowball` 不变。

### 本期不做(Out — 防 scope creep,Claude Code 不得擅自扩)
- ❌ **IBKR 持仓拉取**。已确认下单账户与 `snowball` 同步的账户是同一个 IB 账户,持仓由现有 snowball 通道覆盖,下单后复盘自动闭环。**不新建 `broker_sync/ibkr/`**。
- ❌ **`CONDITIONAL_LIMIT` / 条件单**(触发条件、价格条件、盘中盘后回报复杂度高,留 v3.11)。
- ❌ MARKET 市价单(现有 `OrderRequest` 契约本就不支持,不新增)。
- ❌ IB Gateway 自动登录 / 开机自启 / 会话保活(IBC)。本期手动起,跑通再说。
- ❌ 期权 / 期货 / bracket / trailing 等复杂订单。
- ❌ market 白名单扩到全球。本期 `{US, HK}`。
- ❌ 把内部 `snowball` 标识重命名为 `ibkr`(伤筋动骨,零收益)。
- ❌ 在 `OrderManager` 引入自动重下/自动重试循环。
- ❌ 实盘大额下单测试。

---

## 2. 架构决策汇总(已拍板,实现时按此执行)

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 连接模式 | **IB Gateway**(无头常驻),非 TWS GUI |
| 2 | Python 库 | **`ib_async`**(非 `ib_insync`/`ibapi`);装进 `wealthpilot` conda 环境(Homebrew 3.11),**禁止**装到系统 `python3`(3.7.1) |
| 3 | paper 安全闸门 | 镜像老虎闸门 1:`ENABLE_IBKR_LIVE_TRADING` 环境开关 + paper 账户断言(IBKR paper `DU` 前缀 / live `U` 前缀) |
| 4 | 凭证/配置模型 | **连接参数走 config.py/.env(`IBKR_*`),不走 keyring,不碰 `REQUIRED_CREDENTIAL_FIELDS`** |
| 5 | market 白名单 | `SUPPORTED_MARKETS = {"US", "HK"}` |
| 6 | 订单类型 | **仅 `LIMIT`**;`CONDITIONAL_LIMIT` → rejected |
| 7 | 持仓拉取 | 本期不做(同账户,snowball 已覆盖) |
| 8 | cancel 语义 | 真撤单(M0),且**撤单成功 ≠ 已撤销**,须券商确认才置终态(§3.1) |
| 9 | 前端改名 | 仅显示层;`snowball`/`ibkr` 内部标识保留,审计/调试可区分通道 |

---

## 3. 详细设计

### 3.1 M0 — cancel 真撤单修复(broker 无关,先做)

**现状**(`order_manager.py:335-347`):`cancel_order()` 只 `order.status = CANCELLED` + 落审计,**不调** `adapter.cancel_order()`。

**目标行为**(改 `OrderManager.cancel_order()`,各 adapter 的 `cancel_order` 接口不变):

| 前置条件 | 处理 |
|---------|------|
| 订单**无** `broker_order_id`(从未提交) | 维持原行为:仅本地置 `CANCELLED` + 审计 |
| 订单有 `broker_order_id` 且可撤(`submitted_to_broker`/`broker_pending`/`partially_filled`) | **先调 `adapter.cancel_order(broker_order_id)`**,按结果分流(见下) |
| 订单已终态 | 拒绝撤单,返回明确错误,不改状态 |

**调 adapter 后的分流(核心:撤单成功 ≠ 已撤销)**:
- 返回 `True`(撤单请求**已被券商受理**,非"已撤销")→ **不立即置 cancelled**。立刻调 `get_order_status()`:
  - 券商明确返回 `Cancelled`/`ApiCancelled` → 本地置 `CANCELLED` + 审计。
  - 仍 `Submitted`/`PendingCancel`/`PreSubmitted`(→ 九态 `broker_pending`)→ **本地保持 `broker_pending`**,在 `raw_response` 与 `audit_log` 标 `cancel_requested=true`,交 poller 继续轮询直到终态。
  - 已 `filled`/部分成交(撤单与成交擦肩)→ 回填真实态 + 审计差异。
- 返回 `False`(券商称已终态)→ **不盲目本地置 CANCELLED**;调 `get_order_status()` 拉真实状态回填,审计"撤单时已终态"。
- 抛 `ConnectionError`/`TimeoutError`(无法确认)→ 本地置 `UNKNOWN` + 审计,提示用户"撤单结果未知,请稍后核对";**绝不**置 `CANCELLED`。

**不新增状态**:用现有九态 `broker_pending` + `cancel_requested` 标记兜住"撤单中"。
**联动硬约束**:确认 `order_poller` 的 `POLLABLE_STATUSES` 包含 `broker_pending`,使带 `cancel_requested` 的单能被轮询收敛到终态(若当前不含,需纳入修复)。

**回归要求(硬性)**:
- 现有老虎 paper 测试与 OrderManager 既有测试**全绿不退化**。
- 新增 OrderManager 单测覆盖:撤单受理→券商确认 cancelled / 撤单受理但仍 broker_pending(保持不终态)/ 返回 False→reconcile / 抛网络异常→unknown / 撤单与成交擦肩→回填 filled。
- 用 mock adapter 覆盖分支;另做老虎 paper 冒烟:挂不可成交单→撤单→券商端 open orders 确实消失后本地才 cancelled。

**独立 commit**,先于 IBKR adapter 落地。

---

### 3.2 M1+ — `IBKRBrokerAdapter`(镜像老虎)

新建 `backend/services/action/brokers/ibkr.py`,`class IBKRBrokerAdapter(BrokerAdapter)`,实现全部 8 抽象方法,逐方法镜像 `tiger.py`:

| 方法 | IBKR 实现要点 |
|------|--------------|
| `broker_name` | `"ibkr"` |
| `authenticate` | 已连 Gateway 且账户在 `ib.managedAccounts()` |
| `place_order` | 四闸门(§3.3)→ IB `Stock` 合约 + `LimitOrder`(强制 `orderRef=local_order_id`,§3.6.1)→ `ib.placeOrder` → `submitted_to_broker`;异常按 §3.4 |
| `cancel_order` | 按 `broker_order_id` 反查 Trade/Order 对象(§3.6)再 `ib.cancelOrder`;已终态/查不到对象返回 `False`,受理成功 `True`(语义供 §3.1 的 OrderManager 调用) |
| `get_order_status` | 查回报 → 映射九态(§3.5);网络异常透传,其余 → `unknown` |
| `list_open_orders` | `ib.openTrades()` → 未终态,`local_order_id` 留空(由 OrderManager 关联) |
| `get_positions` | `ib.positions()` → dict(本期下单用,不接持仓同步) |
| `get_account_info` | `ib.accountSummary()` → 现金/购买力/净值 + 脱敏 + `is_paper` |
| `shutdown` | 断 ib 连接、清理事件循环/线程(§3.6) |

---

### 3.3 四道安全闸门(IBKR 版)

| # | 闸门 | IBKR 实现 |
|---|------|----------|
| 1 | **paper-only 断言** | `if not ENABLE_IBKR_LIVE_TRADING: assert account_id.startswith("DU")`,否则拒绝 |
| 2 | **market 白名单** | `market not in {"US","HK"}` → `rejected`(A 股走国金 QMT) |
| 3 | **order_type** | **仅 `LIMIT`**;`CONDITIONAL_LIMIT` → `rejected`,提示"IBKR v3.10 暂不支持条件限价单,请使用普通限价单";其余 → rejected |
| 4 | **盘后单关闭** | IB `Order.outsideRth = False` |

---

### 3.4 异常透传契约(镜像老虎,不得自创)

- `ConnectionError`/`TimeoutError`(含 Gateway 断连)→ **不 catch,透传**给 OrderManager(映射 `unknown`)。
- IBKR 业务错误(购买力不足/合约无效/无权限)→ catch → `rejected`,原始错误进 `raw_response`。
- 订单查不到(not_found)→ 镜像老虎:指数退避重试,耗尽抛继承 `ConnectionError` 的孤儿订单异常。
- `raw_response` 对齐老虎 `_build_raw`(broker、account_type、脱敏账户、action 等)+ §3.6 的多 ID 字段。

---

### 3.5 状态映射:ib_async 回报 → 九态(提议表,**实现时必须核实**)

九态:`created / submitted_to_broker / broker_pending / partially_filled / filled / cancelled / rejected / expired / unknown`。

| IB 状态(`trade.orderStatus.status`)| → 九态 | 备注 |
|------|--------|------|
| `PendingSubmit` / `PreSubmitted` | `submitted_to_broker` | 已发往 IB,未完全确认 |
| `Submitted` | `broker_pending` | 在市场挂单中 |
| 部分成交 | `partially_filled` | 由 `filled`/`remaining` 判定 |
| `Filled` | `filled` | 终态 |
| `Cancelled` / `ApiCancelled` | `cancelled` | 终态 |
| `PendingCancel` | `broker_pending` | **不提前判 cancelled**,等 IB 确认 |
| `Inactive` | **核实点** | 默认保守 `unknown`,看原因再定 |
| 拒单(购买力/合约/权限)| `rejected` | 由 IB 错误回报判定 |

> **硬约束给 Claude Code**:上表 IB 状态字符串与字段(`orderStatus.status`、`filled`、`remaining`、`avgFillPrice`、`permId` 等)以**本地已安装 ib_async 实际接口为准**,不得照本 PRD 字符串硬编码。实现前先 inspect 已安装包 / 查官方文档核实,核实结论写进实现说明。**保守原则:任何拿不准的状态一律 `unknown`,绝不误判终态。**

---

### 3.6 连接、订单 ID、symbol、事件循环

- **连接**:`ib.connect('127.0.0.1', port, clientId=N)`。paper Gateway `4002` / live `4001`。`127.0.0.1` 本地回环不走代理(`NO_PROXY` 已覆盖);Gateway→IBKR 那段走 Clash Verge。
- **`broker_order_id` 主存 `permId`**(账户级永久 ID,跨会话稳定),**但 `raw_response` 必须同存**:`permId / orderId / clientId / account / orderRef / conId`(及合约基本信息)。`orderId` 是 per-session/per-clientId,不可作为唯一持久键。**核实点**:`permId` 获取时机(下单后可能异步回填,需等回报)。
- **撤单对象反查**:`cancel_order` 不能只凭 `permId` 直接撤。优先用 `permId` 在 `openTrades`/`openOrders` 反查 `Trade`/`Order` 对象;找不到再用 `orderId/clientId` 辅助匹配;仍找不到 → 进 `get_order_status`/reconcile,**不得直接判 cancelled**。
- **symbol 反标准化**:US 直接 ticker + `exchange="SMART"` + `currency="USD"`(LI/NVDA/TSLA 直通)。HK + `exchange="SEHK"` + `currency="HKD"`,**港股代码格式需核实**(IBKR 一般用去前导零数字代码,与老虎 zfill(5) 不同)。复用 `backend/utils/symbol.py` 解析,反标准化 IBKR 单独写。
- **★ 事件循环风险(必须设计)**:ib_async 基于 asyncio,与 FastAPI 主循环嵌套易冲突(ib_insync 被弃痛点之一)。**实现要求**:IBKR 连接与调用隔离在**独立线程 + 独立事件循环**(或 ib_async 同步包装),adapter 对外仍是同步阻塞接口(与老虎一致,OrderManager 无感知)。方案写进实现说明并验证不阻塞 FastAPI。

---

### 3.6.1 幂等约束(防重复买入,**资金安全最高优先**)

真实风险:place_order 时 IBKR 已收到单,但网络超时,系统误以为失败;若重试则重复下单、重复买入。

**硬约束**:
1. 每笔 IBKR 订单**强制** `order.orderRef = local_order_id`。
2. `place_order` 发生**超时 / 断连 / unknown** 时,**禁止盲目重下**。
3. adapter 提供按 orderRef 反查能力(在 `openTrades`/`openOrders`/`executions` 中查 `orderRef == local_order_id`):
   - 命中 → 回填 `broker_order_id`(permId)与真实状态,**不重下**。
   - 确认不存在 → 才允许重新提交。
4. 边界:此为 **adapter 能力 + 系统规则**;**不**在 `OrderManager` 引入自动重下/重试循环(当前也无)。任何"重新提交"路径必须先经 orderRef 反查。

---

### 3.7 凭证与配置

`core/config.py` 新增,遵循现有 `{BROKER}_{字段}` 规范:

```
IBKR_HOST                 # 默认 127.0.0.1
IBKR_PORT                 # paper 4002 / live 4001
IBKR_CLIENT_ID            # ib_async clientId
IBKR_ACCOUNT              # DU... (paper) / U... (live)
IBKR_READ_ONLY_MODE       # 对齐其他 broker 只读开关
ENABLE_IBKR_LIVE_TRADING  # 默认 false,闸门 1 用
```

`.env.example` 同步加注释。**不动** `credentials.py` 的 `REQUIRED_CREDENTIAL_FIELDS`。

---

### 3.8 factory 集成点

`factory.py` 在 tiger 分支(L41-45)后、`raise UnsupportedBrokerError`(L47)前,加 `if broker_name == "ibkr"` 分支,从 config 读连接参数构造 `IBKRBrokerAdapter`。

**边界(措辞精确)**:除 M0 修复 `cancel_order` 的共享逻辑外,**IBKR 接入本身不得修改 `OrderManager` 的 `place_order` / `sync_order_status` 主链路**,只通过 factory + `IBKRBrokerAdapter` 接入。`OrderRecord.broker_name` 取值新增 `"ibkr"`。

---

### 3.9 前端改名(仅显示层)+ 通道可区分

- 用户可见 `display_name` 统一为"盈透证券"。
- **内部标识 `snowball` 与 `ibkr` 一字不改**;显示映射层把两者都映射到"盈透证券",保证持仓页(走 snowball)与订单页(走 ibkr)券商名一致。
- **但 audit_log / raw_response / debug 必须保留 channel**:持仓通道 `snowball`、下单通道 `ibkr`。用户看一个账户,系统内部能区分两条通道,便于排查。
- Claude Code 先**全量检索**前端"雪盈"/"雪盈证券"出现位置(不许猜),列清单再改。**独立 commit**。

---

## 4. 实现里程碑(镜像老虎 v3.4 的 M 节奏)

| 里程碑 | 内容 | 验收 |
|--------|------|------|
| **M0** | cancel 真撤单修复(含 §3.1 最终一致语义 + poller 联动)+ OrderManager 单测 + 老虎 paper 回归 | 老虎既有测试不退化;§3.1 各分支单测绿;老虎挂单→撤单券商端确撤后本地才 cancelled |
| **M1** | `IBKRBrokerAdapter` 骨架 + 四闸门 + 凭证/config + 连接(线程隔离)+ orderRef + 基础状态映射 | 连 paper Gateway;闸门拦截类单测全绿;orderRef 写入验证 |
| **M2** | 异常兜底完整(拒单分类 / not_found 重试 / 网络透传 / 状态映射核实落表 / orderRef 反查) | 失败路径单测覆盖;状态映射与 orderRef 反查核实结论入实现说明 |
| **M3** | factory 接入 + paper 端到端:下单→查状态→撤单全链路 | paper 实跑 §5 全部用例,审计完整 |
| **M4** | 前端改名(snowball/ibkr→"盈透证券",保留 channel) | 持仓页与订单页券商名一致;audit 仍可区分通道 |
| **M5** | 脚本验证报告 + 实盘最小额冒烟(时点你定) | 报告含失败路径/异常/审计证据,非仅 happy path |

---

## 5. 验证计划(对齐 §九 红线;paper 优先)

**通用**:全程 paper(端口 4002),实盘仅最小额冒烟且需显式 `ENABLE_IBKR_LIVE_TRADING`;每笔下单/查状态/撤单/异常落 `audit_log`;老虎/富途/snowball/guojin 现有测试与同步链路不受影响。

**必验失败路径(非 happy path)**:购买力不足拒单、合约无效拒单、白名单外 market 拒单、非 LIMIT 拒单、Gateway 断连→unknown。

**新增 4 个具体 paper 用例**:
1. **不可成交限价单撤单**:买价故意挂极低不成交 → 点取消 → 券商端 open orders 确实消失,本地才 `cancelled`。
2. **撤单已受理但未确认**:`cancel_order=True` 但 `get_order_status` 仍 `submitted/broker_pending` → 本地**不得** `cancelled`,保持 `broker_pending` + `cancel_requested`,poller 继续收敛。
3. **下单超时幂等恢复**:`place_order` 超时但 IBKR 已收单 → 系统按 `orderRef=local_order_id` 找回订单回填,而**非**重新下一笔。
4. **CONDITIONAL_LIMIT 拒绝**:IBKR v3.10 收到条件限价单 → 明确 `rejected`,不允许半猜半写条件单。

---

## 6. 实现前必须核实的 IBKR API 细节(给 Claude Code 的硬约束)

下列项**禁止照本 PRD 假设直接编码**,实现前以本地已安装 `ib_async` 实际接口 / 官方文档核实,结论写进实现说明:
1. `placeOrder` / `LimitOrder` / `Stock` 合约的实际签名与字段。
2. `trade.orderStatus.status` 实际状态字符串全集 → §3.5 映射定稿。
3. `permId` 获取时机与可靠性(下单后异步回填?)。
4. 港股 symbol 在 IBKR 的确切格式(前导零 / exchange 代码)。
5. `cancelOrder` 对已终态订单的实际行为(报错?静默?)→ 决定 `cancel_order` 返回 True/False 的判定。
6. `orderRef` 的写入方式与在 `openTrades`/`openOrders`/`executions` 的可查性(支撑 §3.6.1 幂等反查)。
7. asyncio 事件循环与 FastAPI 共存的具体隔离方案(线程 vs 同步包装)。

---

## 7. 前置环境(属 paper 验证阶段,不挡 M0/M1 编码)

- 在 IBKR 账户后台 / Gateway 配置中**开通 API 访问权限**(官方 APP 能登录 ≠ API 已开)。
- 安装并手动启动 **IB Gateway**(本机),登录 paper 账户,确认能连上 IBKR(Gateway→IBKR 走代理)。
- 确认 paper 账户号(`DU` 开头)填入 `IBKR_ACCOUNT`,`IBKR_PORT=4002`。

---

## 8. Claude Code 协作约束(务必遵循)

- 提示词中文;先核实再写,**靠猜的接口一律先核**(尤其 §6 七项)。
- **不得扩 scope**(§1 Out 清单是硬边界);不做开放式选项,按本 PRD 决策执行。
- 每个逻辑单元**独立 commit**,git 干净;M0 先行且独立。
- 测试断言对齐真实语义,**禁止关键词匹配式断言**;失败路径必须真实覆盖。
- 报告**不许 overconfident**(不写"全部完成/无重大偏差"类收尾),如实列做了什么、没做什么、待确认什么。
