"""
WealthPilot - 真实数据写入脚本

将用户的实际持仓和负债数据写入数据库，替换示例数据。
运行方法（从项目根目录）：
    python scripts/populate_real_data.py

注意：此脚本会清空已有数据后重新写入。
"""

import sys
import os

# 确保能 import app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import init_db, get_session
from app.models import Portfolio, Position, Liability


# USD → CNY 汇率（基于老虎证券截图：928,889.90 CNH ÷ 134,528.14 USD）
USD_CNY = 6.90
HKD_CNY = 0.92  # 港股用 HKD，近似汇率


def main():
    init_db()
    session = get_session()
    try:
        # ── 清空旧数据 ──────────────────────────────────
        session.query(Liability).delete()
        session.query(Position).delete()
        session.query(Portfolio).delete()
        session.commit()

        # ── 创建投资组合 ────────────────────────────────
        portfolio = Portfolio(
            name="我的投资组合",
            # 权益约束: 40%~80%；其余不设约束
            min_equity_pct=40.0,
            max_equity_pct=80.0,
            min_fixed_income_pct=0.0,
            max_fixed_income_pct=100.0,
            min_cash_pct=0.0,
            max_cash_pct=100.0,
            min_alternative_pct=0.0,
            max_alternative_pct=100.0,
            max_single_stock_pct=15.0,
            max_leverage_ratio=80.0,
        )
        session.add(portfolio)
        session.flush()  # 获取 portfolio.id

        pid = portfolio.id

        # ── 持仓数据 ────────────────────────────────────
        # 老虎证券（港美股券商）
        # 总市值 CNH: 928,889.90（含现金），换算 CNY 用 USD_CNY=6.90
        positions = [
            # ── 老虎证券 ──
            Position(portfolio_id=pid, name="苹果 AAPL",          ticker="AAPL",    platform="港美股券商", asset_class="权益", currency="USD", quantity=60,   cost_price=180.0, current_price=249.52, market_value_cny=103_401),
            Position(portfolio_id=pid, name="理想汽车 LI",         ticker="LI",      platform="港美股券商", asset_class="权益", currency="USD", quantity=2500, cost_price=18.0,  current_price=17.33,  market_value_cny=298_943),
            Position(portfolio_id=pid, name="Meta META",           ticker="META",    platform="港美股券商", asset_class="权益", currency="USD", quantity=27,   cost_price=380.0, current_price=611.22, market_value_cny=113_922),
            Position(portfolio_id=pid, name="特斯拉 TSLA",         ticker="TSLA",    platform="港美股券商", asset_class="权益", currency="USD", quantity=32,   cost_price=250.0, current_price=389.67, market_value_cny=86_068),
            Position(portfolio_id=pid, name="拼多多 PDD",          ticker="PDD",     platform="港美股券商", asset_class="权益", currency="USD", quantity=150,  cost_price=95.0,  current_price=102.50, market_value_cny=106_088),
            Position(portfolio_id=pid, name="谷歌 GOOG",           ticker="GOOG",    platform="港美股券商", asset_class="权益", currency="USD", quantity=18,   cost_price=170.0, current_price=300.72, market_value_cny=37_384),
            Position(portfolio_id=pid, name="伯克希尔B BRK.B",    ticker="BRK.B",   platform="港美股券商", asset_class="权益", currency="USD", quantity=20,   cost_price=380.0, current_price=489.27, market_value_cny=67_620),
            Position(portfolio_id=pid, name="QQQ",                 ticker="QQQ",     platform="港美股券商", asset_class="权益", currency="USD", quantity=10,   cost_price=420.0, current_price=485.33, market_value_cny=40_947),
            Position(portfolio_id=pid, name="微软 MSFT",           ticker="MSFT",    platform="港美股券商", asset_class="权益", currency="USD", quantity=12,   cost_price=350.0, current_price=394.62, market_value_cny=32_714),
            Position(portfolio_id=pid, name="Coinbase COIN",       ticker="COIN",    platform="港美股券商", asset_class="权益", currency="USD", quantity=16,   cost_price=180.0, current_price=194.64, market_value_cny=21_473),
            Position(portfolio_id=pid, name="Hims HIMS",           ticker="HIMS",    platform="港美股券商", asset_class="权益", currency="USD", quantity=100,  cost_price=20.0,  current_price=24.54,  market_value_cny=16_933),
            Position(portfolio_id=pid, name="SHY",                 ticker="SHY",     platform="港美股券商", asset_class="固收", currency="USD", quantity=5,    cost_price=82.0,  current_price=82.68,  market_value_cny=2_852),
            Position(portfolio_id=pid, name="VONTOBEL债券基金",    ticker="",        platform="港美股券商", asset_class="固收", currency="USD", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=58_111),
            Position(portfolio_id=pid, name="安本标准债券基金",    ticker="",        platform="港美股券商", asset_class="固收", currency="USD", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=23_570),
            Position(portfolio_id=pid, name="平安货币基金",        ticker="",        platform="港美股券商", asset_class="现金", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=368),

            # ── 富途证券 ──
            Position(portfolio_id=pid, name="微软 MSFT",           ticker="MSFT",    platform="港美股券商", asset_class="权益", currency="USD", quantity=5,    cost_price=350.0, current_price=394.62, market_value_cny=2_013),
            Position(portfolio_id=pid, name="拼多多 PDD",          ticker="PDD",     platform="港美股券商", asset_class="权益", currency="USD", quantity=1,    cost_price=95.0,  current_price=102.50, market_value_cny=102),
            Position(portfolio_id=pid, name="QQQ",                 ticker="QQQ",     platform="港美股券商", asset_class="权益", currency="USD", quantity=1,    cost_price=420.0, current_price=485.33, market_value_cny=598),

            # ── 雪盈证券 ──
            Position(portfolio_id=pid, name="理想汽车 LI",         ticker="LI",      platform="港美股券商", asset_class="权益", currency="USD", quantity=300,  cost_price=18.0,  current_price=17.33,  market_value_cny=35_943),

            # ── 国金证券（港股，HKD）──
            Position(portfolio_id=pid, name="美团-W 03690",        ticker="03690.HK", platform="境内券商", asset_class="权益", currency="HKD", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=166_805),
            Position(portfolio_id=pid, name="理想汽车-W 02015(大)", ticker="02015.HK", platform="境内券商", asset_class="权益", currency="HKD", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=137_195),
            Position(portfolio_id=pid, name="理想汽车-W 02015(小)", ticker="02015.HK", platform="境内券商", asset_class="权益", currency="HKD", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=5_965),
            Position(portfolio_id=pid, name="国金现金",            ticker="",         platform="境内券商", asset_class="现金", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=177),

            # ── 招商银行 ──
            Position(portfolio_id=pid, name="朝朝宝",              ticker="",        platform="银行",     asset_class="现金", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=2_285),
            Position(portfolio_id=pid, name="招行理财",            ticker="",        platform="银行",     asset_class="固收", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=1_136),
            Position(portfolio_id=pid, name="招行基金",            ticker="",        platform="银行",     asset_class="权益", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=698),
            Position(portfolio_id=pid, name="招行黄金",            ticker="",        platform="银行",     asset_class="另类", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=28),

            # ── 建设银行 ──
            Position(portfolio_id=pid, name="建行活期",            ticker="",        platform="银行",     asset_class="现金", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=7),
            Position(portfolio_id=pid, name="建行理财",            ticker="",        platform="银行",     asset_class="固收", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=20),
            Position(portfolio_id=pid, name="建行基金",            ticker="",        platform="银行",     asset_class="权益", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=757),
            Position(portfolio_id=pid, name="建行债券",            ticker="",        platform="银行",     asset_class="固收", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=103),
            Position(portfolio_id=pid, name="个人养老金",          ticker="",        platform="银行",     asset_class="另类", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=13_796),

            # ── 支付宝 ──
            Position(portfolio_id=pid, name="余额宝",              ticker="",        platform="支付宝",   asset_class="现金", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=199),
            Position(portfolio_id=pid, name="稳健理财",            ticker="",        platform="支付宝",   asset_class="固收", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=535),
            Position(portfolio_id=pid, name="进阶理财",            ticker="",        platform="支付宝",   asset_class="权益", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=460),
            Position(portfolio_id=pid, name="支付宝养老金",        ticker="",        platform="支付宝",   asset_class="另类", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=22),

            # ── 其他 ──
            Position(portfolio_id=pid, name="住房公积金（杭州）",  ticker="",        platform="其他",     asset_class="另类", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=359_522),
            Position(portfolio_id=pid, name="企业年金（建行）",    ticker="",        platform="其他",     asset_class="另类", currency="CNY", quantity=0,    cost_price=0.0,   current_price=0.0,    market_value_cny=161_012),
        ]

        # ── 负债数据 ────────────────────────────────────
        liabilities = [
            Liability(portfolio_id=pid, name="招行信用卡",      category="信用卡", amount=5_169,   interest_rate=0.0),
            Liability(portfolio_id=pid, name="招行闪电贷",      category="信用贷", amount=10_000,  interest_rate=3.6),
            Liability(portfolio_id=pid, name="建行信用卡",      category="信用卡", amount=162,     interest_rate=0.0),
            Liability(portfolio_id=pid, name="建行快贷",        category="信用贷", amount=140_900, interest_rate=3.05),
            Liability(portfolio_id=pid, name="农行网捷贷",      category="信用贷", amount=300_000, interest_rate=3.0),
            Liability(portfolio_id=pid, name="南京银行信易贷",  category="信用贷", amount=365_000, interest_rate=3.6),
            Liability(portfolio_id=pid, name="北京银行京e贷",   category="信用贷", amount=500_000, interest_rate=5.7),
            Liability(portfolio_id=pid, name="萧山农商浙里贷",  category="信用贷", amount=210_000, interest_rate=5.0),
        ]

        session.add_all(positions)
        session.add_all(liabilities)
        session.commit()

        # ── 统计汇报 ────────────────────────────────────
        total_assets = sum(p.market_value_cny for p in positions)
        total_liabilities = sum(l.amount for l in liabilities)
        net_worth = total_assets - total_liabilities
        leverage = total_liabilities / total_assets * 100 if total_assets > 0 else 0

        print(f"写入完成！")
        print(f"  持仓数量: {len(positions)} 条")
        print(f"  负债数量: {len(liabilities)} 条")
        print(f"  总资产:   ¥{total_assets:,.0f}")
        print(f"  总负债:   ¥{total_liabilities:,.0f}")
        print(f"  净资产:   ¥{net_worth:,.0f}")
        print(f"  杠杆率:   {leverage:.1f}%")

    except Exception as e:
        session.rollback()
        print(f"写入失败: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
