"""
v3.4 数据迁移: 修复 positions 表 currency 字段。

KNOWN_ISSUE #1: 港股/美股 positions 的 currency 被硬编码为 CNY,
应从 original_currency 字段恢复原币种。

幂等: 跑多次结果一致(只修 currency != original_currency 的行)。

用法: conda activate wealthpilot && python backend/scripts/v3_4/fix_position_currency.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from app.database import get_session
from app.models import Position


def migrate():
    session = get_session()
    try:
        # 找所有 original_currency 非空且与 currency 不一致的行
        positions = session.query(Position).filter(
            Position.original_currency.isnot(None),
            Position.original_currency != "",
            Position.currency != Position.original_currency,
        ).all()

        if not positions:
            print("无需迁移(所有 currency 已与 original_currency 一致)")
            return 0

        count = 0
        for p in positions:
            old = p.currency
            p.currency = p.original_currency
            print(f"  [{p.id}] {p.ticker} ({p.platform}): {old} -> {p.original_currency}")
            count += 1

        session.commit()
        print(f"\n迁移完成: {count} 条记录")
        return count
    except Exception as e:
        session.rollback()
        print(f"迁移失败: {e}")
        return -1
    finally:
        session.close()


if __name__ == "__main__":
    print("=== v3.4 positions currency 修复 ===")
    migrate()
