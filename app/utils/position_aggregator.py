"""
持仓聚合公共模块 (Position Aggregator)

职责：跨平台合并同一标的的持仓数据，作为整个系统的唯一持仓聚合来源。

口径定义（全系统统一）：
    当前仓位 (weight) = 该标的聚合市值 / 所有投资类持仓总市值
    即：占投资组合的比例，不含生活账户资产

使用方：
    - app_pages/discipline.py  （持仓集中度规则校验）
    - decision_engine/data_loader.py  （投资决策数据加载）

聚合规则（与 discipline.py 原实现完全一致）：
    - 券商平台：优先按 ticker 合并；无 ticker 时按正则标准化名称合并
    - 银行/支付宝：按产品名称合并（建设银行有专属分类映射）

名称标准化：
    "理想汽车-W_1" / "理想汽车-W_2" → "理想汽车"
    "理想汽车 (LI)"                  → "理想汽车"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.models import Position, get_session


# ── 常量（与 discipline.py 保持完全一致）────────────────────────────────────────

_BANK_PLATFORMS = {"招商银行", "建设银行", "支付宝"}

_CCB_NAME_MAP = {
    "活钱":     "活钱管理",
    "理财产品": "稳健投资",
    "债券":     "稳健投资",
    "基金":     "进取投资",
}

_LEVERAGE_ETF_KEYWORDS = ["杠杆", "TQQQ", "SOXL", "UPRO", "TECL", "LABU", "FNGU"]


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class AggregatedPosition:
    """
    单一标的跨平台聚合后的持仓信息。

    weight 字段在 aggregate_investment_positions() 返回后已计算好，
    始终等于 market_value_cny / 同批次 total_assets，口径与 discipline.py 完全一致。
    """
    name: str                    # 标准化显示名称
    ticker: str                  # 主要 ticker（来自第一个有 ticker 的持仓）
    asset_class: str             # 资产分类（来自第一个持仓）
    market_value_cny: float      # 聚合市值（所有平台之和）
    profit_loss_value: float     # 聚合盈亏金额
    cost_value: float            # 聚合成本价值
    pl_rate: float               # 加权盈亏率（%，注意是百分比数值，不是小数）
    weight: float                # 占投资组合总市值比例（0~1 小数）
    platforms: list[str] = field(default_factory=list)   # 持仓平台列表
    is_leverage_etf: bool = False

    @property
    def platform_display(self) -> str:
        return " / ".join(self.platforms)

    @property
    def profit_loss_rate(self) -> float:
        """返回小数形式的盈亏率，兼容 PositionInfo.profit_loss_rate 的调用方。"""
        return self.pl_rate / 100.0


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _is_leverage_etf(name: str, ticker: str) -> bool:
    text = f"{name} {ticker}".upper()
    return any(k.upper() in text for k in _LEVERAGE_ETF_KEYWORDS)


def _norm(name: str) -> str:
    """
    标准化名称：去掉序号后缀、港股标识、括号内容。
    与 discipline.py._aggregate_positions 内嵌函数完全一致。
    """
    name = re.sub(r'_\d+$', '', name)           # 去掉 _1 _2 等序号
    name = re.sub(r'-W$', '', name)              # 去掉港股 -W 标识
    name = re.sub(r'\s*\(.*?\)\s*$', '', name)   # 去掉括号内容如 (LI)
    return name.strip()


# ── 核心公共函数 ───────────────────────────────────────────────────────────────

def load_raw_positions(pid: int, segment: str = "投资") -> list[dict]:
    """
    从数据库加载原始持仓记录，返回序列化 dict 列表。

    不做任何聚合，供下游的 aggregate() 函数使用。
    """
    session = get_session()
    try:
        rows = session.query(Position).filter_by(
            portfolio_id=pid, segment=segment
        ).all()
        return [
            {
                "name": p.name,
                "ticker": p.ticker or "",
                "platform": p.platform or "",
                "asset_class": p.asset_class or "未知",
                "market_value_cny": p.market_value_cny or 0.0,
                "profit_loss_rate": p.profit_loss_rate or 0.0,
                "profit_loss_value": p.profit_loss_value or 0.0,
                "cost_price": p.cost_price or 0.0,
                "current_price": p.current_price or 0.0,
                "is_leverage_etf": _is_leverage_etf(p.name or "", p.ticker or ""),
            }
            for p in rows
        ]
    finally:
        session.close()


def aggregate(raw: list[dict]) -> tuple[list[AggregatedPosition], float]:
    """
    对原始持仓列表执行跨平台聚合，返回 (聚合后列表, 总市值)。

    聚合逻辑与 discipline.py._aggregate_positions() 完全一致，
    此函数是其提取后的公共版本。

    Args:
        raw: load_raw_positions() 返回的 dict 列表

    Returns:
        (aggregated_positions, total_assets_cny)
        - aggregated_positions: 每个标的只有一条，已按市值降序排列
        - total_assets_cny: 所有持仓的总市值（用于计算 weight）
    """
    # ── 第一遍：建立「标准名 → ticker」映射（来自有 ticker 的券商持仓）──────
    norm_to_ticker: dict[str, str] = {}
    for r in raw:
        if r["platform"] not in _BANK_PLATFORMS and r["ticker"]:
            norm_to_ticker[_norm(r["name"])] = r["ticker"]

    # ── 第二遍：按聚合 key 累加 ────────────────────────────────────────────
    buckets: dict[str, dict] = {}

    for r in raw:
        is_bank = r["platform"] in _BANK_PLATFORMS

        if is_bank:
            # 建设银行：映射到标准三类；其他银行/支付宝直接用 name
            key = (
                _CCB_NAME_MAP.get(r["name"], r["name"])
                if r["platform"] == "建设银行"
                else r["name"]
            )
        elif r["ticker"]:
            key = r["ticker"]
        else:
            key = norm_to_ticker.get(_norm(r["name"]), _norm(r["name"]))

        if key not in buckets:
            display_name = key if is_bank else _norm(r["name"])
            buckets[key] = {
                "name": display_name,
                "ticker": r["ticker"] or key,
                "asset_class": r["asset_class"],
                "market_value_cny": 0.0,
                "profit_loss_value": 0.0,
                "cost_value": 0.0,
                "is_leverage_etf": r["is_leverage_etf"],
                "platforms": [],
            }

        b = buckets[key]
        b["market_value_cny"] += r["market_value_cny"]
        b["profit_loss_value"] += r.get("profit_loss_value", 0.0)
        b["cost_value"] += r["market_value_cny"] - r.get("profit_loss_value", 0.0)
        if r["platform"] and r["platform"] not in b["platforms"]:
            b["platforms"].append(r["platform"])

    # ── 计算总市值和每个标的的 weight ────────────────────────────────────
    total_assets = sum(b["market_value_cny"] for b in buckets.values()) or 1.0

    result: list[AggregatedPosition] = []
    for b in buckets.values():
        pl_rate = (
            b["profit_loss_value"] / b["cost_value"] * 100
            if b["cost_value"] > 0
            else 0.0
        )
        result.append(AggregatedPosition(
            name=b["name"],
            ticker=b["ticker"],
            asset_class=b["asset_class"],
            market_value_cny=b["market_value_cny"],
            profit_loss_value=b["profit_loss_value"],
            cost_value=b["cost_value"],
            pl_rate=pl_rate,
            weight=b["market_value_cny"] / total_assets,
            platforms=b["platforms"],
            is_leverage_etf=b["is_leverage_etf"],
        ))

    result.sort(key=lambda x: -x.market_value_cny)
    return result, total_assets


def aggregate_investment_positions(
    pid: int,
) -> tuple[list[AggregatedPosition], float]:
    """
    便捷入口：加载并聚合指定 portfolio 的投资类持仓。

    Returns:
        (aggregated_positions, total_assets_cny)
    """
    raw = load_raw_positions(pid, segment="投资")
    return aggregate(raw)


def find_target(
    positions: list[AggregatedPosition],
    asset_name: str,
) -> tuple[Optional[AggregatedPosition], list[AggregatedPosition]]:
    """
    在聚合后的持仓列表中查找目标标的。

    匹配优先级（精确 > 模糊，与 data_loader._find_all_positions 逻辑一致）：
        1. 精确匹配：name 完全相等 或 ticker 完全相等
        2. 模糊匹配：name/ticker 双向包含（至少 2 字符）

    Returns:
        (target, ambiguous_list)
        - target: 唯一命中时返回该持仓；多命中或零命中时返回 None
        - ambiguous_list: 多命中时返回候选列表；其他情况为空列表
    """
    if not asset_name:
        return None, []

    name_lower = asset_name.lower().replace(" ", "")
    if len(name_lower) < 2:
        return None, []

    exact: list[AggregatedPosition] = []
    partial: list[AggregatedPosition] = []

    for p in positions:
        p_name = p.name.lower().replace(" ", "")
        p_ticker = p.ticker.strip().lower()

        name_exact = name_lower == p_name
        ticker_exact = len(p_ticker) >= 2 and name_lower == p_ticker

        if name_exact or ticker_exact:
            exact.append(p)
            continue

        name_partial = (name_lower in p_name) or (p_name in name_lower and len(p_name) >= 2)
        ticker_partial = len(p_ticker) >= 2 and (p_ticker in name_lower or name_lower in p_ticker)

        if name_partial or ticker_partial:
            partial.append(p)

    candidates = exact if exact else partial

    if len(candidates) == 1:
        return candidates[0], []
    elif len(candidates) > 1:
        return None, candidates
    else:
        return None, []
