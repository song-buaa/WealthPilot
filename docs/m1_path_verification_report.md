# M1-Step1: 产品哲学假设验证报告

> 生成时间：2026-05-01 11:18:19  
> 验证目标：当前 v2.5.1 代码遇 asset 模糊输入的实际路径  
> PRD 哲学（v1.4 §2.2）：模糊输入主动推断（路径 A）

## 路径分布

| 路径 | 含义 | 数量 |
|-----|------|------|
| A | 主动推断（符合产品哲学） | 0/3 |
| B | 走澄清（不符合，需 M1 实现推断 Tool） | 3/3 |
| C | 异常/中断（需先修 bug） | 0/3 |
| U | 人工判读（信号矛盾） | 0/3 |

## 用例细节

### ⚠️ PD_001 — 浮盈最大

**Q**：我有一只股票最近涨了不少，该不该趁现在落袋为安？

| 字段 | 值 |
|-----|-----|
| IntentResult.asset | (空) |
| IntentResult.confidence | 0.6 |
| IntentResult.action_type | TAKE_PROFIT |
| final_stage | intent |
| was_aborted | False |
| 回答中找到的持仓名 | (无) |
| 是否澄清话术 | True |
| decision_id | (无) |

**回答全文**：

```
根据您持仓的盈利情况，帮您筛出涨幅较大的标的：
• AAPL 240621 190C（+99.9%）
• 特斯拉（+66.9%）
• 苹果（+42.8%）

请问您说的是哪一只？或者直接告诉我标的名称也可以。
```

---

### ⚠️ PD_002 — 浮亏最大

**Q**：我有一只基金持续亏损，现在止损出来还是继续持有？

| 字段 | 值 |
|-----|-----|
| IntentResult.asset | (空) |
| IntentResult.confidence | 0.8 |
| IntentResult.action_type | STOP_LOSS |
| final_stage | intent |
| was_aborted | False |
| 回答中找到的持仓名 | (无) |
| 是否澄清话术 | True |
| decision_id | (无) |

**回答全文**：

```
根据您持仓的亏损情况，帮您筛出浮亏较大的标的：
• 微软（-20.1%）
• 景顺长城新能源产业股票A（-15.2%）
• 理想汽车-W（-13.8%）

请问您说的是哪一只？
```

---

### ⚠️ PD_003 — 重仓

**Q**：我看好一个标的想加仓，但它在我组合里已经不轻了，怎么判断能不能加？

| 字段 | 值 |
|-----|-----|
| IntentResult.asset | (空) |
| IntentResult.confidence | 0.9 |
| IntentResult.action_type | ANALYZE |
| final_stage | intent |
| was_aborted | False |
| 回答中找到的持仓名 | (无) |
| 是否澄清话术 | False |
| decision_id | (无) |

**回答全文**：

```
根据您的持仓占比，帮您筛出仓位较重的标的：
• 理想汽车（占比 24.5%）
• 拼多多（占比 7.1%）
• 安本标准-前缘市场债券基金A Acc USD（占比 6.6%）

请问您想分析的是哪一只？或者直接告诉我标的名称也可以。
```

---

## M1 行动建议

🔴 **全部走澄清路径**。当前代码不符合产品哲学。

**M1 第一优先工作**：在 ResearchAgent 中实现 `infer_target_from_holdings` Tool。

- 输入：`user_query`、`positions`
- 推断逻辑：
  - 关键词匹配：`涨/盈利/赚` → max(profit_loss_rate)
  - 关键词匹配：`跌/亏/套牢` → min(profit_loss_rate)
  - 关键词匹配：`重仓/不轻/占比大` → max(weight)
- 工作量：+0.5 天，应吸收进 M1 的 3.5 天工作量内

