"""
Symbol 标准化迁移脚本：填充 positions.symbol_v2 字段。

根据 positions 表中 ticker + currency 推断标准 symbol (TICKER:MARKET)，
写入已预留的 symbol_v2 字段。

特性：
- 幂等：已有 symbol_v2 值的行默认不覆盖（--force 强制覆盖）
- 安全：只更新 symbol_v2 字段，不改其他字段
- 报告：输出迁移统计（填充数、跳过数、无法推断数）

用法：
    python backend/scripts/symbol_standardization/migrate_positions_symbol_v2.py
    python backend/scripts/symbol_standardization/migrate_positions_symbol_v2.py --force
    python backend/scripts/symbol_standardization/migrate_positions_symbol_v2.py --dry-run
"""

import argparse
import os
import sys

# 确保 backend/ 在 sys.path
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from utils.symbol import infer_symbol_from_ticker


def find_db_path() -> str:
    """查找 wealthpilot.db 路径。"""
    candidates = [
        os.path.join(BACKEND_DIR, "..", "data", "wealthpilot.db"),
        os.path.join(BACKEND_DIR, "wealthpilot.db"),
    ]
    for p in candidates:
        full = os.path.abspath(p)
        if os.path.exists(full):
            return full
    raise FileNotFoundError(
        f"找不到 wealthpilot.db，搜索路径: {[os.path.abspath(c) for c in candidates]}"
    )


def migrate(force: bool = False, dry_run: bool = False):
    import sqlite3

    db_path = find_db_path()
    print(f"数据库: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 读取所有 positions
    rows = cursor.execute(
        "SELECT id, ticker, currency, name, symbol_v2 FROM positions"
    ).fetchall()

    stats = {
        "total": len(rows),
        "filled": 0,
        "skipped_existing": 0,
        "skipped_no_ticker": 0,
        "skipped_cannot_infer": 0,
    }
    migration_log = []

    for row in rows:
        pid = row["id"]
        ticker = row["ticker"] or ""
        currency = row["currency"] or ""
        name = row["name"] or ""
        existing_v2 = row["symbol_v2"]

        # 跳过已有值（除非 --force）
        if existing_v2 and not force:
            stats["skipped_existing"] += 1
            continue

        # 跳过无 ticker
        if not ticker.strip():
            stats["skipped_no_ticker"] += 1
            migration_log.append(
                f"  SKIP id={pid} name={name!r}: 无 ticker"
            )
            continue

        # 推断 symbol
        symbol_v2 = infer_symbol_from_ticker(ticker, currency)

        if symbol_v2 is None:
            stats["skipped_cannot_infer"] += 1
            migration_log.append(
                f"  SKIP id={pid} ticker={ticker!r} currency={currency!r} "
                f"name={name!r}: 无法推断"
            )
            continue

        # 填充
        stats["filled"] += 1
        action = "DRY-RUN" if dry_run else "UPDATE"
        migration_log.append(
            f"  {action} id={pid} ticker={ticker!r} → symbol_v2={symbol_v2!r}"
        )
        if not dry_run:
            cursor.execute(
                "UPDATE positions SET symbol_v2 = ? WHERE id = ?",
                (symbol_v2, pid),
            )

    if not dry_run:
        conn.commit()
    conn.close()

    # 输出报告
    print(f"\n{'=' * 50}")
    print(f"迁移{'预览 (dry-run)' if dry_run else '完成'}")
    print(f"{'=' * 50}")
    print(f"总行数:         {stats['total']}")
    print(f"已填充:         {stats['filled']}")
    print(f"跳过(已有值):   {stats['skipped_existing']}")
    print(f"跳过(无ticker): {stats['skipped_no_ticker']}")
    print(f"跳过(无法推断): {stats['skipped_cannot_infer']}")
    print()

    if migration_log:
        print("详细日志:")
        for line in migration_log:
            print(line)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="填充 positions.symbol_v2 字段")
    parser.add_argument(
        "--force", action="store_true", help="覆盖已有 symbol_v2 值"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="预览模式，不实际修改数据库"
    )
    args = parser.parse_args()
    migrate(force=args.force, dry_run=args.dry_run)
