"""
WealthPilot — 投资纪律规则配置

来源：《投资纪律手册 v1.4》
运行时优先从 data/rules_config.json 加载，文件不存在则使用代码内置默认值。
通过 get_rules() 获取当前生效配置，保证手册上传后即时生效。
"""

import json
from pathlib import Path

_RULES_FILE = Path("data/rules_config.json")

# ── v1.4 内置默认值（手册文件缺失时的保底）─────────────────────────────────
_DEFAULT_RULES: dict = {
    "single_asset_limits": {
        "max_position_pct":          0.40,   # 单一标的仓位硬性上限（规则3）
        "warning_position_pct":      0.30,   # 警戒区起点（规则3）
        "preferred_position_range":  [0.20, 0.30],
        "core_holding_floor_pct":    0.10,   # 底仓下限（规则9）
    },
    "position_sizing": {
        "max_single_add_pct":              0.10,  # 单次加仓上限（规则6）
        "min_batches_required":            2,
        "min_interval_between_adds_days":  1,
    },
    "leverage_limits": {
        # v1.4: Level0 仅剩期权/高危衍生品；融资融券和借贷投资降为 Level1
        "level_0_forbidden":             ["options"],
        "level_1_items":                 ["margin_trading", "credit_loan"],
        "level_1_max_pct":               0.05,   # 杠杆ETF持仓上限（规则1）
        # 总杠杆率 4 档阈值（规则1·v1.4）
        "leverage_ratio_normal_max":     1.05,   # ≤1.05 正常
        "leverage_ratio_acceptable_max": 1.20,   # 1.05~1.20 可接受
        "leverage_ratio_warning_max":    1.35,   # 1.20~1.35 警戒；>1.35 超限
    },
    "liquidity_limits": {
        "min_cash_pct":        0.20,
        "extreme_reserve_pct": 0.10,
    },
    "rebalancing_rules": {
        "deviation_warning_pct":          0.10,
        "deviation_force_rebalance_pct":  0.20,
    },
    "stop_loss_rules": {
        "hard_stop":                     "logic_break",
        "soft_stop_review_trigger_pct":  0.30,
        "soft_stop_action":              "force_review",
    },
    "asset_allocation_ranges": {
        "monetary_min_amount":  10_000,
        "monetary_max_amount": 100_000,
        "fixed_income_min":     0.20,
        "fixed_income_max":     0.60,
        "equity_min":           0.40,
        "equity_max":           0.80,
        "alternatives_max":     0.10,
        "derivatives_max":      0.10,
    },
    "cooldown_rules": {
        "daily_nav_drop_trigger_pct": 0.05,
        "cooldown_hours":             24,
    },
    "portfolio_circuit_breaker": {
        "drawdown_trigger_pct":  0.25,
        "action":                "suspend_all_buys",
        "resume_threshold_pct":  0.15,
    },
}


def get_rules() -> dict:
    """
    动态加载当前生效的规则配置。
    优先读取 data/rules_config.json（用户通过手册上传更新），
    文件不存在或损坏时回退到代码内置默认值。
    """
    if _RULES_FILE.exists():
        try:
            return json.loads(_RULES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return _DEFAULT_RULES


def save_rules_config(config: dict) -> None:
    """将规则配置持久化到 data/rules_config.json。"""
    _RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _RULES_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def reset_rules_to_default() -> None:
    """删除 data/rules_config.json，使 get_rules() 回退到代码内置默认值。"""
    if _RULES_FILE.exists():
        _RULES_FILE.unlink()


# 向后兼容：模块级常量（import RULES 的地方仍可用）
# ⚠️  此值在进程启动时加载一次；如需即时生效请改用 get_rules()
RULES: dict = get_rules()
