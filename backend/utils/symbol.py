"""
WealthPilot 全局 Symbol 标准化工具。

统一格式: TICKER:MARKET
  - 美股: AAPL:US, LI:US
  - 港股: 0700:HK (4 位补零, 港交所官方标准)
  - A 股: 600519:SH, 300750:SZ

本模块是全系统唯一的 symbol 格式权威, 其他模块应通过此模块
进行格式转换, 不应自行实现 symbol 解析逻辑。
"""

import re

VALID_MARKETS = {"US", "HK", "SH", "SZ", "CN"}

# CN 是 broker_sync 层历史遗留的 A 股市场代码, 统一映射到 SH/SZ
# 但单独出现 CN 时无法区分 SH/SZ, 需要根据代码规则推断
_CN_PREFIX_TO_MARKET = {
    "6": "SH",  # 60xxxx → 上海
    "9": "SH",  # 90xxxx → 上海 B 股
    "0": "SZ",  # 00xxxx → 深圳
    "3": "SZ",  # 30xxxx → 深圳创业板
    "2": "SZ",  # 20xxxx → 深圳 B 股
}

# currency → market 推断 (兼容旧格式)
_CURRENCY_TO_MARKET = {
    "USD": "US",
    "HKD": "HK",
    "CNH": "CN",
    "CNY": "CN",
}


def normalize_symbol(ticker: str, market: str) -> str:
    """统一输出 TICKER:MARKET 格式。

    港股 ticker 自动 zfill(4)。
    CN 市场自动根据代码前缀细分为 SH/SZ。

    >>> normalize_symbol("700", "HK")
    '0700:HK'
    >>> normalize_symbol("AAPL", "US")
    'AAPL:US'
    >>> normalize_symbol("600519", "CN")
    '600519:SH'
    """
    ticker = ticker.strip().lstrip("'")
    market = market.strip().upper()

    if market == "HK" and ticker.isdigit():
        ticker = ticker.lstrip("0") or "0"
        ticker = ticker.zfill(4)

    if market == "CN" and ticker.isdigit() and len(ticker) == 6:
        prefix = ticker[0]
        market = _CN_PREFIX_TO_MARKET.get(prefix, "SH")

    return f"{ticker}:{market}"


def parse_symbol(symbol_str: str) -> tuple[str, str]:
    """解析 symbol 字符串 → (ticker, market)。

    主路径: TICKER:MARKET (统一格式)
    兼容旧格式:
      - TICKER.MARKET (broker_sync 遗留)
      - MARKET.TICKER (OrderRequest 遗留 / 富途 SDK 原生)

    >>> parse_symbol("LI:US")
    ('LI', 'US')
    >>> parse_symbol("LI.US")
    ('LI', 'US')
    >>> parse_symbol("US.LI")
    ('LI', 'US')
    >>> parse_symbol("0700:HK")
    ('0700', 'HK')

    Raises:
        ValueError: 格式不合法
    """
    s = symbol_str.strip()
    if not s:
        raise ValueError("symbol 字符串为空")

    # 主路径: TICKER:MARKET
    if ":" in s:
        ticker, market = s.split(":", 1)
        ticker = ticker.strip()
        market = market.strip().upper()
        if market in VALID_MARKETS:
            return ticker, market
        raise ValueError(f"无效市场代码 '{market}', 有效值: {sorted(VALID_MARKETS)}")

    # 兼容: 点分隔
    if "." in s:
        left, right = s.split(".", 1)
        left_upper = left.strip().upper()
        right_upper = right.strip().upper()

        # 判断哪边是 market: 如果 left 是已知 market → MARKET.TICKER
        if left_upper in VALID_MARKETS:
            return right.strip(), left_upper
        # 否则 → TICKER.MARKET
        if right_upper in VALID_MARKETS:
            return left.strip(), right_upper
        # 特殊: BRK.B 这种 ticker 含点的情况, 无法推断 market
        raise ValueError(
            f"无法从 '{s}' 推断 market, 左='{left}' 右='{right}' 均不是有效市场代码"
        )

    raise ValueError(f"symbol '{s}' 缺少分隔符 ':' 或 '.', 无法解析")


def ticker_to_broker_sync(symbol_str: str) -> str:
    """TICKER:MARKET → TICKER.MARKET (broker_sync 内部格式)。

    >>> ticker_to_broker_sync("LI:US")
    'LI.US'
    >>> ticker_to_broker_sync("0700:HK")
    '0700.HK'
    """
    ticker, market = parse_symbol(symbol_str)
    return f"{ticker}.{market}"


