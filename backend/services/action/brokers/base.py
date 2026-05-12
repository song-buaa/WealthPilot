"""
WealthPilot v3.2 BrokerAdapter 抽象基类 + 统一数据契约。

所有券商适配器（Mock / Tiger / Futu / GJZQ）必须实现此接口。
OrderManager 通过依赖注入获取适配器实例，不感知具体券商实现。

数据契约：
- OrderRequest：统一下单请求（OrderManager → BrokerAdapter）
- OrderStatusUpdate：统一状态回报（BrokerAdapter → OrderManager）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class OrderRequest:
    """统一下单请求契约。"""
    symbol: str                         # LI:US / 0700:HK / 600519:SH (TICKER:MARKET)
    side: str                           # BUY / SELL
    quantity: int
    order_type: str                     # LIMIT / CONDITIONAL_LIMIT
    limit_price: Optional[Decimal] = None
    trigger_price: Optional[Decimal] = None  # CONDITIONAL_LIMIT 必填
    time_in_force: str = "GTC"          # DAY / GTC
    local_order_id: str = ""            # WealthPilot 侧 order_record.id


@dataclass
class OrderStatusUpdate:
    """统一订单状态回报。"""
    broker_order_id: Optional[str]      # 券商侧订单 ID
    local_order_id: str                 # WealthPilot 侧 order_record.id
    status: str                         # OrderStatus 9 个值之一
    filled_quantity: int = 0
    avg_filled_price: Optional[Decimal] = None
    timestamp: int = 0                  # Unix 毫秒时间戳
    raw_response: dict = field(default_factory=dict)


class BrokerAdapter(ABC):
    """券商适配器抽象基类。"""

    @property
    @abstractmethod
    def broker_name(self) -> str:
        """券商标识：mock / tiger / futu / gjzq"""
        ...

    @abstractmethod
    def authenticate(self, credentials: dict) -> bool:
        """验证券商凭证。成功返回 True，失败返回 False。"""
        ...

    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderStatusUpdate:
        """
        提交订单到券商。

        可能的返回状态：
        - submitted_to_broker（正常提交）
        - rejected（合规/资金校验失败）

        可能抛出的异常：
        - ConnectionError / TimeoutError（网络异常，上层应设 status=unknown）
        """
        ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        """
        取消订单。

        已终态（filled/cancelled/rejected/expired）返回 False。
        其他状态返回 True 表示取消成功。
        """
        ...

    @abstractmethod
    def get_order_status(self, broker_order_id: str) -> OrderStatusUpdate:
        """
        查询订单最新状态。

        可能抛出 ConnectionError（上层应处理为 unknown）。
        """
        ...

    @abstractmethod
    def list_open_orders(self) -> list[OrderStatusUpdate]:
        """列出所有未终态订单。"""
        ...

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """获取当前持仓。"""
        ...

    @abstractmethod
    def get_account_info(self) -> dict:
        """获取账户信息（资金余额等）。"""
        ...

    def shutdown(self) -> None:
        """
        清理资源（取消 Timer、关闭连接等）。
        FastAPI lifespan shutdown 时调用。
        默认空实现，子类按需覆盖。
        """
        pass
