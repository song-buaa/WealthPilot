"""
WealthPilot - 数据库模型定义
核心领域对象: Portfolio, Position, DecisionLog
"""

from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime,
    Text, ForeignKey, Enum as SAEnum
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
import enum
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "wealthpilot.db")
Base = declarative_base()

# 懒加载：engine 和 SessionLocal 在首次使用时才创建，
# 避免 import 时 data/ 目录不存在导致的路径问题。
_engine = None
_SessionLocal = None


def _get_engine():
    global _engine
    if _engine is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
    return _engine


def _get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=_get_engine())
    return _SessionLocal


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

    # 目标资产配置 (百分比)
    target_equity_pct = Column(Float, default=60.0)
    target_fixed_income_pct = Column(Float, default=30.0)
    target_cash_pct = Column(Float, default=10.0)
    target_alternative_pct = Column(Float, default=0.0)

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
    quantity = Column(Float, default=0)                   # 数量/份额
    cost_price = Column(Float, default=0)                 # 成本价
    current_price = Column(Float, default=0)              # 当前价格
    market_value_cny = Column(Float, default=0)           # 市值 (人民币)
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
    amount = Column(Float, default=0)                     # 负债金额 (人民币)
    interest_rate = Column(Float, default=0)              # 年利率 (%)
    created_at = Column(DateTime, default=datetime.now)

    portfolio = relationship("Portfolio", back_populates="liabilities")


class DecisionLog(Base):
    """投资决策日志"""
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


# ──────────────────────────────────────────────
# 初始化数据库
# ──────────────────────────────────────────────

def init_db():
    """创建所有表"""
    Base.metadata.create_all(_get_engine())


def get_session():
    """获取数据库会话"""
    return _get_session_factory()()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at: {DB_PATH}")
