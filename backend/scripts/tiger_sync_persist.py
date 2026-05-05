"""
老虎持仓同步落库端到端验证（会真实写入数据库）。

运行: cd backend && python -m scripts.tiger_sync_persist
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

from app.database import get_session, init_db
from services.broker_sync.models import PositionSnapshotRun, PositionSnapshot
from services.broker_sync.tiger.sync_service import TigerSyncService


def main():
    print("=" * 80)
    print("老虎持仓同步落库验证（真实写入数据库）")
    print("=" * 80)

    # 确保表已创建
    init_db()

    service = TigerSyncService()
    db = get_session()

    try:
        run_id = service.sync_and_persist(db, triggered_by="manual")
        print(f"\n✅ 同步成功,run_id = {run_id}")

        # 查询并展示
        run = db.get(PositionSnapshotRun, run_id)
        print(f"\nRun 信息:")
        print(f"  broker:      {run.broker}")
        print(f"  account:     {run.account_id}")
        print(f"  status:      {run.status}")
        print(f"  started:     {run.started_at}")
        print(f"  finished:    {run.finished_at}")
        print(f"  count:       {run.position_count}")
        print(f"  retry_count: {run.retry_count}")

        print(f"\n持仓快照（{len(run.snapshots)} 条）:")
        print(f"{'symbol':<14}{'name':<22}{'qty':>8}{'mkt_value':>14}{'pnl_pct':>10}")
        print("-" * 70)
        for snap in run.snapshots:
            print(
                f"{snap.symbol:<14}{snap.name[:20]:<22}"
                f"{snap.quantity:>8}{snap.market_value:>14}"
                f"{snap.unrealized_pnl_pct:>10.4f}"
            )

        print(f"\n数据库统计:")
        total_runs = db.query(PositionSnapshotRun).count()
        total_snapshots = db.query(PositionSnapshot).count()
        print(f"  position_snapshot_runs 总数: {total_runs}")
        print(f"  position_snapshots 总数:    {total_snapshots}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
