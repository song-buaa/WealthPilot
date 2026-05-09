"""M3-a 验证：资金流向 adapter"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
from dotenv import load_dotenv; from pathlib import Path
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / '.env')

from services.market_data.futu_capital_flow_service import fetch_capital_flow

for symbol in ["LI.US", "MSFT.US", "02015.HK"]:
    print(f"\n=== {symbol} ===")
    cf = fetch_capital_flow(symbol)
    if cf:
        print(f"  net_inflow: {cf.net_inflow:,.0f}")
        print(f"  super_net:  {cf.super_net:,.0f}" if cf.super_net is not None else "  super_net: None")
        print(f"  big_net:    {cf.big_net:,.0f}" if cf.big_net is not None else "  big_net: None")
        print(f"  small_net:  {cf.small_net:,.0f}" if cf.small_net is not None else "  small_net: None")
        print(f"  main_net:   {cf.main_net}" if cf.main_net is not None else "  main_net: None (美股正常)")
        print(f"  data_as_of: {cf.data_as_of}")
    else:
        print("  返回 None")