def broker_sync_to_symbol(broker_sync_str: str) -> str:
    """TICKER.MARKET → TICKER:MARKET。

    >>> broker_sync_to_symbol("LI.US")
    'LI:US'
    >>> broker_sync_to_symbol("00700.HK")
    '00700:HK'
    """
    ticker, market = parse_symbol(broker_sync_str)
    return f"{ticker}:{market}"


def symbol_to_futu(symbol_str: str) -> str:
    """TICKER:MARKET → MARKET.TICKER (富途 SDK 格式)。

    港股 ticker 补零到 5 位（富途需要 HK.00700 而非 HK.0700）。

    >>> symbol_to_futu("QQQ:US")
    'US.QQQ'
    >>> symbol_to_futu("0700:HK")
    'HK.00700'
    >>> symbol_to_futu("00700:HK")
    'HK.00700'
    """
    ticker, market = parse_symbol(symbol_str)
    if market == "HK" and ticker.isdigit():
        ticker = ticker.zfill(5)
    return f"{market}.{ticker}"


def symbol_to_av_ticker(symbol_str: str) -> str | None:
    """TICKER:MARKET → Alpha Vantage 纯 ticker。港股返回 None。

    >>> symbol_to_av_ticker("LI:US")
    'LI'
    >>> symbol_to_av_ticker("0700:HK") is None
    True
    """
    ticker, market = parse_symbol(symbol_str)
    if market == "HK":
        return None
    return ticker


def symbol_to_tiger_ticker(symbol_str: str) -> str:
    """TICKER:MARKET → Tiger SDK 纯 ticker。

    港股 ticker 补零到 5 位（Tiger SDK 需要 00700 而非 0700）。

    >>> symbol_to_tiger_ticker("LI:US")
    'LI'
    >>> symbol_to_tiger_ticker("0700:HK")
    '00700'
    >>> symbol_to_tiger_ticker("00700:HK")
    '00700'
    """
    ticker, market = parse_symbol(symbol_str)
    if market == "HK" and ticker.isdigit():
        ticker = ticker.zfill(5)
    return ticker


def infer_symbol_from_ticker(ticker: str, currency: str) -> str | None:
    """从裸 ticker + currency 推断标准 symbol。

    用于 positions 表等只有 ticker + currency 的旧数据。
    无法推断时返回 None (基金、ISIN 等)。

    >>> infer_symbol_from_ticker("AAPL", "USD")
    'AAPL:US'
    >>> infer_symbol_from_ticker("00068", "HKD")
    '0068:HK'
    >>> infer_symbol_from_ticker("006479", "CNY")  # 基金
    """
    if not ticker or not ticker.strip():
        return None

    t = ticker.strip()
    c = (currency or "").upper()

    # ISIN
    if t.startswith("LU") or t.startswith("IE"):
        return None

    # 含 .SH / .SZ 后缀的 A 股 ticker
    if re.match(r"^\d{6}\.S[HZ]$", t):
        return f"{t[:6]}:{t[-2:]}"

    # 纯数字
    if t.isdigit():
        if c == "HKD" or (len(t) in (4, 5) and c not in ("USD", "CNY")):
            return normalize_symbol(t, "HK")
        if len(t) == 6 and c in ("CNY", "CNH"):
            # 6 位 + CNY 无法可靠区分 A 股 vs 基金(代码空间重叠)
            # 保守策略: 全部返回 None, 不做推断
            # A 股应通过 broker_sync 层(有明确 market 字段)获得正确 symbol
            return None
        # 短数字 + USD → 可能是港股美元计价, 不确定
        if len(t) <= 5 and c == "USD":
            return None
        return None

    # 期权格式
    if re.match(r"^[A-Z]+\d{6}[CP]\d+$", t):
        return None

    # 含 - 或过长 → 不支持
    if "-" in t or len(t) > 10:
        return None

    # 纯字母 1-5 位 → 美股
    if re.match(r"^[A-Z]{1,5}$", t):
        return f"{t}:US"

    # 混合字母数字 (如 BRK) 且 USD → 美股
    if c == "USD" and re.match(r"^[A-Z0-9]{1,10}$", t):
        return f"{t}:US"

    return None
