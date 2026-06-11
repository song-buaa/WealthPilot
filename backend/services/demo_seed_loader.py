"""
Demo 种子数据加载器 — 启动时从 CSV 导入种子持仓到 DB。

仅在 PUBLIC_DEMO_MODE 下执行。如果 DB 已有持仓数据则跳过（不覆盖）。
"""
import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SEED_CSV = Path(__file__).parent.parent.parent / "demo_seed" / "demo_seed_positions.csv"


def load_seed_positions_if_empty(portfolio_id: int = 1) -> int:
    """如果 DB 持仓为空，从种子 CSV 导入。返回导入条数。"""
    from backend.core.demo_mode import PUBLIC_DEMO_MODE
    if not PUBLIC_DEMO_MODE:
        return 0

    from app.models import Position, get_session

    session = get_session()
    try:
        existing = session.query(Position).filter_by(portfolio_id=portfolio_id).count()
        if existing > 0:
            logger.info(f"[demo_seed] DB 已有 {existing} 条持仓，跳过种子导入")
            return 0

        if not _SEED_CSV.exists():
            logger.warning(f"[demo_seed] 种子 CSV 不存在: {_SEED_CSV}")
            return 0

        count = 0
        with open(_SEED_CSV, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pos = Position(
                    portfolio_id=portfolio_id,
                    name=row["name"],
                    ticker=row["ticker"],
                    platform=row["platform"],
                    asset_class=row["asset_class"],
                    currency=row.get("currency", "CNY"),
                    quantity=float(row.get("quantity", 0)) if row.get("quantity") else None,
                    cost_price=float(row.get("cost_price", 0)) if row.get("cost_price") else None,
                    current_price=float(row.get("current_price", 0)) if row.get("current_price") else None,
                    market_value_cny=float(row.get("market_value_cny", 0)),
                    original_currency=row.get("original_currency"),
                    original_value=float(row.get("original_value", 0)) if row.get("original_value") else None,
                    fx_rate_to_cny=float(row.get("fx_rate_to_cny", 1.0)) if row.get("fx_rate_to_cny") else None,
                    profit_loss_value=float(row.get("profit_loss_value", 0)) if row.get("profit_loss_value") else None,
                    profit_loss_rate=float(row.get("profit_loss_rate", 0)) if row.get("profit_loss_rate") else None,
                    segment=row.get("segment", "投资"),
                )
                session.add(pos)
                count += 1

        session.commit()
        logger.info(f"[demo_seed] 导入 {count} 条种子持仓")
        return count

    except Exception as e:
        session.rollback()
        logger.error(f"[demo_seed] 种子导入失败: {e}")
        return 0
    finally:
        session.close()


def load_seed_profile_if_empty() -> bool:
    """如果 DB 无用户画像，插入演示用虚构画像。返回是否插入。"""
    from backend.core.demo_mode import PUBLIC_DEMO_MODE
    if not PUBLIC_DEMO_MODE:
        return False

    from app.models import get_session
    from app.models import UserProfile

    session = get_session()
    try:
        if session.query(UserProfile).count() > 0:
            return False

        profile = UserProfile(
            risk_source="demo",
            risk_provider="演示数据",
            risk_original_level="C4",
            risk_normalized_level=4,
            risk_type="积极型",
            income_level="中高",
            income_stability="稳定",
            total_assets="100-500万",
            investable_ratio="50-70%",
            liability_level="低",
            family_status="已婚有子女",
            asset_structure="股票为主",
            investment_motivation="资产增值",
            fund_usage_timeline="3年以上",
            max_drawdown="15-30%",
            target_return="10-20%",
        )
        session.add(profile)
        session.commit()
        logger.info("[demo_seed] 插入演示用户画像")
        return True
    except Exception as e:
        session.rollback()
        logger.error(f"[demo_seed] 用户画像种子失败: {e}")
        return False
    finally:
        session.close()
