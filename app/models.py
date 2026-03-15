"""
WealthPilot - 数据库模型定义
核心领域对象: Portfolio, Position, Liability, DecisionLog

数据库基础设施（engine / session / init）统一在 app.database 中管理。
"""

import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base

# 保留向后兼容的便捷导入，让其他模块无需感知 database.py 的存在
from app.database import get_session, init_db  # noqa: F401


# ──────────────────────────────────────────────
# 枚举类型
# ──────────────────────────────────────────────

class AssetClass(str, enum.Enum):
    """大类资产分类"""
    EQUITY = "权益"
    FIXED_INCOME = "固收"
    CASH = "现金"
    ALTERNATIVE = "另类"


class Platform(str, enum.Enum):
    """资产所在平台"""
    HK_US_BROKER = "港美股券商"
    CN_BROKER = "境内券商"
    BANK = "银行"
    ALIPAY = "支付宝"
    FUND_PLATFORM = "基金平台"
    OTHER = "其他"


class DecisionStatus(str, enum.Enum):
    """决策执行状态"""
    PENDING = "待执行"
    EXECUTED = "已执行"
    CANCELLED = "已取消"


# ──────────────────────────────────────────────
# 核心模型
# ──────────────────────────────────────────────

class Portfolio(Base):
    """投资组合 - 系统核心操作对象"""
    __tablename__ = "portfolios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, default="我的投资组合")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 目标资产配置区间 (百分比)
    # min=0 且 max=100 表示"不设约束"
    min_equity_pct = Column(Float, default=40.0)
    max_equity_pct = Column(Float, default=80.0)
    min_fixed_income_pct = Column(Float, default=0.0)
    max_fixed_income_pct = Column(Float, default=100.0)
    min_cash_pct = Column(Float, default=0.0)
    max_cash_pct = Column(Float, default=100.0)
    min_alternative_pct = Column(Float, default=0.0)
    max_alternative_pct = Column(Float, default=100.0)

    # 投资纪律约束
    max_single_stock_pct = Column(Float, default=15.0)  # 单一股票仓位上限
    max_leverage_ratio = Column(Float, default=20.0)     # 最大杠杆率

    # 关联
    positions = relationship("Position", back_populates="portfolio", cascade="all, delete-orphan")
    liabilities = relationship("Liability", back_populates="portfolio", cascade="all, delete-orphan")
    decision_logs = relationship("DecisionLog", back_populates="portfolio", cascade="all, delete-orphan")


class Position(Base):
    """持仓头寸"""
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False)

    name = Column(String(200), nullable=False)          # 资产名称 (如: 理想汽车、沪深300ETF)
    ticker = Column(String(50), nullable=True)           # 代码 (如: LI, 510300)
    platform = Column(String(50), nullable=False)        # 所在平台
    asset_class = Column(String(20), nullable=False)     # 大类资产分类
    currency = Column(String(10), default="CNY")         # 币种
    quantity = Column(Float, default=0)                  # 数量/份额
    cost_price = Column(Float, default=0)                # 成本价
    current_price = Column(Float, default=0)             # 当前价格
    market_value_cny = Column(Float, default=0)          # 市值 (人民币)
    created_at = Column(DateTime, default=datetime.now)

    portfolio = relationship("Portfolio", back_populates="positions")

    @property
    def profit_loss(self):
        """盈亏金额"""
        if self.cost_price > 0 and self.quantity > 0:
            return (self.current_price - self.cost_price) * self.quantity
        return 0

    @property
    def profit_loss_pct(self):
        """盈亏百分比"""
        if self.cost_price > 0:
            return (self.current_price - self.cost_price) / self.cost_price * 100
        return 0


class Liability(Base):
    """负债"""
    __tablename__ = "liabilities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False)

    name = Column(String(200), nullable=False)           # 负债名称 (如: 招行信用卡、微粒贷)
    category = Column(String(50), nullable=False)        # 类型 (信用卡/信用贷/房贷/其他)
    amount = Column(Float, default=0)                    # 负债金额 (人民币)
    interest_rate = Column(Float, default=0)             # 年利率 (%)
    created_at = Column(DateTime, default=datetime.now)

    portfolio = relationship("Portfolio", back_populates="liabilities")


class DecisionLog(Base):
    """投资决策日志

    NOTE: M2 feature — UI 入口尚未实现，表结构已就绪供后续迭代使用。
    """
    __tablename__ = "decision_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False)

    trigger = Column(String(50), nullable=False)         # 决策动因: 策略偏离/纪律触发/事件驱动/风险暴露
    title = Column(String(200), nullable=False)          # 决策标题
    context = Column(Text, nullable=True)                # 决策时的市场快照与持仓情况
    reasoning = Column(Text, nullable=True)              # 分析逻辑
    conclusion = Column(Text, nullable=True)             # 最终决策结论
    status = Column(String(20), default="待执行")         # 执行状态
    created_at = Column(DateTime, default=datetime.now)
    executed_at = Column(DateTime, nullable=True)

    portfolio = relationship("Portfolio", back_populates="decision_logs")
