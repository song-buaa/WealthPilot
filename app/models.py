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
    MONETARY = "货币"
    ALTERNATIVE = "另类"
    DERIVATIVE = "衍生"


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

    # 多货币支持
    original_currency = Column(String(10), default="CNY")   # USD/HKD/CNY
    original_value    = Column(Float, default=0)             # 原始货币金额
    fx_rate_to_cny    = Column(Float, default=1.0)           # 汇率
    fx_rate_date      = Column(String(20), nullable=True)    # "latest" or "YYYY-MM-DD"
    segment           = Column(String(20), default="投资")   # 投资/养老/公积金

    # 盈亏（直接存储，不依赖成本价计算）
    profit_loss_value = Column(Float, default=0)             # 盈亏金额(元)
    profit_loss_rate  = Column(Float, default=0)             # 盈亏百分比
    profit_loss_original_value = Column(Float, default=0)   # 盈亏金额（原始货币，USD/HKD/CNY）

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
    purpose = Column(String(20), default="日常消费")     # 投资杠杆/购房/日常消费
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


# ──────────────────────────────────────────────
# 投研观点模块（三表：原始资料 → 候选卡 → 正式观点）
# ──────────────────────────────────────────────

class ResearchDocument(Base):
    """投研原始资料
    parse_status 流转：pending → parsed | saved_only | discarded
    """
    __tablename__ = "research_documents"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    title        = Column(String(300), nullable=False)
    # markdown / text / pdf / link / manual
    source_type  = Column(String(20), nullable=False, default="text")
    source_url   = Column(Text, nullable=True)
    raw_content  = Column(Text, nullable=True)
    uploaded_at  = Column(DateTime, default=datetime.now)
    publish_time = Column(String(50), nullable=True)    # 允许字符串，如"2025-Q1"
    author       = Column(String(100), nullable=True)
    object_name  = Column(String(100), nullable=True)   # 美团/理想/拼多多
    market_name  = Column(String(50), nullable=True)    # 港股/美股/A股/宏观
    tags         = Column(Text, nullable=True)           # JSON 字符串列表
    parse_status = Column(String(20), default="pending")
    notes        = Column(Text, nullable=True)

    cards = relationship(
        "ResearchCard", back_populates="document", cascade="all, delete-orphan"
    )


class ResearchCard(Base):
    """AI 提炼的候选观点卡（一份资料对应一张卡）"""
    __tablename__ = "research_cards"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("research_documents.id"), nullable=False)

    summary               = Column(Text, nullable=True)   # 资料主要讲什么
    thesis                = Column(Text, nullable=True)   # 核心投研结论
    bull_case             = Column(Text, nullable=True)   # 看多逻辑
    bear_case             = Column(Text, nullable=True)   # 看空/反对逻辑
    key_drivers           = Column(Text, nullable=True)   # JSON 列表：关键驱动因素
    risks                 = Column(Text, nullable=True)   # JSON 列表：主要风险
    key_metrics           = Column(Text, nullable=True)   # JSON 列表：待观察指标
    horizon               = Column(String(20), nullable=True)  # short/medium/long
    stance                = Column(String(20), nullable=True)  # bullish/bearish/neutral/watch
    action_suggestion     = Column(Text, nullable=True)
    invalidation_conditions = Column(Text, nullable=True)
    suggested_tags        = Column(Text, nullable=True)   # JSON 列表
    created_at            = Column(DateTime, default=datetime.now)

    document  = relationship("ResearchDocument", back_populates="cards")
    viewpoint = relationship(
        "ResearchViewpoint", back_populates="source_card", uselist=False
    )


