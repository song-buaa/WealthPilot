"""
时效衰减函数。

MVP 阶段不启用（所有 decay_factor 恒为 1.0）。
代码就位，v3.6.2 启用时改 knowledge.yaml 的 decay.enabled=true 即可。

衰减公式：decay_factor = 0.5 ** (age_months / half_life_months)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


# 半衰期配置（月），与 knowledge.yaml 的 decay.half_life_months 对齐
_DEFAULT_HALF_LIVES = {
    "permanent": 99999,
    "slow_decay": 12,
    "medium_decay": 3,
    "fast_decay": 1,
}


def compute_decay_factor(
    time_sensitivity: Optional[str],
    content_date: Optional[str],
    half_lives: Optional[dict[str, float]] = None,
    enabled: bool = False,
) -> float:
    """
    计算时效衰减因子。

    Args:
        time_sensitivity: permanent / slow_decay / medium_decay / fast_decay
        content_date: 内容日期，ISO 8601 格式（如 "2026-04-15"）
        half_lives: 半衰期配置，默认使用内置值
        enabled: 是否启用衰减。False 时恒返回 1.0

    Returns:
        衰减因子（0-1），1.0 表示无衰减
    """
    if not enabled:
        return 1.0

    if not time_sensitivity or not content_date:
        return 1.0

    lives = half_lives or _DEFAULT_HALF_LIVES
    half_life = lives.get(time_sensitivity, 99999)

    if half_life >= 99999:
        return 1.0

    try:
        dt = datetime.fromisoformat(content_date.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age_months = max(0, (now - dt).days / 30.44)
        return 0.5 ** (age_months / half_life)
    except (ValueError, TypeError):
        return 1.0
