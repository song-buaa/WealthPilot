# M2.5 IBKR Paper 开市诊断探针结果

运行时间: 2026-06-08 21:52:34

## 连接

状态: OK
账户: ['DUQ629797']

## 场景 1: permId 回填延迟

- permId: 1008325248
- **回填耗时: 256 ms**
- 市价参考: 395.89
- 限价: 197.94
- 最终状态: Submitted

### 状态序列

| 时间 | 状态 | errorCode | message |
|------|------|-----------|---------|
| 2026-06-08 13:52:18.732048+00:00 | PendingSubmit | 0 |  |
| 2026-06-08 13:52:18.983586+00:00 | PreSubmitted | 0 |  |
| 2026-06-08 13:52:19.157705+00:00 | Submitted | 0 |  |

## 场景 2: 拒单场景

### 2a) 无效合约 ZZZZINVALID

- 最终状态: Cancelled
- is_Inactive: False
- 异常: 无

| errorCode | message | status |
|-----------|---------|--------|
| 200 | Error 200, reqId 7: No security definition has been found for the request | Cancelled |

### 2b) 数量异常（TSLA 1 亿股）

- 最终状态: Inactive
- is_Inactive: **True**
- 异常: 无
- 注意: trade.log 里 errorCode=0，但 IB error callback 收到 Error 201:
  `Order rejected - reason: We are unable to accept your order. Your Available Funds are insufficient to cover the change in the account's margin requirements...`

| errorCode | message | status |
|-----------|---------|--------|
| 201 | Order rejected - reason: Available Funds insufficient (margin) | Inactive |

### 2c) 价格异常（TSLA 限价 $0.01）

- 最终状态: **Submitted**（未拒！IB 接受了 $0.01 的限价买单）
- is_Inactive: False
- 异常: 无
- 结论: 极低限价不触发拒单，只是不会成交

## 汇总: 观测到的全部 errorCode

| errorCode | 含义 | 触发场景 | 最终状态 |
|-----------|------|---------|---------|
| 200 | No security definition has been found for the request | 无效合约 | Cancelled |
| 201 | Order rejected - Available Funds insufficient (margin) | 数量异常/保证金不足 | Inactive |
| 202 | Order Canceled (撤单确认) | 正常撤单 | Cancelled |

## 校准结论

### permId 回填
- 开市实测 **256ms**，远小于 2 秒上限
- `PERM_ID_WAIT_SECONDS = 2` 足够，甚至可以降到 1 秒

### 状态序列（正常单）
- PendingSubmit → PreSubmitted → Submitted（开市完整链路，休市只到 PreSubmitted）
- 撤单: Submitted → PendingCancel → Cancelled

### Inactive / REJECTED_ERROR_CODES
- **errorCode 200**: 合约无效 → 直接 Cancelled（不经 Inactive）
- **errorCode 201**: 保证金不足 → **Inactive**（这是 IBKR 的"软拒"模式，不用 Rejected）
- **errorCode 202**: 撤单确认（非拒单，正常流程）
- 极低限价（$0.01）: **不触发拒单**，正常 Submitted
- REJECTED_ERROR_CODES 应包含: `{200, 201}`

## 收尾确认

- 清理前 openTrades: 0
- 清理后 openTrades: 0
- ✅ 无遗留挂单
