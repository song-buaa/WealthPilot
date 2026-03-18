"""
Platform-specific CSV importers for Tiger Brokers and Futu.
Each parser returns (positions: list[dict], usd_to_cny_rate: float).
"""
import csv
import re
import io
from typing import Tuple, List

# 固收 ticker 白名单（无需名称映射，名称直接取 CSV 原始值）

# 固收 ticker 白名单
FIXED_INCOME_TICKERS = {"SHY", "BND", "AGG", "TLT", "IEF"}


def _extract_ticker(name_str: str) -> str:
    """从 '苹果 (AAPL)' 或 'Coinbase Global, Inc. (COIN)' 中提取 ticker"""
    m = re.search(r'\(([^)]+)\)$', name_str.strip())
    if m:
        return m.group(1).strip()
    return ""


def _classify_tiger(subsection: str, ticker: str, raw_name: str) -> str:
    """老虎证券资产大类分类"""
    if subsection == "基金":
        if "货币" in raw_name:
            return "货币"
        return "固收"
    # 股票
    if ticker in FIXED_INCOME_TICKERS:
        return "固收"
    return "权益"


def parse_tiger_csv(content: str) -> Tuple[List[dict], float]:
    """
    解析老虎证券对账单 CSV。
    返回 (positions, usd_to_cny_rate)。

    实际 CSV 格式（每行 col 0~N）：
      期末持仓行：col0="期末持仓", col1=subsection("股票"/"基金"/空), col3="DATA"/"TOTAL"/空
      FX 汇率行： col0="基本货币汇率", col3="HEADER_DATA", col4=货币代码, col5=汇率(相对USD)
    """
    usd_to_cny = 6.889  # 默认 fallback

    positions = []
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)

    # ── 第一遍：提取 CNH 汇率 ────────────────────────────────────
    # 行格式：['基本货币汇率', '', '', 'HEADER_DATA', 'CNH', '0.14515']
    # 0.14515 表示 1 CNH = 0.14515 USD，即 1 USD = 1/0.14515 CNY
    for row in rows:
        if (len(row) >= 6
                and row[0].strip() == "基本货币汇率"
                and row[3].strip() == "HEADER_DATA"
                and row[4].strip() == "CNH"):
            try:
                cnh_per_usd = float(row[5].strip())
                if cnh_per_usd > 0:
                    usd_to_cny = round(1.0 / cnh_per_usd, 6)
            except (ValueError, IndexError):
                pass

    # ── 第二遍：提取持仓数据 ─────────────────────────────────────
    # 每条持仓行：col0="期末持仓", col1=subsection, col3="DATA"
    # 股票行（含乘数列）：idx  4=名称, 5=数量, 6=乘数, 7=成本价, 8=收盘价, 9=市值, 10=P&L
    # 基金行（无乘数列）：idx  4=名称, 5=数量, 6=成本价, 7=收盘价, 8=市值, 9=P&L
    for row in rows:
        if not row:
            continue
        if row[0].strip() != "期末持仓":
            continue
        if len(row) < 4 or row[3].strip() != "DATA":
            continue

        subsection = row[1].strip() if len(row) > 1 else ""
        if subsection not in ("股票", "基金"):
            continue

        raw_name = row[4].strip() if len(row) > 4 else ""
        ticker = _extract_ticker(raw_name)

        try:
            quantity = float(row[5].strip().replace(",", "")) if len(row) > 5 else 0.0

            if subsection == "股票":
                # col6=乘数, col7=成本价, col8=收盘价, col9=市值, col10=P&L
                market_value_usd = float(row[9].strip().replace(",", "")) if len(row) > 9 else 0.0
                pnl_usd = float(row[10].strip().replace(",", "")) if len(row) > 10 else 0.0
            else:  # 基金
                # col6=成本价, col7=收盘价, col8=市值, col9=P&L
                market_value_usd = float(row[8].strip().replace(",", "")) if len(row) > 8 else 0.0
                pnl_usd = float(row[9].strip().replace(",", "")) if len(row) > 9 else 0.0
        except (ValueError, IndexError):
            continue

        if market_value_usd == 0 and quantity == 0:
            continue

        asset_class = _classify_tiger(subsection, ticker, raw_name)
        name = raw_name  # 老虎 CSV 原始名称即为标准格式，如"纳指100ETF (QQQ)"

        # P&L% 计算：pnl / cost_basis * 100
        cost_basis = market_value_usd - pnl_usd
        if cost_basis != 0:
            pnl_rate = pnl_usd / abs(cost_basis) * 100
        else:
            pnl_rate = 0.0

        positions.append({
            "name": name,
            "ticker": ticker,
            "platform": "老虎证券",
            "asset_class": asset_class,
            "segment": "投资",
            "currency": "USD",
            "original_currency": "USD",
            "original_value": round(market_value_usd, 2),
            "fx_rate_to_cny": usd_to_cny,
            "market_value_cny": round(market_value_usd * usd_to_cny),
            "quantity": quantity,
            "profit_loss_original_value": round(pnl_usd, 2),
            "profit_loss_value": round(pnl_usd * usd_to_cny),
            "profit_loss_rate": round(pnl_rate, 2),
        })

    return positions, usd_to_cny


def parse_futu_csv(content: str) -> Tuple[List[dict], float]:
    """
    解析富途证券持仓 CSV。
    返回 (positions, usd_to_cny_rate)。
    """
    from app.fx_service import fx_service
    usd_to_cny, _ = fx_service._get_rate_with_date("USD", "CNY", "latest")

    positions = []
    reader = csv.DictReader(io.StringIO(content))

    for row in reader:
        ticker = row.get("代码", "").strip()
        if not ticker:
            continue

        raw_name = row.get("名称", "").strip()
        # 富途：中文名+代码 → "微软 (MSFT)"，纯英文代码或与 ticker 相同则直接用 ticker
        name = f"{raw_name} ({ticker})" if raw_name and raw_name != ticker else ticker

        quantity_str = row.get("持有数量", "0").strip().replace(",", "")
        try:
            quantity = float(quantity_str)
        except ValueError:
            quantity = 0.0

        market_val_str = row.get("市值", "0").strip().replace(",", "").replace('"', "")
        try:
            market_value_usd = float(market_val_str)
        except ValueError:
            market_value_usd = 0.0

        pnl_str = row.get("盈亏金额", "0").strip().replace(",", "").replace('"', "")
        # 去掉前缀 +/- 后保留数值（含负号）
        pnl_str_clean = pnl_str.lstrip("+")
        try:
            pnl_usd = float(pnl_str_clean)
        except ValueError:
            pnl_usd = 0.0

        pnl_pct_str = row.get("盈亏比例", "0").strip().replace("%", "").replace("+", "")
        try:
            pnl_rate = float(pnl_pct_str)
        except ValueError:
            pnl_rate = 0.0

        if market_value_usd == 0:
            continue

        positions.append({
            "name": name,
            "ticker": ticker,
            "platform": "富途证券",
            "asset_class": "权益",
            "segment": "投资",
            "currency": "USD",
            "original_currency": "USD",
            "original_value": round(market_value_usd, 2),
            "fx_rate_to_cny": usd_to_cny,
            "market_value_cny": round(market_value_usd * usd_to_cny),
            "quantity": quantity,
            "profit_loss_original_value": round(pnl_usd, 2),
            "profit_loss_value": round(pnl_usd * usd_to_cny),
            "profit_loss_rate": round(pnl_rate, 2),
        })

    return positions, usd_to_cny
