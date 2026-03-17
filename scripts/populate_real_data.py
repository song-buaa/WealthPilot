"""
WealthPilot - 真实数据写入脚本

将用户的实际持仓和负债数据写入数据库，替换示例数据。
运行方法（从项目根目录）：
    python scripts/populate_real_data.py

注意：此脚本会清空已有数据后重新写入。
在脚本开头一次性获取 USD/CNY 和 HKD/CNY 的当前汇率，
所有计算使用这两个值，存储时仍保存 fx_rate_to_cny 和 fx_rate_date。
"""

import sys
import os

# 确保能 import app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import init_db, get_session, get_engine, Base
from app.models import Portfolio, Position, Liability
from app.fx_service import fx_service


def main():
    # ── 一次性获取汇率 ───────────────────────────────────────────────
    print("正在获取最新汇率...")
    usd_rate, usd_date = fx_service._get_rate_with_date("USD", "CNY", "latest")
    hkd_rate, hkd_date = fx_service._get_rate_with_date("HKD", "CNY", "latest")
    print(f"  USD/CNY = {usd_rate:.4f} ({usd_date})")
    print(f"  HKD/CNY = {hkd_rate:.4f} ({hkd_date})")

    # ── 重建数据库（drop_all + create_all 确保新字段生效）──────────────
    print("重建数据库表结构...")
    from app import models as _models  # noqa: F401 触发所有 Model 注册
    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())

    session = get_session()
    try:

        # ── 创建投资组合 ─────────────────────────────────────────────
        portfolio = Portfolio(
            name="我的投资组合",
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
        session.flush()
        pid = portfolio.id

        # 辅助函数：根据 original_currency 计算 cny 市值
        def cny(orig_val, orig_cur="CNY"):
            if orig_cur == "USD":
                return round(orig_val * usd_rate)
            elif orig_cur == "HKD":
                return round(orig_val * hkd_rate)
            else:
                return round(orig_val)

        def pos(name, ticker, platform, asset_class, segment,
                orig_cur, orig_val, quantity=0,
                pnl_value=0, pnl_orig=0, pnl_rate=0, market_cny_override=None):
            """创建 Position 对象的辅助函数"""
            market_val = market_cny_override if market_cny_override is not None else cny(orig_val, orig_cur)
            fx = usd_rate if orig_cur == "USD" else (hkd_rate if orig_cur == "HKD" else 1.0)
            fx_date = usd_date if orig_cur == "USD" else (hkd_date if orig_cur == "HKD" else "N/A")
            return Position(
                portfolio_id=pid,
                name=name,
                ticker=ticker or "",
                platform=platform,
                asset_class=asset_class,
                currency=orig_cur,
                quantity=quantity,
                cost_price=0.0,
                current_price=0.0,
                market_value_cny=market_val,
                original_currency=orig_cur,
                original_value=orig_val,
                fx_rate_to_cny=fx,
                fx_rate_date=fx_date,
                segment=segment,
                profit_loss_value=pnl_value,
                profit_loss_rate=pnl_rate,
                profit_loss_original_value=pnl_orig,
            )

        # ── 持仓数据 ─────────────────────────────────────────────────
        positions = [
            # ── 国金证券（港股 HKD）──
            pos("美团-W 03690",          "03690.HK", "国金证券", "权益",  "投资", "HKD", 181310, market_cny_override=159671, pnl_orig=0),
            pos("理想汽车-W 02015(大)",   "02015.HK", "国金证券", "权益",  "投资", "HKD", 149125, market_cny_override=131327, pnl_orig=0),
            pos("理想汽车-W 02015(小)",   "02015.HK", "国金证券", "权益",  "投资", "HKD", 6484,   market_cny_override=5710,   pnl_orig=0),
            pos("国金现金",               "",         "国金证券", "货币",  "投资", "CNY", 177),

            # ── 支付宝 ──
            pos("稳健理财",               "",         "支付宝",   "固收",  "投资", "CNY", 535),
            pos("进阶理财",               "",         "支付宝",   "权益",  "投资", "CNY", 460),
            pos("余额宝",                 "",         "支付宝",   "货币",  "投资", "CNY", 199),

            # ── 老虎证券（USD，数据来自2026-03-16对账单，rate=6.889）──
            pos("理想汽车 LI",      "LI",               "老虎证券", "权益", "投资", "USD", 45600.00, quantity=2500,   pnl_orig=-7744.31, pnl_rate=-14.52, market_cny_override=round(45600.00*6.889), pnl_value=round(-7744.31*6.889)),
            pos("Meta META",        "META",             "老虎证券", "权益", "投资", "USD", 16941.15, quantity=27,     pnl_orig=737.13,   pnl_rate=4.55,   market_cny_override=round(16941.15*6.889), pnl_value=round(737.13*6.889)),
            pos("拼多多 PDD",        "PDD",              "老虎证券", "权益", "投资", "USD", 15576.00, quantity=150,    pnl_orig=589.29,   pnl_rate=3.93,   market_cny_override=round(15576.00*6.889), pnl_value=round(589.29*6.889)),
            pos("苹果 AAPL",         "AAPL",             "老虎证券", "权益", "投资", "USD", 15169.20, quantity=60,     pnl_orig=2459.59,  pnl_rate=19.35,  market_cny_override=round(15169.20*6.889), pnl_value=round(2459.59*6.889)),
            pos("特斯拉 TSLA",       "TSLA",             "老虎证券", "权益", "投资", "USD", 12657.92, quantity=32,     pnl_orig=3790.54,  pnl_rate=42.74,  market_cny_override=round(12657.92*6.889), pnl_value=round(3790.54*6.889)),
            pos("伯克希尔B BRK.B",   "BRK.B",            "老虎证券", "权益", "投资", "USD", 9844.20,  quantity=20,     pnl_orig=397.26,   pnl_rate=4.20,   market_cny_override=round(9844.20*6.889),  pnl_value=round(397.26*6.889)),
            pos("VONTOBEL债券基金",  "LU2416422678.USD", "老虎证券", "固收",  "投资", "USD", 8383.20,  quantity=56,     pnl_orig=224.67,   pnl_rate=2.75,   market_cny_override=round(8383.20*6.889),  pnl_value=round(224.67*6.889)),
            pos("QQQ",               "QQQ",              "老虎证券", "权益", "投资", "USD", 6003.80,  quantity=10,     pnl_orig=530.93,   pnl_rate=9.70,   market_cny_override=round(6003.80*6.889),  pnl_value=round(530.93*6.889)),
            pos("谷歌 GOOG",         "GOOG",             "老虎证券", "权益", "投资", "USD", 5479.56,  quantity=18,     pnl_orig=215.67,   pnl_rate=4.10,   market_cny_override=round(5479.56*6.889),  pnl_value=round(215.67*6.889)),
            pos("微软 MSFT",         "MSFT",             "老虎证券", "权益", "投资", "USD", 4799.40,  quantity=12,     pnl_orig=-1089.72, pnl_rate=-18.50, market_cny_override=round(4799.40*6.889),  pnl_value=round(-1089.72*6.889)),
            pos("安本标准债券基金",  "LU1725895616.USD", "老虎证券", "固收",  "投资", "USD", 3408.74,  quantity=210,    pnl_orig=268.42,   pnl_rate=8.55,   market_cny_override=round(3408.74*6.889),  pnl_value=round(268.42*6.889)),
            pos("Coinbase COIN",     "COIN",             "老虎证券", "权益", "投资", "USD", 3253.12,  quantity=16,     pnl_orig=-2496.92, pnl_rate=-43.42, market_cny_override=round(3253.12*6.889),  pnl_value=round(-2496.92*6.889)),
            pos("Hims HIMS",         "HIMS",             "老虎证券", "权益", "投资", "USD", 2488.00,  quantity=100,    pnl_orig=-1936.37, pnl_rate=-43.77, market_cny_override=round(2488.00*6.889),  pnl_value=round(-1936.37*6.889)),
            pos("SHY",               "SHY",              "老虎证券", "固收",  "投资", "USD", 413.25,   quantity=5,      pnl_orig=-2.25,    pnl_rate=-0.54,  market_cny_override=round(413.25*6.889),   pnl_value=round(-2.25*6.889)),
            pos("平安货币基金",       "HK0000720752.USD", "老虎证券", "货币",  "投资", "USD", 53.37,    quantity=0.4466, pnl_orig=0.02,     pnl_rate=0.04,   market_cny_override=round(53.37*6.889),    pnl_value=round(0.02*6.889)),

            # ── 雪盈证券（USD）──
            pos("理想汽车 LI",   "LI",    "雪盈证券", "权益", "投资", "USD", 5208,     quantity=300,   pnl_value=-28790, pnl_orig=-4175.35, pnl_rate=-44.50, market_cny_override=35915),

            # ── 富途证券（USD，数据来自2026-03-17持仓，rate=6.889）──
            pos("微软 MSFT",  "MSFT", "富途证券", "权益", "投资", "USD", 1994.70, quantity=5, pnl_orig=31.70,  pnl_rate=1.61,  market_cny_override=round(1994.70*6.889), pnl_value=round(31.70*6.889)),
            pos("QQQ",        "QQQ",  "富途证券", "权益", "投资", "USD", 599.59,  quantity=1, pnl_orig=-26.87, pnl_rate=-4.29, market_cny_override=round(599.59*6.889),  pnl_value=round(-26.87*6.889)),
            pos("拼多多 PDD", "PDD",  "富途证券", "权益", "投资", "USD", 104.28,  quantity=1, pnl_orig=-4.48,  pnl_rate=-4.12, market_cny_override=round(104.28*6.889),  pnl_value=round(-4.48*6.889)),

            # ── 招商银行 ──
            pos("朝朝宝",         "",      "招商银行", "货币",  "投资", "CNY", 2285),
            pos("招行理财",        "",      "招商银行", "固收",  "投资", "CNY", 1136),
            pos("招行基金",        "",      "招商银行", "权益",  "投资", "CNY", 698),
            pos("招行黄金",        "",      "招商银行", "另类",  "投资", "CNY", 28),

            # ── 建设银行（投资部分）──
            pos("建行基金",        "",      "建设银行", "权益",  "投资", "CNY", 757),
            pos("建行债券",        "",      "建设银行", "固收",  "投资", "CNY", 103),
            pos("建行理财",        "",      "建设银行", "固收",  "投资", "CNY", 20),
            pos("建行活期",        "",      "建设银行", "货币",  "投资", "CNY", 7),

            # ── 建设银行（养老部分）──
            pos("企业年金（建行）", "",     "建设银行", "货币",  "养老", "CNY", 161012),
            pos("个人养老金",       "",     "建设银行", "固收",  "养老", "CNY", 13796),

            # ── 全国住房公积金 ──
            pos("住房公积金（杭州）","",    "全国住房公积金", "货币", "公积金", "CNY", 359522),
        ]

        # ── 负债数据 ─────────────────────────────────────────────────
        liabilities = [
            Liability(portfolio_id=pid, name="招行-信用卡",      category="信用卡", purpose="日常消费", amount=5_169,   interest_rate=0.0),
            Liability(portfolio_id=pid, name="招行-闪电贷",      category="信用贷", purpose="日常消费", amount=10_000,  interest_rate=3.0),
            Liability(portfolio_id=pid, name="招行-e招贷",       category="信用贷", purpose="日常消费", amount=50_000,  interest_rate=3.08),
            Liability(portfolio_id=pid, name="建行-信用卡",      category="信用卡", purpose="日常消费", amount=162,     interest_rate=0.0),
            Liability(portfolio_id=pid, name="建行-快贷",        category="信用贷", purpose="购房",     amount=140_900, interest_rate=3.05),
            Liability(portfolio_id=pid, name="农行-网捷贷",      category="信用贷", purpose="投资杠杆", amount=300_000, interest_rate=3.0),
            Liability(portfolio_id=pid, name="南京银行-信易贷",  category="信用贷", purpose="购房",     amount=365_000, interest_rate=3.0),
            Liability(portfolio_id=pid, name="北京银行-京e贷",   category="信用贷", purpose="投资杠杆", amount=500_000, interest_rate=3.0),
            Liability(portfolio_id=pid, name="萧山农商-浙里贷",  category="信用贷", purpose="购房",     amount=210_000, interest_rate=3.0),
        ]

        session.add_all(positions)
        session.add_all(liabilities)
        session.commit()

        # ── 统计汇报 ─────────────────────────────────────────────────
        invest_pos = [p for p in positions if p.segment == "投资"]
        invest_liab = [l for l in liabilities if l.purpose == "投资杠杆"]
        all_pos = positions
        all_liab = liabilities

        total_invest_assets = sum(p.market_value_cny for p in invest_pos)
        total_invest_liab = sum(l.amount for l in invest_liab)
        total_all_assets = sum(p.market_value_cny for p in all_pos)
        total_all_liab = sum(l.amount for l in all_liab)
        net_invest = total_invest_assets - total_invest_liab
        net_all = total_all_assets - total_all_liab
        leverage = total_invest_liab / total_invest_assets * 100 if total_invest_assets > 0 else 0

        print(f"\n写入完成！")
        print(f"  持仓数量: {len(positions)} 条（投资 {len(invest_pos)} 条）")
        print(f"  负债数量: {len(liabilities)} 条（投资杠杆 {len(invest_liab)} 条）")
        print(f"\n【投资账户口径】")
        print(f"  总资产:   ¥{total_invest_assets:,.0f}")
        print(f"  投资杠杆: ¥{total_invest_liab:,.0f}")
        print(f"  净资产:   ¥{net_invest:,.0f}")
        print(f"  杠杆率:   {leverage:.1f}%")
        print(f"\n【全口径（含养老/公积金）】")
        print(f"  总资产:   ¥{total_all_assets:,.0f}")
        print(f"  总负债:   ¥{total_all_liab:,.0f}")
        print(f"  净资产:   ¥{net_all:,.0f}")

    except Exception as e:
        session.rollback()
        print(f"写入失败: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
