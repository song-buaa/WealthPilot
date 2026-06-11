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
            risk_type="成长型",
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
            goal_type='["长期资产增值", "子女教育金储备"]',
            investment_horizon="3年以上",
            ai_summary="您属于成长型投资者，风险承受力中高（R4），以长期资产增值为主要目标，兼顾子女教育金储备。当前组合以权益类资产为主，配置集中于中美优质科技与消费标的，辅以固收和货币类资产做安全垫。建议维持3年以上投资视野，控制单标的集中度，逐步优化固收配比。",
            ai_style="成长",
            ai_confidence="high",
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


def load_seed_liability_if_empty(portfolio_id: int = 1) -> bool:
    """如果 DB 无负债，插入演示用融资融券负债。"""
    from backend.core.demo_mode import PUBLIC_DEMO_MODE
    if not PUBLIC_DEMO_MODE:
        return False

    from app.models import get_session, Liability

    session = get_session()
    try:
        if session.query(Liability).filter_by(portfolio_id=portfolio_id).count() > 0:
            return False

        liability = Liability(
            portfolio_id=portfolio_id,
            name="融资融券负债",
            category="信用贷",
            amount=50000.0,
            interest_rate=5.5,
            purpose="投资杠杆",
        )
        session.add(liability)
        session.commit()
        logger.info("[demo_seed] 插入演示负债 ¥50,000")
        return True
    except Exception as e:
        session.rollback()
        logger.error(f"[demo_seed] 负债种子失败: {e}")
        return False
    finally:
        session.close()


def load_seed_research_docs_if_empty() -> bool:
    """如果 DB 无已导入资料，插入演示用研报摘要。"""
    from backend.core.demo_mode import PUBLIC_DEMO_MODE
    if not PUBLIC_DEMO_MODE:
        return False

    from app.database import get_session
    from datetime import datetime
    from sqlalchemy import text

    session = get_session()
    try:
        count = session.execute(text("SELECT COUNT(*) FROM research_documents")).scalar()
        if count > 0:
            return False

        docs = [
            {
                "title": "腾讯控股(00700.HK) 2026年一季度业绩前瞻",
                "source_type": "user_upload",
                "content": "腾讯2025年全年营收同比增长约8%，其中游戏业务受益于海外发行放量，广告业务受视频号商业化驱动保持双位数增长。2026Q1预计营收约1,700亿元，同比增长约9%。利润端受益于降本增效持续推进，Non-IFRS净利润率有望维持在30%以上。关注要点：① 微信生态商业化节奏（视频号电商GMV增速）；② 海外游戏pipeline（特别是东南亚市场的增量贡献）；③ AI大模型在企业服务板块的落地进展及对云收入的拉动。估值方面，当前PE约22倍，处于近三年中枢偏下水平，若业绩符合预期，估值存在修复空间。",
            },
            {
                "title": "Apple(AAPL) AI 生态战略及对硬件周期的影响",
                "source_type": "user_upload",
                "content": "Apple Intelligence 于2025年下半年正式上线，覆盖iPhone/iPad/Mac全线设备，但初期功能以端侧推理为主，云端能力仍在迭代。核心观察：① 新一代Siri升级为对话式AI助手，用户活跃度数据尚未披露；② AI功能驱动的换机需求目前温和，2026财年iPhone出货量预计同比增长约5%，不及市场此前10%+的乐观预期；③ 服务收入（App Store + Apple Music + iCloud）受益于AI功能附加订阅，年化增速有望维持在15%左右。风险点在于欧盟DMA合规要求可能限制AI功能的部分分发模式，以及Epic诉讼对App Store抽成模式的潜在冲击。当前PE约35倍，反映了市场对AI生态的溢价预期。",
            },
        ]

        for doc in docs:
            session.execute(text(
                "INSERT INTO research_documents (title, source_type, content, created_at) VALUES (:title, :source_type, :content, :created_at)"
            ), {"title": doc["title"], "source_type": doc["source_type"], "content": doc["content"], "created_at": datetime.now().isoformat()})

        session.commit()
        logger.info("[demo_seed] 插入 2 份演示已导入资料")
        return True
    except Exception as e:
        session.rollback()
        logger.error(f"[demo_seed] 已导入资料种子失败: {e}")
        return False
    finally:
        session.close()
