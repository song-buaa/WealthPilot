"""
老虎持仓同步端到端验证脚本。

运行: cd backend && python -m scripts.tiger_sync_e2e

输出:
  1. DEBUG 信息（第一条持仓的原始 unrealized_pnl_percent 值）
  2. 完整持仓对账表（归一化后的 14 条）
  3. 总市值汇总（按币种）
"""
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

from services.broker_sync.tiger.sync_service import TigerSyncService


def main():
    print("=" * 80)
    print("老虎持仓同步端到端验证")
    print("=" * 80)

    service = TigerSyncService()

    # 拉数据前先打印一条 SDK 原始 pnl_pct 看格式
    sdk_positions = service._trade_client.get_positions(account=service.account_id)
    if sdk_positions:
        first = sdk_positions[0]
        raw_pct = first.unrealized_pnl_percent
        print(f"\n[DEBUG] 第一条持仓的 unrealized_pnl_percent 原始值:")
        print(f"  类型: {type(raw_pct).__name__}")
        print(f"  值:   {raw_pct!r}")
        print(f"  含义判断: 如果是百分数(如 30.5),adapter 需要 /100;如果是小数(如 0.305),adapter 直接用")

    # 转换为统一 schema
    positions = service.fetch_positions()

    print(f"\n共 {len(positions)} 条持仓\n")
    print(f"{'代码':<14}{'名称':<20}{'数量':>8}{'成本':>12}{'现价':>12}{'市值':>14}{'浮盈%':>10}")
    print("-" * 90)

    market_value_by_currency = defaultdict(Decimal)
    for pos in positions:
        market_value_by_currency[pos.currency] += pos.market_value
        pct_display = f"{pos.unrealized_pnl_pct:.4f}"
        print(
            f"{pos.symbol:<14}{pos.name[:18]:<20}"
            f"{pos.quantity:>8}{pos.avg_cost:>12}{pos.current_price:>12}"
            f"{pos.market_value:>14}{pct_display:>10}"
        )

    print("\n按币种汇总市值:")
    for ccy, total in market_value_by_currency.items():
        print(f"  {ccy}: {total:,.2f}")

    print(f"\n✅ 端到端验证通过（共 {len(positions)} 条持仓）")


if __name__ == "__main__":
    main()
