"""
WealthPilot - analyzer.py 单元测试

测试策略：
- 只测纯计算逻辑，不碰数据库（用 unittest.mock patch 掉 get_session）
- 覆盖 BalanceSheet 计算、各类偏离告警、边界条件
- 运行：pytest tests/test_analyzer.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import MagicMock, patch
from app.analyzer import analyze_portfolio, check_deviations, BalanceSheet, DeviationAlert


# ──────────────────────────────────────────────
# 测试辅助：构造假 ORM 对象
# ──────────────────────────────────────────────

def _make_portfolio(**kwargs):
    """构造带默认值的假 Portfolio 对象"""
    p = MagicMock()
    p.target_equity_pct = kwargs.get("target_equity_pct", 60.0)
    p.target_fixed_income_pct = kwargs.get("target_fixed_income_pct", 30.0)
    p.target_cash_pct = kwargs.get("target_cash_pct", 10.0)
    p.target_alternative_pct = kwargs.get("target_alternative_pct", 0.0)
    p.max_single_stock_pct = kwargs.get("max_single_stock_pct", 15.0)
    p.max_leverage_ratio = kwargs.get("max_leverage_ratio", 20.0)
    return p


def _make_position(name, asset_class, platform, market_value_cny,
                   ticker="", cost_price=0.0, current_price=0.0, quantity=0.0):
    pos = MagicMock()
    pos.name = name
    pos.ticker = ticker
    pos.asset_class = asset_class
    pos.platform = platform
    pos.market_value_cny = market_value_cny
    pos.cost_price = cost_price
    pos.current_price = current_price
    pos.quantity = quantity
    return pos


def _make_liability(name, amount, category="信用贷", interest_rate=0.0):
    liab = MagicMock()
    liab.name = name
    liab.amount = amount
    liab.category = category
    liab.interest_rate = interest_rate
    return liab


# ──────────────────────────────────────────────
# analyze_portfolio 测试
# ──────────────────────────────────────────────

class TestAnalyzePortfolio:

    def _run(self, positions, liabilities):
        """patch DB，直接测计算逻辑"""
        portfolio = _make_portfolio()
        portfolio.positions = positions
        portfolio.liabilities = liabilities

        session_mock = MagicMock()
        session_mock.query.return_value.filter_by.return_value.first.return_value = portfolio

        with patch("app.analyzer.get_session", return_value=session_mock):
            return analyze_portfolio(portfolio_id=1)

    def test_basic_totals(self):
        """总资产、总负债、净资产计算正确"""
        positions = [
            _make_position("股票A", "权益", "港美股券商", 100_000),
            _make_position("债券B", "固收", "银行", 50_000),
        ]
        liabilities = [_make_liability("信用卡", 20_000)]
        bs = self._run(positions, liabilities)

        assert bs.total_assets == 150_000
        assert bs.total_liabilities == 20_000
        assert bs.net_worth == 130_000

    def test_asset_class_breakdown(self):
        """各大类资产金额和占比正确"""
        positions = [
            _make_position("股票A", "权益", "港美股券商", 60_000),
            _make_position("债券B", "固收", "银行", 30_000),
            _make_position("余额宝", "现金", "支付宝", 10_000),
        ]
        bs = self._run(positions, [])

        assert bs.equity_value == 60_000
        assert bs.fixed_income_value == 30_000
        assert bs.cash_value == 10_000
        assert bs.equity_pct == 60.0
        assert bs.fixed_income_pct == 30.0
        assert bs.cash_pct == 10.0

    def test_leverage_ratio(self):
        """杠杆率 = 负债 / 总资产 × 100"""
        positions = [_make_position("股票A", "权益", "港美股券商", 100_000)]
        liabilities = [_make_liability("贷款", 25_000)]
        bs = self._run(positions, liabilities)

        assert bs.leverage_ratio == 25.0

    def test_platform_distribution(self):
        """平台分布汇总正确（同平台多个持仓累加）"""
        positions = [
            _make_position("股票A", "权益", "港美股券商", 40_000),
            _make_position("股票B", "权益", "港美股券商", 20_000),
            _make_position("债券C", "固收", "银行", 40_000),
        ]
        bs = self._run(positions, [])

        assert bs.platform_distribution["港美股券商"] == 60_000
        assert bs.platform_distribution["银行"] == 40_000

    def test_concentration_as_percentage(self):
        """集中度以百分比表示（非绝对金额）"""
        positions = [
            _make_position("股票A", "权益", "港美股券商", 80_000),
            _make_position("债券B", "固收", "银行", 20_000),
        ]
        bs = self._run(positions, [])

        assert bs.concentration["股票A"] == 80.0
        assert bs.concentration["债券B"] == 20.0

    def test_empty_portfolio(self):
        """空持仓不崩溃，返回全零 BalanceSheet"""
        bs = self._run([], [])

        assert bs.total_assets == 0
        assert bs.total_liabilities == 0
        assert bs.equity_pct == 0
        assert bs.leverage_ratio == 0

    def test_no_liabilities(self):
        """无负债时杠杆率为 0"""
        positions = [_make_position("股票A", "权益", "港美股券商", 100_000)]
        bs = self._run(positions, [])

        assert bs.total_liabilities == 0
        assert bs.leverage_ratio == 0.0
        assert bs.net_worth == 100_000

    def test_alternative_asset_class(self):
        """另类资产正确归类"""
        positions = [
            _make_position("黄金ETF", "另类", "银行", 50_000),
            _make_position("股票A", "权益", "港美股券商", 50_000),
        ]
        bs = self._run(positions, [])

        assert bs.alternative_value == 50_000
        assert bs.alternative_pct == 50.0


# ──────────────────────────────────────────────
# check_deviations 测试
# ──────────────────────────────────────────────

class TestCheckDeviations:

    def _run(self, bs: BalanceSheet, portfolio_kwargs=None):
        portfolio = _make_portfolio(**(portfolio_kwargs or {}))
        session_mock = MagicMock()
        session_mock.query.return_value.filter_by.return_value.first.return_value = portfolio

        with patch("app.analyzer.get_session", return_value=session_mock):
            return check_deviations(portfolio_id=1, balance_sheet=bs)

    def _bs_with_alloc(self, equity=60.0, fi=30.0, cash=10.0, alt=0.0,
                       leverage=0.0, concentration=None):
        """快速构造一个 BalanceSheet"""
        bs = BalanceSheet()
        bs.total_assets = 100.0  # 用 100 方便直接把百分比当数值
        bs.equity_pct = equity
        bs.fixed_income_pct = fi
        bs.cash_pct = cash
        bs.alternative_pct = alt
        bs.leverage_ratio = leverage
        bs.concentration = concentration or {}
        return bs

    def test_no_alerts_when_on_target(self):
        """配置与目标完全一致时无告警"""
        bs = self._bs_with_alloc(equity=60, fi=30, cash=10)
        alerts = self._run(bs)
        assert alerts == []

    def test_equity_over_threshold_triggers_alert(self):
        """权益超配超过阈值产生告警"""
        bs = self._bs_with_alloc(equity=72)  # 目标 60，偏离 +12 > 5
        alerts = self._run(bs)
        types = [a.alert_type for a in alerts]
        titles = [a.title for a in alerts]
        assert "策略偏离" in types
        assert any("权益" in t for t in titles)

    def test_equity_underweight_triggers_alert(self):
        """权益低配也产生告警"""
        bs = self._bs_with_alloc(equity=50)  # 偏离 -10
        alerts = self._run(bs)
        assert any("低配" in a.title for a in alerts)

    def test_small_deviation_no_alert(self):
        """偏离在阈值内（≤5pp）不产生告警"""
        bs = self._bs_with_alloc(equity=63)  # 偏离 +3，低于阈值
        alerts = self._run(bs)
        assert not any(a.alert_type == "策略偏离" and "权益" in a.title for a in alerts)

    def test_high_severity_over_15pp(self):
        """偏离超过 15pp 标记为高严重度"""
        bs = self._bs_with_alloc(equity=80)  # 偏离 +20
        alerts = self._run(bs)
        equity_alert = next(a for a in alerts if "权益" in a.title)
        assert equity_alert.severity == "高"

    def test_medium_severity_between_5_and_15pp(self):
        """偏离 5~15pp 标记为中严重度"""
        bs = self._bs_with_alloc(equity=70)  # 偏离 +10
        alerts = self._run(bs)
        equity_alert = next(a for a in alerts if "权益" in a.title)
        assert equity_alert.severity == "中"

    def test_single_position_over_limit(self):
        """单一持仓超过上限产生纪律触发告警"""
        bs = self._bs_with_alloc(concentration={"1:股票A": 20.0})  # 上限 15%
        alerts = self._run(bs)
        assert any(a.alert_type == "纪律触发" and "股票A" in a.title for a in alerts)

    def test_single_position_within_limit_no_alert(self):
        """单一持仓在上限内不告警"""
        bs = self._bs_with_alloc(concentration={"1:股票A": 10.0})
        alerts = self._run(bs)
        assert not any(a.alert_type == "纪律触发" for a in alerts)

    def test_leverage_over_limit(self):
        """杠杆率超限产生风险暴露告警"""
        bs = self._bs_with_alloc(leverage=25.0)  # 上限 20%
        alerts = self._run(bs)
        assert any(a.alert_type == "风险暴露" and "杠杆" in a.title for a in alerts)

    def test_alerts_sorted_by_severity(self):
        """告警按严重程度排序：高 > 中 > 低"""
        bs = self._bs_with_alloc(
            equity=80,                            # 高：偏离 +20pp
            fi=19,                                # 中：偏离 -11pp
            concentration={"1:股票A": 20.0},      # 高：纪律触发
            leverage=25.0,                         # 高：风险暴露
        )
        alerts = self._run(bs)
        severity_order = {"高": 0, "中": 1, "低": 2}
        ranks = [severity_order[a.severity] for a in alerts]
        assert ranks == sorted(ranks)

    def test_empty_balance_sheet_returns_no_alerts(self):
        """总资产为 0 时不产生任何告警"""
        bs = BalanceSheet()  # total_assets = 0
        alerts = self._run(bs)
        assert alerts == []

    def test_deviation_value_correct(self):
        """告警中的 deviation 数值计算正确"""
        bs = self._bs_with_alloc(equity=72)  # 目标 60，偏离 +12
        alerts = self._run(bs)
        equity_alert = next(a for a in alerts if "权益" in a.title)
        assert abs(equity_alert.deviation - 12.0) < 0.01
