# M0 -- Tiger SDK 沙箱探索笔记

## 0. 调研环境
- 日期: 2026-05-12
- Python 环境: conda env `wealthpilot`
- Python 版本: 3.11.13
- Python 可执行文件路径: /Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python
- tigeropen SDK 版本: 3.5.8 (`tigeropen.__VERSION__`，注意大写)
- 操作系统: macOS (Darwin 25.3.0)
- 代理: 未使用 (直连可达 Tiger 服务器)
- 私钥格式: PKCS#1 (`RSA PRIVATE KEY`)
- 私钥路径: backend/secrets/tiger_private_key.pem
- 测试账号: 模拟盘 21995161433588262 (空仓, 初始资金 $1,000,000, 购买力 $4,000,000)
- 牌照: TBNZ
- SPY 当日市价: $739.28

## 1. 关键发现(放最前面，让 M1 一眼看到)

1. **TBNZ 模拟盘 TradeClient 能下美股 LIMIT 单吗？** -- YES，place_order 返回 int 类型的 broker_order_id，订单状态立即可查
2. **broker_order_id 在 Tiger response 的哪个字段？** -- `order.id`（int 类型，如 `43207668465158144`）。注意 `order.order_id` 是本地自增序号（1,2,3...），**不是** broker_order_id
3. **一笔挂单，3 秒内能查到状态吗？** -- YES，`get_order(id=broker_order_id)` 立即返回。状态字段是 `order.status`，类型是 `OrderStatus` 枚举。挂单后立即查到 `HELD`（= Submitted，已挂单等待成交）
4. **cancel_order 是同步立即返回 cancelled 还是异步？** -- **同步**。cancel_order 返回后立即 get_order，status 已经是 `CANCELLED`
5. **TBNZ 模拟盘能下港股、A 股 LIMIT 单吗？** -- 港股 YES（00700 挂单成功，status=HELD）；A 股 place_order 成功但立即变为 EXPIRED（reason 未显示，可能是模拟盘限制或合约配置问题）

**最大意外**:
- `sandbox_debug=True` 在 tigeropen 3.5.8 中已废弃（`raise NotImplementedError`），模拟盘通过 account ID 自动识别（`config.is_paper = True`）
- 超出购买力的订单 **不会在 place_order 时被拒**，而是先 accept 再异步变为 `EXPIRED`（reason="您的可用资金或者可用购买力不足"），这不是 REJECTED 是 EXPIRED
- `order.order_id` vs `order.id` 的区别：`order_id` 是 SDK 本地自增序号，`id` 才是 Tiger 服务端的订单 ID。**M1 必须用 `order.id`**
- 不存在的 symbol 会在 `place_order` 时直接抛 `ApiException`，不会返回订单对象

## 2. 问题 #0: TBNZ 模拟盘下单权限
**结论**: TBNZ 模拟盘 TradeClient 完全可以下单（美股、港股均成功）

**证据**:
```
# Step 1: 账户查询
get_managed_accounts() 返回两个账户:
  4472659 (STANDARD, 实盘)
  21995161433588262 (PAPER, 模拟盘)

# Step 2: 美股买单成功
place_order() 返回: 43207668465158144 (int)
get_order() status: OrderStatus.HELD (已挂单)

# Step 1: 资产
cash: 1,000,000.0 USD
buying_power: 4,000,000.0 (4x margin)
net_liquidation: 1,000,000.0
```

## 3. 问题 #1: 代理配置
**结论**: 直连可用，无需代理

**实现方式**:
```python
# 无需任何代理配置，tigeropen SDK 直连 Tiger 服务器即可
# 测试环境：中国大陆 macOS，无 Clash Verge
config = TigerOpenClientConfig(sandbox_debug=False)
# SDK 会自动根据 license (TBNZ) 选择服务器域名
```

## 4. 问题 #2: 最小买单调用形态

