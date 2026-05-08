"""雪盈持仓同步端到端验证（会真实写入数据库）。"""
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

from app.database import get_session, init_db
from app.models import Position as BusinessPosition
from services.broker_sync.snowball.sync_service import SnowballSyncService
from services.broker_sync.models import PositionSnapshotRun, PositionSnapshot


def main():
    print("=" * 80)
    print("雪盈 API → snapshot → Position 业务表 端到端验证")
    print("=" * 80)

    init_db()
    service = SnowballSyncService()
    db = get_session()

    try:
        before = db.query(BusinessPosition).filter_by(platform="雪盈证券").count()
        before_total = db.query(BusinessPosition).count()
        print(f"\n[同步前] Position 表共 {before_total} 条,其中雪盈证券 {before} 条\n")

        run_id = service.sync_and_persist(db, triggered_by="manual")
        print(f"✅ 同步成功 run_id={run_id}\n")

        after = db.query(BusinessPosition).filter_by(platform="雪盈证券").count()
        after_total = db.query(BusinessPosition).count()
        print(f"[同步后] Position 表共 {after_total} 条,其中雪盈证券 {after} 条\n")

        snowball_positions = db.query(BusinessPosition).filter_by(platform="雪盈证券").all()
        print("雪盈证券持仓明细:")
        print(f"{'ticker':<10}{'name':<16}{'qty':>8}{'mv_cny':>14}{'asset_class':>10}")
        print("-" * 60)
        for p in snowball_positions:
            print(
                f"{p.ticker:<10}{(p.name or '')[:14]:<16}"
                f"{p.quantity:>8}{p.market_value_cny:>14.2f}"
                f"{p.asset_class:>10}"
            )

        # 验证 LI 跨三平台合并
        ticker_platforms = defaultdict(set)
        for p in db.query(BusinessPosition).all():
            if p.ticker:
                ticker_platforms[p.ticker].add(p.platform)
        li_platforms = ticker_platforms.get("LI", set())
        if len(li_platforms) > 1:
            li_rows = db.query(BusinessPosition).filter_by(ticker="LI").all()
            total_qty = sum(r.quantity or 0 for r in li_rows)
            total_mv = sum(r.market_value_cny or 0 for r in li_rows)
            print(f"\n🎯 LI(理想汽车)跨平台合并: {' + '.join(sorted(li_platforms))}")
            print(f"   合并总持仓: {total_qty:.0f} 股, 合并市值: {total_mv:,.0f} CNY")
        else:
            print(f"\nLI 当前平台: {li_platforms}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
