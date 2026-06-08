# M3 IBKR Paper 端到端验证结果

运行时间: 2026-06-08T22:55:15.696446

## 连接

- status: OK
- account: DUQ629797

## 下单

- status: submitted_to_broker
- broker_order_id: 1008325252
- broker_name: ibkr
- local_order_id: 473fe2ea-6551-452d-a3a9-5df5f9a76931
- elapsed_ms: 1259
- is_perm_id: True

## 同步状态

- status: broker_pending
- filled_quantity: 0

## orderRef 反查

- found: True
- broker_order_id: 1008325252
- status: broker_pending
- matches_place_order: True

## 撤单

- status: cancelled
- cancelled_at: 2026-06-08 14:55:20.144261
- is_real_cancel: True

## 审计日志

- total_recent: 13
- related_to_order: 7
- events: ['order_synced', 'sync_network_error', 'order_synced', 'cancel_broker_responded', 'order_synced', 'order_submitted', 'order_created']

## 收尾

- initial: 0
- remaining: 0

