# M3 IBKR Paper 端到端验证结果

运行时间: 2026-06-08T22:43:40.805175

## 连接

- status: OK
- account: DUQ629797

## 下单

- status: submitted_to_broker
- broker_order_id: 1008325251
- broker_name: ibkr
- local_order_id: 03580333-17d0-4e6d-b1f7-d6fe313ff3fc
- elapsed_ms: 1334
- is_perm_id: True

## 同步状态

- status: broker_pending
- filled_quantity: 0

## orderRef 反查

- found: True
- broker_order_id: 1008325251
- status: broker_pending
- matches_place_order: True

## 撤单

- status: cancelled
- cancelled_at: 2026-06-08 14:43:45.304101
- is_real_cancel: True

## 审计日志

- total_recent: 6
- related_to_order: 6
- events: ['order_synced', 'order_synced', 'cancel_broker_responded', 'order_synced', 'order_submitted', 'order_created']

## 收尾

- initial: 0
- remaining: 0

