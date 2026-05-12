# WealthPilot v3.4 M1 实施护栏

> **文档性质**: M1 开工前的产品/工程边界文档,非 PRD
> **创建日期**: 2026-05-12
> **作者**: Songbin
> **状态**: M1 启动依据
> **依赖**: v3.2 PRD v0.7 + M0_tiger_探索笔记.md + 此护栏
> 
> **目的**: 在 M1 启动前固定产品范围和技术边界,防止实施过程中范围蔓延和错误假设固化。
> **范围**: 仅覆盖 M1(TigerBrokerAdapter 实现)。M2-M7 不在本文档范围内。

---

## 1. M1 产品范围(明确做什么,不做什么)

### 1.1 M1 做什么

| 能力                    | 范围                                               |
| --------------------- | ------------------------------------------------ |
| 实现 TigerBrokerAdapter | 继承 v3.2 BrokerAdapter ABC                        |
| 支持市场                  | 美股(US) + 港股(HK)                                  |
| 支持订单类型                | LIMIT(普通限价单)                                     |
| 支持交易方向                | BUY + SELL 双向                                    |
| 运行模式                  | 仅模拟盘(paper account: 21995161433588262)           |
| 错误兜底                  | ApiException + EXPIRED 二义性 + 网络异常 + not_found 重试 |
| 审计 payload            | 完整 raw_response 落库                               |
| 沙箱端到端验证               | 美股开盘时段验证买卖成交链路 + 部分成交(可选)                        |

### 1.2 M1 不做什么

- ❌ 不动 `backend/services/action/` 下任何业务代码(OrderManager / RiskEngine / 状态机)
- ❌ 不实现凭证管理(M2 的范围,M1 暂用文件路径,但接口要为 keyring 预留扩展位)
- ❌ 不动前端 UI
- ❌ 不接实盘账号 4472659
- ❌ 不实现 A 股交易(由国金 QMT 在 v3.5+ 承担)
- ❌ 不实现 MARKET 单(v3.2 PRD §5.7 已明确不计划支持)
- ❌ 不实现 STOP_LOSS / STOP_PROFIT(留 v3.5+)
- ❌ 不实现盘前盘后交易(outside_rth 默认 False)
- ❌ 不做多账户管理

---

## 2. 多券商市场分工(战略级)

WealthPilot 的多券商策略已确定:

| 券商         | 市场覆盖        | v3.4 状态     |
| ---------- | ----------- | ----------- |
| Tiger 老虎证券 | 美股 + 港股     | M1 实现(本里程碑) |
| 富途证券       | 美股 + 港股(候选) | v3.5+ 可选    |
| 国金证券 QMT   | A 股(权限已开通)  | v3.5+ 实现    |

**重要**: A 股交易由国金 QMT 承担,**不是因为 Tiger 不支持而被迫禁用**,而是**多券商分工的产品架构决策**。UI / 文案应当反映这一点:

- ❌ 错误文案: "A 股暂不支持"(暗示能力缺失)
- ✅ 正确文案: "A 股交易将通过国金 QMT 接入(v3.5+)"

---

## 3. 核心技术规则(M1 实施时必须遵守)

### 3.1 broker_order_id 规则

- 必须使用 `order.id`(int 类型,Tiger 服务端订单 ID)
- **严禁**使用 `order.order_id`(SDK 本地自增序号,不是真实订单 ID)
- 存入数据库时统一转 str

### 3.2 sandbox_debug 规则

- 必须 `sandbox_debug=False`(True 在 tigeropen 3.5.8 已废弃)
- 模拟盘通过 account ID 自动识别(SDK 设 `config.is_paper=True`)
- 实施 paper-only 安全闸门(见 3.5)

### 3.3 EXPIRED 二义性映射规则(关键)

Tiger EXPIRED 状态承担双语义,M1 必须按以下规则映射:

```python
def map_expired(tiger_order) -> str:
    """Tiger EXPIRED → v3.2 状态二义性映射"""
    reason = (tiger_order.reason or "").lower()

    # 已知拒单关键词 → rejected
    rejected_keywords = ["购买力", "资金不足", "合约", "权限", "禁止", "不正确"]
    if any(kw in reason for kw in rejected_keywords):
        return "rejected"

    # 已知过期关键词 → expired
    expired_keywords = ["过期", "超时", "expired", "timeout", "day order"]
    if any(kw in reason for kw in expired_keywords):
        return "expired"

    # 未知 reason / reason 为空 → unknown_terminal (异常终态)
    # 强制进入人工复核流程,审计日志保留原 reason
    return "unknown_terminal"
```

**严禁**默认映射成 `expired` 或 `rejected`。未知 reason 必须落到 `unknown_terminal`,UI 强提示用户人工核实,审计日志保留原始 reason 便于关键词字典扩充。

