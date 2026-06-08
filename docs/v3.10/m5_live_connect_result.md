# M5 IBKR 实盘连接探针结果

运行时间: 2026-06-09T07:57:27.427774

**★ 全程只读，无任何订单操作**

## 连接

- 状态: OK
- 端口: 7496（实盘）

## managedAccounts

- 账户列表: ['U3831209']
- 包含预期账户: True

## accountSummary（只读）

- AvailableFunds: 13502.18 CNH
- BuyingPower: 90014.56 CNH
- GrossPositionValue: 29579.22 CNH
- NetLiquidation: 29770.76 CNH
- TotalCashValue: 191.53 CNH

## 闸门 1 live 校验

全部符合预期: True

| 账户 | 说明 | 预期 | 实际 | 符合? |
|------|------|------|------|-------|
| U3831209 | 真实 live 账户 | 通过 | 通过 | ✅ |
| U9999999 | 其他 U 开头 | 通过 | 通过 | ✅ |
| DUQ629797 | paper 账户 (DU 开头) | 拒绝 | 拒绝 | ✅ |
| <空> | 空字符串 | 拒绝 | 拒绝 | ✅ |
| X1234567 | 异常前缀 | 拒绝 | 拒绝 | ✅ |

## live 实盘前缀确认

- 实盘账户: ['U3831209']
- 全部 U 开头: True
- **结论: 实盘个人账户确认 U 前缀**

此结论消除 v3.10 验证报告 checklist 中「live=U 前缀待确认」的标注。

