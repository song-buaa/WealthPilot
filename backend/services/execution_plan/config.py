"""
执行计划引擎默认参数。

集中管理 §5.2 的阈值，不硬编码进 rule_engine。
v1 不做前端配置；每次生成计划时实际取值落入 constraints_applied。
"""

# ── 触发价生成 (§5.2 B) ──────────────────────────────────────
ATR_MULTIPLIER = 1.5          # base_step% = ATR_MULTIPLIER * ATR%
BASE_STEP_MIN_PCT = 0.03      # 单档间距下限 3%
BASE_STEP_MAX_PCT = 0.08      # 单档间距上限 8%
MAX_TOTAL_DEVIATION_PCT = 0.25  # 最远一档偏离现价上限 25%
MIN_SPREAD_PCT = 0.005        # 相邻档最小价差(占现价) 0.5%

# ── 分位保护 (§5.2 B) ─────────────────────────────────────────
HIGH_PERCENTILE_THRESHOLD = 0.8  # percentile > 此值时首档延后

# ── 批数 N (§5.2 C) ──────────────────────────────────────────
VOL_HIGH_THRESHOLD = 0.40     # 年化波动率 > 40% 时 N += 1
MAX_BATCHES = 5               # 批数封顶

# ── 限价 buffer (触发价 ± buffer = 限价) ──────────────────────
LIMIT_PRICE_BUFFER_PCT = 0.002  # 限价在触发价基础上偏移 0.2%
