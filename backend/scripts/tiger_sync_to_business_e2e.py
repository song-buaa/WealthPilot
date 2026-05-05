"""
端到端验证:老虎 API → snapshot → Position 业务表完整链路。

执行前提:
- 已经跑过 backend/scripts/archive_legacy_tiger_positions.py(归档旧数据)

运行: cd backend && python -m scripts.tiger_sync_to_business_e2e
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
from services.broker_sync.tiger.sync_service import TigerSyncService


def main():
    print("=" * 80)
    print("老虎 API → snapshot → Position 业务表 端到端验证")
    print("=" * 80)

    init_db()
    service = TigerSyncService()
    db = get_session()

    try:
        # 同步前快照
        before_count = db.query(BusinessPosition).filter_by(platform="老虎证券").count()
        before_total_count = db.query(BusinessPosition).count()
        print(f"\n[同步前] Position 表共 {before_total_count} 条,其中老虎证券 {before_count} 条\n")

        # 执行同步
        run_id = service.sync_and_persist(db, triggered_by="manual")
        print(f"✅ 同步成功 run_id={run_id}\n")

        # 同步后快照
        after_count = db.query(BusinessPosition).filter_by(platform="老虎证券").count()
        after_total_count = db.query(BusinessPosition).count()
        print(f"[同步后] Position 表共 {after_total_count} 条,其中老虎证券 {after_count} 条\n")

        # 展示老虎证券的所有持仓
        tiger_positions = db.query(BusinessPosition).filter_by(platform="老虎证券").all()
        print(f"老虎证券持仓明细:")
        print(f"{'ticker':<12}{'name':<22}{'qty':>8}{'mv_cny':>14}{'mv_orig':>14}{'fx':>8}")
        print("-" * 80)
        total_cny = 0
        for p in tiger_positions:
            total_cny += p.market_value_cny or 0
            print(
                f"{p.ticker:<12}{(p.name or '')[:20]:<22}"
                f"{p.quantity:>8}{p.market_value_cny:>14.2f}"
                f"{p.original_value or 0:>14.2f}{p.fx_rate_to_cny:>8.4f}"
            )
        print(f"\n老虎证券总市值: {total_cny:,.2f} CNY")

        # 验证跨平台同 ticker 情况
        ticker_count = defaultdict(set)
        for p in db.query(BusinessPosition).all():
            if p.ticker:
                ticker_count[p.ticker].add(p.platform)
        multi_platform = {t: pl for t, pl in ticker_count.items() if len(pl) > 1}
        print(f"\n跨平台同 ticker:{len(multi_platform)} 只 (本次只跑老虎,预期 0 只)")

    finally:
        db.close()


if __name__ == "__main__":
    main()
