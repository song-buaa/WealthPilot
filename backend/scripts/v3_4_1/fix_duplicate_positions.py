"""修复 positions 表重复行（Symbol 标准化遗留 bug）。

两类重复：
A. Symbol 格式差异（同 name + 同 platform，ticker 不同）
   如 BRK vs BRK.B、00068 vs 0068
   → 保留 id 较大的（新格式），删除 id 较小的

B. 同平台同 ticker 真重复（同 name + 同 platform + 同 ticker）
   如华泰紫金支付宝 x2
   → 保留 id 较大的，删除 id 较小的

跨平台双行（同 name 不同 platform）不处理（合理持仓）。

用法：
    python backend/scripts/v3_4_1/fix_duplicate_positions.py --dry-run
    python backend/scripts/v3_4_1/fix_duplicate_positions.py
"""
import os
import sys
import argparse
from collections import defaultdict

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


def main(dry_run: bool = False):
    from app.database import get_session
    from app.models import Position

    db = get_session()
    try:
        all_positions = db.query(Position).all()

        # 按 (name, platform) 分组
        groups: dict[tuple[str, str], list] = defaultdict(list)
        for p in all_positions:
            groups[(p.name, p.platform)].append(p)

        total_deleted = 0
        total_normalized = 0

        for (name, platform), positions in sorted(groups.items()):
            if len(positions) <= 1:
                continue

            # 保留 id 最大的，删除其余
            keeper = max(positions, key=lambda p: p.id)
            to_delete = [p for p in positions if p.id != keeper.id]

            # 港股 ticker 标准化（zfill(4)）
            new_ticker = keeper.ticker
            if keeper.currency == "HKD" and keeper.ticker and keeper.ticker.isdigit():
                new_ticker = keeper.ticker.zfill(4)

            ticker_changed = new_ticker != keeper.ticker
            tickers_str = " / ".join(f"{p.ticker}(id={p.id})" for p in positions)

            print(f"\n[{name}] platform={platform} tickers=[{tickers_str}]")
            print(f"  保留 id={keeper.id} ticker={keeper.ticker}" +
                  (f" → {new_ticker}" if ticker_changed else ""))
            for p in to_delete:
                print(f"  删除 id={p.id} ticker={p.ticker} qty={p.quantity} mv_cny={p.market_value_cny:.2f}")

            if not dry_run:
                if ticker_changed:
                    keeper.ticker = new_ticker
                    total_normalized += 1
                for p in to_delete:
                    db.delete(p)
                    total_deleted += 1

        if dry_run:
            db.rollback()
            print(f"\n[DRY-RUN] 预计删除 {sum(len(ps) - 1 for ps in groups.values() if len(ps) > 1)} 行，"
                  f"涉及 {sum(1 for ps in groups.values() if len(ps) > 1)} 组重复")
        else:
            db.commit()
            print(f"\n实际删除 {total_deleted} 行，标准化 {total_normalized} 个 ticker")

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