**最小可用代码**:
```python
from tigeropen.tiger_open_config import TigerOpenClientConfig
from tigeropen.common.util.signature_utils import read_private_key
from tigeropen.common.consts import Language
from tigeropen.trade.trade_client import TradeClient
from tigeropen.trade.domain.contract import Contract
from tigeropen.common.util.order_utils import limit_order

# 1. 初始化
config = TigerOpenClientConfig(sandbox_debug=False)
config.private_key = read_private_key("backend/secrets/tiger_private_key.pem")
config.tiger_id = "20159046"
config.account = "21995161433588262"  # 模拟盘，SDK 自动识别 is_paper=True
config.language = Language.zh_CN
trade_client = TradeClient(config)

# 2. 构造 contract (注意: ContractFactory 已废弃，直接用 Contract)
contract = Contract(symbol="SPY", currency="USD", sec_type="STK", market="US")

# 3. 构造并下单
order = limit_order(
    account="21995161433588262",
    contract=contract,
    action="BUY",
    limit_price=370.0,
    quantity=1,
)
broker_order_id = trade_client.place_order(order)  # 返回 int

# 4. broker_order_id 也可以从 order.id 获取
assert order.id == broker_order_id
```

**关键字段说明**:
- broker_order_id 字段: **`order.id`**（int 类型，如 43207668465158144）
- `order.order_id`: 本地自增序号（1,2,3...），**不要用这个**
- `place_order()` 返回值: `int`，等于 `order.id`
- 必填参数: account, contract, action, quantity, limit_price
- `time_in_force` 默认 `'DAY'`，limit_order 工具函数默认设置
- `outside_rth`: 查单时发现被自动设为 `True`（盘前盘后可交易）

## 5. 问题 #3: 订单状态枚举与映射表

**Tiger SDK OrderStatus 枚举** (tigeropen 3.5.8):

| 枚举名 | 枚举值 (value) | 含义 |
|--------|---------------|------|
| PENDING_NEW | PendingNew | 待提交 |
| NEW | Initial | 初始 / 新建 |
| HELD | Submitted | 已提交 / 挂单中 |
| PARTIALLY_FILLED | PartiallyFilled | 部分成交 |
| FILLED | Filled | 全部成交 |
| CANCELLED | Cancelled | 已撤单 |
| PENDING_CANCEL | PendingCancel | 撤单中 |
| REJECTED | Inactive | 被拒 |
| EXPIRED | Invalid | 过期/无效 |

**映射到 v3.2 PRD 9 个状态**:
| Tiger 状态 | v3.2 状态 | 备注 |
|-----------|----------|------|
| PENDING_NEW | submitted_to_broker | SDK 本地构造订单后、服务端确认前 |
| NEW | submitted_to_broker | 服务端已收到但尚未挂单 |
| HELD | broker_pending | **核心状态** -- 已挂单等待成交（实测 place_order 后立即变为 HELD） |
| PARTIALLY_FILLED | partially_filled | 部分成交（本次未触发，待补充验证） |
| FILLED | filled | 全部成交 |
| CANCELLED | cancelled | 已撤单 |
| PENDING_CANCEL | cancelled | 撤单请求已提交，实测撤单是同步的所以几乎不会停留在这个状态 |
| REJECTED | rejected | 被拒 |
| EXPIRED | rejected | **关键发现**: 超出购买力的单不是 REJECTED 而是 EXPIRED，reason 字段有拒单原因。M1 应将 EXPIRED 映射为 rejected（检查 reason 字段） |

**特殊情况**:
- 部分成交: 本次未触发（远价 LIMIT 单不会成交），待开盘时段补充验证
- `filled` 字段: 等价于 `filled_quantity`（整数）
- `avg_fill_price`: 成交均价（float）
- `remaining`: 剩余未成交数量
- EXPIRED 既可能是"超时过期"也可能是"资金不足被拒"，需检查 `reason` 字段区分

## 6. 问题 #4: 撤单行为

**同步 / 异步**: **同步**

**证据**:
```
# Step 4 撤单流程:
撤单前: status = OrderStatus.HELD
cancel_order(id=43207670790111232) 返回: 43207670790111232 (int, 返回订单 ID)
撤单后立即查: status = OrderStatus.CANCELLED
撤单后 3 秒查: status = OrderStatus.CANCELLED (无变化)
```

**cancel_order 返回值**: `int`，等于被撤订单的 broker_order_id

