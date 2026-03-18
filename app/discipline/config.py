"""
投资纪律执行引擎 — 规则参数配置

来源：《投资纪律手册 v1.2》JSON 参数块
⚠️  严格禁止修改任何参数值，所有约束均来自真实亏损经历。
"""

RULES: dict = {
    "single_asset_limits": {
        "max_position_pct":          0.40,   # 单一标的仓位硬性上限（规则3）
        "warning_position_pct":      0.30,   # 警戒区起点（规则3）
        "preferred_position_range":  [0.20, 0.30],
        "core_holding_floor_pct":    0.10,   # 底仓下限（规则9）
    },
    "position_sizing": {
        "max_single_add_pct":              0.10,  # 单次加仓上限（规则6）
        "min_batches_required":            2,     # 最少分批次数（规则6）
        "min_interval_between_adds_days":  1,     # 两次加仓最小间隔（规则6）
    },
    "leverage_limits": {
        "level_0_forbidden":   ["margin_trading", "options", "credit_loan"],
        "level_1_max_pct":     0.05,   # 杠杆 ETF 持仓上限（规则1）
        "leverage_ratio_max":  1.0,
    },
    "liquidity_limits": {
        "min_cash_pct":        0.20,   # 正常市场最低流动性资金比例（货币+固收，规则4）
        "extreme_reserve_pct": 0.10,   # 极端情形保留子弹（规则4）
    },
    "rebalancing_rules": {
        "deviation_warning_pct":          0.10,  # 偏离度预警阈值（规则2）
        "deviation_force_rebalance_pct":  0.20,  # 强制再平衡阈值（规则2）
    },
    "stop_loss_rules": {
        "hard_stop":                     "logic_break",
        "soft_stop_review_trigger_pct":  0.30,   # 软止损复核触发回撤（规则5）
        "soft_stop_action":              "force_review",
    },
    "asset_allocation_ranges": {
        # 规则2（v1.2 更新版）—— 与大类资产配置5类保持一致
        "monetary_min_amount":  10_000,   # 货币：最低1万元（绝对金额）
        "monetary_max_amount": 100_000,   # 货币：最高10万元（绝对金额）
        "fixed_income_min":     0.20,     # 固收：20%~60%
        "fixed_income_max":     0.60,
        "equity_min":           0.40,     # 权益：40%~80%
        "equity_max":           0.80,
        "alternatives_max":     0.10,     # 另类：0%~10%
        "derivatives_max":      0.10,     # 衍生：0%~10%
    },
    "cooldown_rules": {
        "daily_nav_drop_trigger_pct": 0.05,  # 单日净值跌幅触发冷却（规则10）
        "cooldown_hours":             24,
    },
    "portfolio_circuit_breaker": {
        "drawdown_trigger_pct":  0.25,              # 触发熔断的回撤幅度
        "action":                "suspend_all_buys",
        "resume_threshold_pct":  0.15,              # 恢复操作的回撤阈值
    },
}
