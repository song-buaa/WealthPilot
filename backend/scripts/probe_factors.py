#!/usr/bin/env python3
"""
因子 service 真实数据验证脚本。

用真实持仓里的 1 个美股 + 1 个港股各跑一次 build_factor_snapshot，
打印完整 FactorSnapshot。

运行: /Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python backend/scripts/probe_factors.py
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

from services.execution_plan.factors import build_factor_snapshot


def _pp(snap):
    d = snap.to_dict()
    print(json.dumps(d, indent=2, default=str, ensure_ascii=False))


def main():
    print("=" * 60)
    print("  美股: LI:US (理想汽车)")
    print("=" * 60)
    us = build_factor_snapshot("LI:US", "US")
    _pp(us)

    print()
    print("=" * 60)
    print("  港股: 0700:HK (腾讯)")
    print("=" * 60)
    hk = build_factor_snapshot("0700:HK", "HK")
    _pp(hk)

    # 简要验证
    print()
    print("=" * 60)
    print("  验证摘要")
    print("=" * 60)
    for label, snap in [("US LI", us), ("HK 0700", hk)]:
        print(f"\n  {label}:")
        print(f"    current_price: {snap.current_price}")
        print(f"    atr14:         {snap.atr14}")
        print(f"    volatility:    {snap.volatility_annual}")
        print(f"    percentile:    {snap.price_percentile}")
        print(f"    drawdown:      {snap.drawdown_from_high}")
        print(f"    degraded:      {snap.data_source_meta.get('degraded_fields', [])}")
        has_new = all([snap.atr14, snap.volatility_annual, snap.drawdown_from_high is not None])
        print(f"    四个新因子完整: {'YES' if has_new else 'NO (部分降级)'}")


if __name__ == "__main__":
    main()
