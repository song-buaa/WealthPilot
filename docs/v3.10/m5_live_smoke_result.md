# v3.10 实盘最小额冒烟结果

运行时间: 2026-06-10T03:37:44.460865
账户: U3831209

## 第一笔: TSLA 拒单链路

- 本地最终状态: rejected
- is_rejected: True
- inactive_error_code: 201
- inactive_cb_error_code: 201
- inactive_resolved_as: rejected

预期: 201(保证金不足) → Inactive → rejected
实际: ✅ 符合

## 第二笔: NIO happy path + 真撤单

- place_order 状态: rejected
- broker_order_id (permId): 30959949
- is_perm_id: True
- orderRef 反查: found=True matches=True
- cancel 后状态: rejected
- is_real_cancel: False

### 三大核心验证

| 验证项 | 预期 | 实际 |
|--------|------|------|
| permId 回填 | 非 MOCK 数字 | ✅ 30959949 |
| 状态映射 | broker_pending | ❌ rejected |
| cancel 真撤单 | cancelled | ❌ rejected |

## 收尾

- openOrders: 0 (实际遗留: 0)
- ✅ 无遗留
