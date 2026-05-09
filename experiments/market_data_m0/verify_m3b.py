"""M3-b 验证：K线技术指标"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from dotenv import load_dotenv; from pathlib import Path
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / '.env')

from services.market_data.tiger_kline_service import fetch_kline
from decision_engine.llm_engine import _interpret_technical

for symbol in ["MSFT.US", "LI.US", "02015.HK"]:
    print(f"\n=== {symbol} ===")
    td = fetch_kline(symbol)
    if td:
        print(f"  bars: {td.bars_count}")
        print(f"  close: {td.current_price}")
        print(f"  MA5={td.ma5}, MA20={td.ma20}")
        print(f"  RSI14={td.rsi14}")
        print(f"  MACD hist={td.macd_hist}")
        print(f"  ma_position: {td.ma_position}")
        print(f"  trend_signal: {td.trend_signal}")
        print(f"  interpretation: {_interpret_technical(td)}")
    else:
        print("  返回 None")