**撤单后的 status 值**: `OrderStatus.CANCELLED`，`can_modify` 变为 `False`

**撤单失败的错误码**: 本次未触发（所有撤单都成功了）。撤已终态单预计会抛 ApiException

**撤已成交单的行为**: 本次未验证（需要开盘时段有成交单才能测试）

## 7. 问题 #5: 限流策略

**已知 QPS 限制**: 本次测试中未触发限流（总共约 20+ 次 API 调用，间隔 2-3 秒）

**触发限流后的错误码**: 未触发

**建议轮询频率**: 根据 Tiger 官方文档，API 调用频率限制为每分钟 120 次。建议 M1 状态轮询间隔 **3-5 秒**，单次轮询批量查询所有 open orders 而非逐单查询

## 8. 问题 #6: 错误场景与错误码

| 场景 | Tiger 错误码 / 异常类型 | 错误消息样例 | 建议处理 |
|------|------------------------|------------|---------|
| 超出购买力 | place_order 成功，2 秒后 status=EXPIRED | reason="您的可用资金或者可用购买力不足" | **不是同步拒单！** M1 需在状态轮询时检查 EXPIRED + reason 字段，映射为 rejected |
| 不存在 symbol | ApiException code=1200 | "standard account response error(bad_request:合约不正确)" | place_order 时直接抛异常，M1 应 catch ApiException 映射为 rejected |
| 错误 broker_order_id (get_order) | ApiException code=1200 | "standard account response error(not_found:订单不存在)" | 抛异常，M1 应 catch 映射为 unknown 或 ValueError |
| 撤不存在的单 | 未单独测试，预计与上同 | 预计 "not_found:订单不存在" | catch ApiException |

**关键发现**: Tiger 的"拒单"不走 REJECTED 状态，而是走 EXPIRED + reason。M1 的状态映射逻辑必须处理这个边界。

## 9. 问题 #7: 多市场差异

**港股**:
- 下单: YES，TBNZ 模拟盘可以下港股 LIMIT 单 (00700 腾讯)
- 下单后状态: HELD (已挂单)
- 撤单: 成功
- contract 构造: `Contract(symbol="00700", currency="HKD", sec_type="STK", market="HK")`
- 最低手数: 100 股 (1 手)
- time_in_force: DAY (默认)

**A 股**:
- 下单: place_order 成功返回 broker_order_id，但查单时 status 立即变为 **EXPIRED**
- reason 字段未显示具体原因（可能是模拟盘不支持 A 股交易，或合约配置问题）
- contract 构造: `Contract(symbol="600519", currency="CNH", sec_type="STK", market="CN")`
- **结论**: TBNZ 模拟盘对 A 股支持存疑，M1 阶段 A 股优先级最低

**TBNZ 牌照限制总结**:
- 美股行情 (QuoteClient): 受限 (`permission denied`)
- 美股交易 (TradeClient): 正常
- 港股交易: 正常
- A 股交易: 受限 (place_order 成功但立即 EXPIRED)

## 10. 问题 #8: 沙箱测试机制

- 模拟盘是否会自动成交 LIMIT 单？远离市价的 LIMIT 单不会成交（$370 vs 市价 $739）。接近市价的 LIMIT 单行为待验证（需要开盘时段测试）
- 成交时机: 本次测试在非交易时段（北京时间 08:50，美股已收盘），挂单可以提交和查询，但不会成交
- 是否支持强制成交？未验证
- 是否能模拟部分成交？未验证（对 v3.4 测试 partially_filled 链路很关键，建议在美股开盘时段挂一笔接近市价的大单验证）

## 11. 问题 #10: 私钥加载

**关键代码**:
```python
# PKCS#1 在 tigeropen 3.5.8 里的加载方式
from tigeropen.common.util.signature_utils import read_private_key
from tigeropen.tiger_open_config import TigerOpenClientConfig
from tigeropen.common.consts import Language

config = TigerOpenClientConfig(sandbox_debug=False)  # 必须 False，True 已废弃
config.private_key = read_private_key("backend/secrets/tiger_private_key.pem")
config.tiger_id = "20159046"
config.account = "21995161433588262"  # 模拟盘 ID，SDK 自动设 is_paper=True
config.language = Language.zh_CN
# config.is_paper 会被自动设为 True
```

