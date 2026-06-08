# IBKR Paper 诊断探针结果

> 运行时间: 2026-06-08 10:49:17
> 账户: DUQ629797 | 端口: 7497 | clientId: 11

---

## A. 连接层
- 状态: **OK**
- managedAccounts: `['DUQ629797']`

## A. 账户摘要
- AvailableFunds: `1000000.00`
- BuyingPower: `6666666.67`
- GrossPositionValue: `0.00`
- NetLiquidation: `1000000.00`
- TotalCashValue: `1000000.00`

## 下单准备
- 现价: `391.0`
- 限价 (×0.6): `234.6`

## B. permId 回填
- permId: `0`
- 回填耗时: **10137.3 ms**
- order.permId: `0`
- orderStatus.permId: `0`
- 结论: ⚠️ 超过 2 秒，需调大 PERM_ID_WAIT_SECONDS

## C. 状态序列
| 时间 | 状态 | 耗时(s) |
|------|------|---------|
| 10:49:12.357 | `PendingSubmit` | 10.24 |

## D. trade.log（下单后）
| 时间 | status | errorCode | message |
|------|--------|-----------|---------|
| 2026-06-08 02:49:02.118371+00:00 | `PendingSubmit` | 0 |  |

## E. orderRef
- 写入: `PROBE-1780886937`
- 读回: `PROBE-1780886937`
- 匹配: ✅

## F. 全部 ID
- orderId: `6`
- permId: `0`
- clientId: `11`
- account: ``
- conId: `76792991`
- orderRef: `PROBE-1780886937`

## G. orderRef 反查
- trades() 反查: ✅ 找到
- 反查到的 orderId: `6`

## H. 撤单状态序列
| 时间 | 状态 | 耗时(ms) |
|------|------|----------|
| 10:49:17.432 | `ApiCancelled` | 106 |

## H. 撤单后 trade.log
| 时间 | status | errorCode | message |
|------|--------|-----------|---------|
| 2026-06-08 02:49:02.118371+00:00 | `PendingSubmit` | 0 |  |
| 2026-06-08 02:49:17.327001+00:00 | `PendingCancel` | 0 |  |
| 2026-06-08 02:49:17.343310+00:00 | `ApiCancelled` | 0 |  |

## 最终状态: `ApiCancelled`
