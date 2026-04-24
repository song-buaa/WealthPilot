"""
InfoAdapter 抽象基类 + RawFact 数据契约。

所有信息源 Adapter 继承 InfoAdapter，返回 RawFact 列表。
Adapter 只负责 fetch，不做 summarize 或 extract_signals。
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from research_v2.schemas import SourceRef, SourceType
from research_v2.symbol import Symbol

logger = logging.getLogger(__name__)


class AdapterQuotaError(Exception):
    """Adapter 配额耗尽异常（如 Alpha Vantage 25次/天限制）"""
    pass


@dataclass
class RawFact:
    """Adapter 返回的单条原始事实，成为 ViewpointCard 的事实层输入。"""
    source_type: SourceType
    source_url: Optional[str]
    as_of: datetime
    affected_symbols: list[Symbol]
    payload: dict
    source_refs: list[SourceRef] = field(default_factory=list)


class InfoAdapter(ABC):
    """信息源适配器抽象基类。"""

    @property
    @abstractmethod
    def adapter_id(self) -> str:
        """Adapter 唯一标识符。"""
        ...

    @property
    @abstractmethod
    def supported_source_types(self) -> list[SourceType]:
        """该 Adapter 支持的 source_type 列表。"""
        ...

    @abstractmethod
    def fetch(self, symbols: list[Symbol], since: Optional[datetime] = None) -> list[RawFact]:
        """拉取指定标的的原始数据。

        Args:
            symbols: 要查询的标的列表
            since: 只返回此时间之后的数据（可选）

        Returns:
            RawFact 列表

        Raises:
            AdapterQuotaError: 配额耗尽
        """
        ...

    @abstractmethod
    def is_symbol_supported(self, symbol: Symbol) -> bool:
        """判断该 Adapter 是否支持此 symbol。"""
        ...