**sandbox_debug 参数说明**:
- `sandbox_debug=True`: **已废弃**，tigeropen 3.5.8 会 `raise NotImplementedError`
- `sandbox_debug=False`: 正常模式，模拟盘通过 account ID 自动识别
- SDK 内部 `AccountUtil.is_paper_account(account)` 检测 account 字符串长度判断是否为模拟盘

## 12. M1 启动前提对照表

- [x] 问题 #0 已回答 -- 下单权限确认，美股+港股均可
- [x] 问题 #1 已回答 -- 直连可用，无需代理
- [x] 问题 #2-#4 已回答 -- 基础调用形态、状态枚举、撤单行为全部验证
- [x] 问题 #5-#6 已回答 -- 限流未触发但有建议值，错误码已记录
- [x] 至少跑通过一笔模拟盘买入->撤单全链路 -- Step 4 完成 (HELD -> cancel -> CANCELLED)
- [ ] 至少跑通过一笔模拟盘买入->成交全链路 -- **未完成**，需要在交易时段挂接近市价的单（非阻塞 M1 启动，可在 M1 开发中验证）

## 13. 给 M1 的实施建议

**TigerBrokerAdapter 应该这样写**:
- `__init__`: 接收 tiger_id, account, private_key_path，构造 TigerOpenClientConfig + TradeClient
- **sandbox_debug 必须设为 False**，不要用 True
- 用 `config.is_paper` 判断是否模拟盘，用于日志和安全校验

**contract 构造方式**:
```python
# ContractFactory 已废弃，直接用 Contract
from tigeropen.trade.domain.contract import Contract

# 美股
contract = Contract(symbol="SPY", currency="USD", sec_type="STK", market="US")
# 港股
contract = Contract(symbol="00700", currency="HKD", sec_type="STK", market="HK")
```

**place_order + broker_order_id 提取**:
```python
from tigeropen.common.util.order_utils import limit_order

order = limit_order(account=account, contract=contract, action="BUY",
                    limit_price=370.0, quantity=1)
result = trade_client.place_order(order)
# result 是 int, 等于 order.id
broker_order_id = str(order.id)  # 转成 str 存入 OrderStatusUpdate
```

**状态轮询机制应该**:
- 轮询间隔: 3-5 秒
- 用 `get_order(id=broker_order_id)` 查单个订单
- 或用 `get_open_orders(account=account)` 批量查未终态订单
- 关注字段: `order.status` (OrderStatus 枚举), `order.filled`, `order.avg_fill_price`, `order.remaining`, `order.reason`

**状态映射实现**:
```python
from tigeropen.trade.domain.order import OrderStatus

TIGER_TO_V32_STATUS = {
    OrderStatus.PENDING_NEW: "submitted_to_broker",
    OrderStatus.NEW: "submitted_to_broker",
    OrderStatus.HELD: "broker_pending",
    OrderStatus.PARTIALLY_FILLED: "partially_filled",
    OrderStatus.FILLED: "filled",
    OrderStatus.CANCELLED: "cancelled",
    OrderStatus.PENDING_CANCEL: "cancelled",
    OrderStatus.REJECTED: "rejected",
    OrderStatus.EXPIRED: "rejected",  # 超额单走 EXPIRED 不走 REJECTED
}
```

**错误兜底应该**:
- `place_order` 时 catch `tigeropen.common.exceptions.ApiException`
- code=1200 + "合约不正确": 映射为 rejected
- code=1200 + "not_found": 订单不存在，映射为 unknown
- 网络异常: catch `ConnectionError` / `TimeoutError`，映射为 unknown
- EXPIRED 状态: 检查 `order.reason` 字段，如果包含"购买力"/"资金不足"等关键词，映射为 rejected 并将 reason 存入 raw_response

**撤单实现**:
```python
# cancel_order 是同步的，返回 int (broker_order_id)
result = trade_client.cancel_order(id=int(broker_order_id))
# 立即 get_order 验证状态
order = trade_client.get_order(id=int(broker_order_id))
assert order.status == OrderStatus.CANCELLED
```