### 3.4 outside_rth 规则

- M1 实现中必须**显式设置** `outside_rth=False`
- 严禁继承 SDK 默认行为(SDK 默认可能允许盘前盘后)
- 未来如开放盘前盘后,必须 UI 单独提示风险

### 3.5 paper-only 安全闸门

代码层面强制保护,防止误用实盘:

```python
# 配置文件 / 环境变量
ENABLE_TIGER_LIVE_TRADING = False  # M1-M4 强制 False
TIGER_PAPER_ACCOUNT = "21995161433588262"
TIGER_LIVE_ACCOUNT = "4472659"

# Adapter 初始化时硬校验
class TigerBrokerAdapter(BrokerAdapter):
    def __init__(self, account_id: str, ...):
        if not ENABLE_TIGER_LIVE_TRADING:
            assert account_id == TIGER_PAPER_ACCOUNT, \
                f"实盘交易未开启,拒绝使用账号 {account_id}"
```

M6 实盘小额验证时才允许 `ENABLE_TIGER_LIVE_TRADING=True`,并配合 `MAX_LIVE_TEST_AMOUNT_CNY` 兜底。

### 3.6 market 白名单规则

```python
SUPPORTED_MARKETS = {"US", "HK"}

# Adapter place_order 入口校验
if contract.market not in SUPPORTED_MARKETS:
    raise UnsupportedMarketError(
        f"v3.4 Tiger Adapter 不支持市场 {contract.market},"
        f"A 股请使用国金 QMT(v3.5+)"
    )
```

A 股不应该让请求触达 Tiger API,**Adapter 层直接拦截**。审计日志记录 `order_blocked_unsupported_market` 事件。

### 3.6.1 Symbol 解析与市场推断(M1 权宜方案)

v3.2 的 `OrderRequest.symbol` 格式不统一(LLM 输出纯代码如 `MSFT`,base.py 注释写 `US.LI`)。M1 Adapter 采用启发式兼容解析:

```python
def _parse_symbol(self, symbol: str) -> tuple[str, str]:
    """解析 symbol -> (market, pure_symbol)

    M1 兼容两种格式:
      - 带前缀: 'US.SPY' / 'HK.00700' / 'CN.600519'
      - 纯代码: 'SPY' / '00700' / '600519'

    启发式规则(M1 权宜):
      - 4-5 位纯数字 -> HK(港股代码)
      - 6 位纯数字 -> CN(A 股代码,触发 market 白名单拒绝)
      - 其他字母代码 -> US(默认美股)
    """
    if '.' in symbol:
        market, pure = symbol.split('.', 1)
        return market.upper(), pure

    if symbol.isdigit():
        if len(symbol) in (4, 5):
            return "HK", symbol
        if len(symbol) == 6:
            return "CN", symbol  # 触发 market 白名单拒绝

    return "US", symbol  # 默认美股(字母代码)
```

**升级路径**: 此启发式在 Symbol 标准化专项完成后将被替换为纯 split 逻辑。

**Symbol 标准化专项立项说明**:

WealthPilot v2.0 阶段曾定义过 `<ticker>:<market>` 格式(如 `LI:US` / `0700:HK`),
但未在全系统落地。当前混乱状况:
- v2 投研模块:可能部分使用 `LI:US` 格式
- v3.2 投资行动模块:LLM 输出纯代码,base.py 注释又写 `US.LI`(点分隔,与 v2 冒号分隔不一致)
- v3.4 Tiger Adapter:启发式兼容

Symbol 标准化作为独立专项已立项(暂定 v3.5 或 v4.0 范围,不在 v3.4 内)。
该专项完成后:
- WealthPilot 全系统统一 symbol 格式
- TigerBrokerAdapter._parse_symbol 简化为纯 split 逻辑
- 删除本节启发式代码

### 3.7 币种处理规则

Tiger 返回金额单位:

- 美股: USD
- 港股: HKD

WealthPilot 内部统一存 CNY,且 v3.2 已修过一次币种 bug。M1 必须验证:

- `avg_fill_price` 保留原币种存储(USD / HKD)
- 风控金额计算时按 `fx_rate_to_cny` 换算
- 审计 payload 同时存原币种和 CNY 等值

### 3.8 not_found 重试规则

```python
def get_order_with_retry(broker_order_id, max_retries=2):
    for attempt in range(max_retries + 1):
        try:
            return trade_client.get_order(id=int(broker_order_id))
        except ApiException as e:
            if "not_found" in str(e) and attempt < max_retries:
                time.sleep(2 ** attempt)  # 指数退避
                continue
            raise OrphanOrderError(broker_order_id) from e
```

连续失败转 orphan 处理流程,**严禁**简单映射成 `unknown` 后无限轮询。