class ResearchViewpoint(Base):
    """正式入库的投研观点（用户认可后从候选卡录入，或手动创建）"""
    __tablename__ = "research_viewpoints"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    title        = Column(String(300), nullable=False)
    # asset / sector / market / macro / strategy
    object_type  = Column(String(20), nullable=False, default="asset")
    object_name  = Column(String(100), nullable=True)
    market_name  = Column(String(50), nullable=True)
    topic_tags   = Column(Text, nullable=True)           # JSON 列表

    thesis              = Column(Text, nullable=True)
    supporting_points   = Column(Text, nullable=True)   # JSON 列表
    opposing_points     = Column(Text, nullable=True)   # JSON 列表
    key_metrics         = Column(Text, nullable=True)   # JSON 列表
    risks               = Column(Text, nullable=True)   # JSON 列表
    action_suggestion   = Column(Text, nullable=True)
    invalidation_conditions = Column(Text, nullable=True)

    horizon              = Column(String(20), nullable=True)  # short/medium/long
    stance               = Column(String(20), nullable=True)  # bullish/bearish/neutral/watch
    # strong / partial / reference
    user_approval_level  = Column(String(20), default="reference")
    # active / watch / outdated / invalid
    validity_status      = Column(String(20), default="active")

    source_card_id     = Column(Integer, ForeignKey("research_cards.id"), nullable=True)
    source_document_id = Column(Integer, ForeignKey("research_documents.id"), nullable=True)
    created_at         = Column(DateTime, default=datetime.now)
    updated_at         = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    source_card = relationship("ResearchCard", back_populates="viewpoint")


# ──────────────────────────────────────────────
# 用户画像模块
# ──────────────────────────────────────────────

class UserProfile(Base):
    """用户画像与投资目标（全局单条记录，upsert 语义）"""
    __tablename__ = "user_profiles"

    id      = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    version    = Column(Integer, default=1)

    # 风险画像
    risk_source           = Column(String(20),  nullable=True)   # "external" | "ai"
    risk_provider         = Column(String(50),  nullable=True)   # "招商银行" | "ai_generated"
    risk_original_level   = Column(String(10),  nullable=True)   # "C3" | "A2" | "高"
    risk_normalized_level = Column(Integer,      nullable=True)   # 1-5
    risk_type             = Column(String(20),  nullable=True)   # "保守型"|"稳健型"|"平衡型"|"成长型"|"进取型"
    risk_assessed_at      = Column(DateTime,    nullable=True)   # 用于判断是否过期（12个月）

    # 基础信息
    income_level          = Column(String(20),  nullable=True)   # "<10万"|"10-30万"|"30-100万"|">100万"
    income_stability      = Column(String(10),  nullable=True)   # "稳定"|"较稳定"|"波动"
    total_assets          = Column(String(20),  nullable=True)   # "<50万"|"50-200万"|"200-500万"|">500万"
    investable_ratio      = Column(String(10),  nullable=True)   # "<20%"|"20-50%"|"50-80%"|">80%"
    liability_level       = Column(String(10),  nullable=True)   # "无"|"低"|"中"|"高"
    family_status         = Column(String(20),  nullable=True)   # "单身"|"已婚无子"|"已婚有子"|"退休"
    asset_structure       = Column(String(20),  nullable=True)   # "现金为主"|"固收为主"|"股票基金为主"|"多元配置"
    investment_motivation = Column(String(20),  nullable=True)   # "新增资金"|"调整配置"|"市场波动调整"|"长期规划"
    fund_usage_timeline   = Column(String(10),  nullable=True)   # "1年内"|"1-3年"|"3年以上"|"不确定"

    # 投资目标
    goal_type             = Column(Text,        nullable=True)   # JSON 字符串，存多选结果
    target_return         = Column(String(10),  nullable=True)   # "<5%"|"5-10%"|"10-20%"|">20%"
    max_drawdown          = Column(String(10),  nullable=True)   # "<5%"|"5-15%"|"15-30%"|">30%"
    investment_horizon    = Column(String(10),  nullable=True)   # "<1年"|"1-3年"|"3-5年"|">5年"

    # AI 生成结果
    ai_summary            = Column(Text,        nullable=True)   # 自然语言总结
    ai_style              = Column(String(10),  nullable=True)   # "稳健"|"平衡"|"进取"
    ai_confidence         = Column(String(10),  nullable=True)   # "high"|"medium"|"low"


# ──────────────────────────────────────────────
# 多轮对话历史（持久化存储）
# ──────────────────────────────────────────────

class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String,  nullable=False, index=True)
    role       = Column(String,  nullable=False)   # "user" | "assistant"
    content    = Column(Text,    nullable=False)   # user原文 / assistant的chat_answer
    intent     = Column(String,  nullable=True)    # 仅assistant轮，如"PositionDecision"
    asset      = Column(String,  nullable=True)    # 仅assistant轮，如"理想汽车"
    created_at = Column(DateTime, default=datetime.utcnow)
