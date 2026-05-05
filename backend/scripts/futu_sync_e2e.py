"""
富途持仓同步端到端验证（会真实调用 OpenD + 写入数据库）。

前提:OpenD 已在本地运行(127.0.0.1:11111)。

运行: cd backend && python -m scripts.futu_sync_e2e
"""
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

from app.database import get_session, init_db
from app.models import Position as BusinessPosition
from services.broker_sync.futu.sync_service import FutuSyncService
from services.broker_sync.models import PositionSnapshotRun, PositionSnapshot


def main():
    print("=" * 80)
    print("富途 API → snapshot → Position 业务表 端到端验证")
    print("=" * 80)

    init_db()
    service = FutuSyncService()
    db = get_session()

    try:
        before_futu = db.query(BusinessPosition).filter_by(platform="富途证券").count()
        before_total = db.query(BusinessPosition).count()
        print(f"\n[同步前] Position 表共 {before_total} 条,其中富途证券 {before_futu} 条\n")

        run_id = service.sync_and_persist(db, triggered_by="manual")
        print(f"✅ 同步成功 run_id={run_id}\n")

        after_futu = db.query(BusinessPosition).filter_by(platform="富途证券").count()
        after_total = db.query(BusinessPosition).count()
        print(f"[同步后] Position 表共 {after_total} 条,其中富途证券 {after_futu} 条\n")

        futu_positions = db.query(BusinessPosition).filter_by(platform="富途证券").all()
        print("富途证券持仓明细:")
        print(f"{'ticker':<14}{'name':<24}{'qty':>8}{'mv_cny':>14}{'asset_class':>10}")
        print("-" * 72)
        for p in futu_positions:
            print(
                f"{p.ticker:<14}{(p.name or '')[:22]:<24}"
                f"{p.quantity:>8}{p.market_value_cny:>14.2f}"
                f"{p.asset_class:>10}"
            )

        # 验证跨平台合并（老虎+富途同 ticker）
        ticker_platforms = defaultdict(set)
        for p in db.query(BusinessPosition).all():
            if p.ticker:
                ticker_platforms[p.ticker].add(p.platform)
        multi = {t: pl for t, pl in ticker_platforms.items() if len(pl) > 1}
        if multi:
            print(f"\n🎯 跨平台同 ticker 持仓（{len(multi)} 只）— aggregate 合并逻辑首次被真实触发:")
            for ticker, platforms in multi.items():
                rows = db.query(BusinessPosition).filter_by(ticker=ticker).all()
                total_mv = sum(r.market_value_cny or 0 for r in rows)
                print(f"  {ticker}: {' + '.join(platforms)} → 合并市值 {total_mv:,.0f} CNY")
        else:
            print("\n跨平台同 ticker: 0 只")

    finally:
        service.close()
        db.close()


if __name__ == "__main__":
    main()