---

## 4. 审计 payload 字段标准

审计 payload 分两层产出,分层原则:**Adapter 只产出券商交互事实(原币种),业务层负责汇率换算和业务语义补充**。

### 4.1 Adapter 层负责的字段(TigerBrokerAdapter 写入 raw_response)

```json
{
  "broker": "tiger",
  "account_type": "paper",
  "account_id_masked": "***88262",
  "market": "US",
  "symbol": "SPY",
  "action": "BUY",
  "order_type": "LIMIT",
  "quantity": 1,
  "limit_price": 370.0,
  "currency": "USD",
  "outside_rth": false,
  "broker_order_id": "43207668465158144",
  "tiger_status": "HELD",
  "mapped_status": "broker_pending",
  "reason": null,
  "raw_error_code": null,
  "raw_error_message": null
}
```

完整 `raw_response` 单独存储,便于排查问题。

### 4.2 业务层负责的字段(OrderManager 在写 audit_log 时补充)

```json
{
  "fx_rate_to_cny": 7.2,
  "amount_cny_equivalent": 2664.0
}
```

这两个字段由 OrderManager 根据 `raw_response.currency` 从 `app.fx_service` 查汇率后计算填入,**Adapter 不负责汇率换算**。

**分层原则**: Adapter 是纯券商交互层,只了解券商 API 的请求/响应事实;汇率、风控阈值、业务语义等属于业务层职责。这样 Adapter 可被不同业务场景复用,且汇率源变更不影响 Adapter 代码。

---

## 5. M1 能力验收矩阵

M1 完成的判定标准(必须全部 ✅ 才能进入 M2):

| 能力                 | M1 完成标准                                |
| ------------------ | -------------------------------------- |
| 美股 LIMIT BUY 挂单    | ✅ broker_pending 状态正确                  |
| 美股 LIMIT BUY 成交    | ✅ filled 状态正确(需开盘时段验证)                 |
| 美股 LIMIT SELL 挂单   | ✅ broker_pending 状态正确                  |
| 美股 LIMIT SELL 成交   | ✅ filled 状态正确(需先有持仓)                   |
| 港股 LIMIT BUY 挂单    | ✅ broker_pending 状态正确                  |
| 港股 LIMIT BUY 成交    | ✅(开盘时段验证,允许跳过 SELL)                    |
| 撤单 - 未成交单          | ✅ cancelled 状态正确                       |
| 撤单 - 已成交单          | ✅ 不报错,业务提示"已终态"                        |
| 不存在 symbol         | ✅ rejected 状态,审计记录 ApiException        |
| 购买力不足              | ✅ rejected 状态(走 EXPIRED → rejected 映射) |
| 错误 broker_order_id | ✅ 重试 2 次后转 orphan                      |
| A 股下单尝试            | ✅ Adapter 层拦截,不触达 Tiger API            |
| MARKET 单尝试         | ✅ Adapter 层拒绝                          |
| 实盘账号尝试             | ✅ paper-only 闸门拦截                      |
| 币种链路               | ✅ USD/HKD → CNY 换算正确                   |
| 审计 payload 完整      | ✅ 上述字段全部落库                             |

**M1 不阻塞但 M3 阻塞的能力**(可在 M1 末或 M3 初补):

| 能力                    | 验证窗口                  |
| --------------------- | --------------------- |
| 部分成交 partially_filled | M1 末美股开盘时段挂大量接近市价单    |
| 限流策略                  | M3 OrderManager 轮询时观察 |

---

## 6. M1 推进顺序

```
M1.0 闸门对齐(0.5 天) — Claude Code 读完此护栏 + M0 笔记,反馈对齐确认
M1.1 TigerBrokerAdapter 基础实现(2-3 天)
M1.2 错误兜底和状态映射(2-3 天)
M1.3 沙箱端到端验证(1-2 个交易时段晚上)
```

每个子里程碑独立提示词,严守边界,不顺手扩展到下一阶段。

---

## 7. 风险登记(M1 期间持续关注)

| 风险                                  | 缓解                                 |
| ----------------------------------- | ---------------------------------- |
| Claude Code 在 M1 阶段顺手改 OrderManager | 提示词显式禁止,审 PR 时核对 diff 范围           |
| 误用实盘账号                              | paper-only 闸门 + 提示词强提醒             |
| A 股请求漏出到 Tiger API                  | Adapter 层 market 白名单兜底             |
| EXPIRED 映射误判                        | unknown_terminal 兜底,审计日志保留原 reason |
| 币种换算 bug                            | M1 验收必检项,fx_rate_to_cny 链路单测       |
| 部分成交未验证就进 M3                        | M3 启动前置条件:M1.3 部分成交链路有数据           |

---

**护栏文档结束。M1 启动以此为依据。**
