"""
一次性脚本:归档 Position 表里的老虎旧数据。

执行后:
1. 创建 positions_archive_<YYYYMMDD> 表,内容是当前 platform='老虎证券' 的所有行
2. 从 positions 表删除 platform='老虎证券' 的所有行

幂等性:CREATE TABLE IF NOT EXISTS + WHERE 过滤,可重复执行。
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

from sqlalchemy import text
from app.database import get_session


PLATFORM = "老虎证券"


def main():
    archive_table = f"positions_archive_{datetime.now().strftime('%Y%m%d')}"
    db = get_session()

    try:
        # 1. 先看有多少条要归档
        count = db.execute(
            text("SELECT COUNT(*) FROM positions WHERE platform = :p"),
            {"p": PLATFORM},
        ).scalar()
        print(f"准备归档 platform='{PLATFORM}' 的旧数据,共 {count} 条")

        if count == 0:
            print("无数据需归档,退出。")
            return

        # 2. 创建归档表(幂等)
        db.execute(text(
            f"CREATE TABLE IF NOT EXISTS {archive_table} AS "
            f"SELECT * FROM positions WHERE 1=0"
        ))

        # 3. 复制数据到归档表
        db.execute(text(f"DELETE FROM {archive_table}"))
        db.execute(text(
            f"INSERT INTO {archive_table} SELECT * FROM positions WHERE platform = :p"
        ), {"p": PLATFORM})

        # 4. 验证归档完整性
        archived_count = db.execute(text(f"SELECT COUNT(*) FROM {archive_table}")).scalar()
        if archived_count != count:
            db.rollback()
            raise RuntimeError(
                f"归档完整性校验失败:原表 {count} 条,归档表 {archived_count} 条"
            )

        # 5. 从原表删除
        db.execute(
            text("DELETE FROM positions WHERE platform = :p"),
            {"p": PLATFORM},
        )

        db.commit()

        # 6. 输出报告
        remaining = db.execute(text("SELECT COUNT(*) FROM positions")).scalar()
        print(f"\n✅ 归档完成")
        print(f"  归档表: {archive_table} ({archived_count} 条)")
        print(f"  原表 positions 现剩 {remaining} 条记录")

    except Exception as e:
        db.rollback()
        print(f"❌ 归档失败: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
