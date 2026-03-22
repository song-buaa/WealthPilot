"""
前置校验模块 (Pre-check)

职责：在进入决策流程前，校验必要数据是否完备。
若以下任一缺失，不进入决策流程，返回提示信息：
    - 用户画像
    - 投资纪律
    - 持仓数据
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .data_loader import LoadedData


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class PreCheckResult:
    """前置校验结果"""
    passed: bool
    missing_items: list[str]   # 缺失项列表
    message: Optional[str]     # 展示给用户的提示信息（passed=False 时有值）


# ── 核心函数 ───────────────────────────────────────────────────────────────────

def check(data: LoadedData) -> PreCheckResult:
    """
    校验决策所需数据是否完备。

    Args:
        data: DataLoader 返回的 LoadedData

    Returns:
        PreCheckResult
    """
    missing = []

    # 校验一：用户画像
    if data.profile is None:
        missing.append("用户投资画像")

    # 校验二：持仓数据
    if not data.positions:
        missing.append("持仓数据")

    # 校验三：投资纪律
    if data.rules is None:
        missing.append("投资纪律配置")

    # 校验四：总资产是否合理（防止全零数据）
    if data.total_assets <= 0:
        missing.append("有效资产数据（总资产为0）")

    if missing:
        items_str = "、".join(missing)
        return PreCheckResult(
            passed=False,
            missing_items=missing,
            message=f"请先完善您的{items_str}后再进行决策分析。",
        )

    return PreCheckResult(passed=True, missing_items=[], message=None)
