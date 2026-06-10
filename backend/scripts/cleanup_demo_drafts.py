"""
清理演示模式产生的 draft 数据。

用法: python -m backend.scripts.cleanup_demo_drafts [--days 7] [--dry-run]

只清理 source_decision_ref 以 "demo" 开头 且 plan_status='draft' 的记录。
已确认(active/completed)的记录不动(不应该有,因为 demo 模式拦了 confirm)。
"""
import argparse
import logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def cleanup(days: int = 7, dry_run: bool = False):
    from app.database import get_session
    from backend.services.execution_plan.models import ExecutionPlan, ExecutionTranche

    session = get_session()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        plans = (
            session.query(ExecutionPlan)
            .filter(
                ExecutionPlan.source_decision_ref.like("demo%"),
                ExecutionPlan.plan_status == "draft",
                ExecutionPlan.created_at < cutoff,
            )
            .all()
        )

        if not plans:
            logger.info("没有需要清理的 demo draft (>%d 天)", days)
            return 0

        plan_ids = [p.id for p in plans]
        logger.info("发现 %d 条 demo draft (>%d 天):", len(plans), days)
        for p in plans:
            logger.info("  %s  %s %s  created=%s", p.id, p.symbol, p.side, p.created_at)

        if dry_run:
            logger.info("[dry-run] 不执行删除")
            return len(plans)

        # 先删 tranches
        deleted_tranches = (
            session.query(ExecutionTranche)
            .filter(ExecutionTranche.plan_id.in_(plan_ids))
            .delete(synchronize_session=False)
        )
        # 再删 plans
        deleted_plans = (
            session.query(ExecutionPlan)
            .filter(ExecutionPlan.id.in_(plan_ids))
            .delete(synchronize_session=False)
        )
        session.commit()
        logger.info("已清理 %d plans + %d tranches", deleted_plans, deleted_tranches)
        return deleted_plans

    except Exception as e:
        session.rollback()
        logger.error("清理失败: %s", e)
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="清理演示模式 demo draft")
    parser.add_argument("--days", type=int, default=7, help="清理超过 N 天的 demo draft (默认 7)")
    parser.add_argument("--dry-run", action="store_true", help="只打印不删除")
    args = parser.parse_args()
    cleanup(days=args.days, dry_run=args.dry_run)
