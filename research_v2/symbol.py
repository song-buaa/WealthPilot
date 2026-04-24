"""
Symbol + Entity 两级标准化，以及 EntityRegistry YAML 加载器。

Symbol 格式: <ticker>:<market>  (如 LI:US, 0700:HK, 600519:SH)
Entity: 公司实体，多 Symbol 聚合（增强层，允许 null）
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

VALID_MARKETS = {"US", "HK", "SH", "SZ"}


@dataclass(frozen=True)
class Symbol:
    """标准化标的标识符，格式 <ticker>:<market>"""

    ticker: str
    market: str

    def __str__(self) -> str:
        return f"{self.ticker}:{self.market}"

    @classmethod
    def parse(cls, raw: str) -> "Symbol":
        """从 'TICKER:MARKET' 字符串解析为 Symbol 对象。

        Raises:
            ValueError: 格式不合法或市场代码无效
        """
        raw = raw.strip()
        if ":" not in raw:
            raise ValueError(f"Symbol 格式错误，缺少 ':'：'{raw}'")
        parts = raw.split(":", maxsplit=1)
        ticker = parts[0].strip().upper()
        market = parts[1].strip().upper()
        if not ticker:
            raise ValueError(f"Symbol ticker 为空：'{raw}'")
        if market not in VALID_MARKETS:
            raise ValueError(
                f"无效的市场代码 '{market}'，"
                f"有效值: {', '.join(sorted(VALID_MARKETS))}"
            )
        return cls(ticker=ticker, market=market)


@dataclass
class Entity:
    """公司实体，跨市场多 Symbol 聚合"""

    entity_id: str
    display_name_cn: str
    display_name_en: str
    symbols: list[Symbol] = field(default_factory=list)


class EntityRegistry:
    """从 YAML 加载 Entity 映射表，提供按 entity_id 和 symbol 的查询。"""

    def __init__(self) -> None:
        self._by_id: dict[str, Entity] = {}
        self._by_symbol: dict[Symbol, Entity] = {}

    def load(self, yaml_path: str) -> None:
        """加载 YAML 文件并构建索引。"""
        if not os.path.exists(yaml_path):
            logger.warning("Entity registry 文件不存在: %s", yaml_path)
            return

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data or "entities" not in data:
            logger.warning("Entity registry 为空或格式错误: %s", yaml_path)
            return

        for item in data["entities"]:
            symbols = [Symbol.parse(s) for s in item.get("symbols", [])]
            entity = Entity(
                entity_id=item["entity_id"],
                display_name_cn=item["display_name_cn"],
                display_name_en=item["display_name_en"],
                symbols=symbols,
            )
            self._by_id[entity.entity_id] = entity
            for sym in symbols:
                self._by_symbol[sym] = entity

        logger.info(
            "EntityRegistry 加载完成: %d entities, %d symbols",
            len(self._by_id),
            len(self._by_symbol),
        )

    def lookup_by_id(self, entity_id: str) -> Optional[Entity]:
        """按 entity_id 查找 Entity。"""
        return self._by_id.get(entity_id)

    def lookup(self, symbol: Symbol) -> Optional[Entity]:
        """按 Symbol 查找所属 Entity。"""
        return self._by_symbol.get(symbol)

    def expand_symbols(self, symbol: Symbol) -> list[Symbol]:
        """按 Entity 扩展 symbol 列表。找不到 Entity 则返回 [symbol] 自身。"""
        entity = self.lookup(symbol)
        if entity is None:
            return [symbol]
        return list(entity.symbols)

    def all_entities(self) -> list[Entity]:
        """返回所有已注册的 Entity。"""
        return list(self._by_id.values())


# ── 全局单例 ──────────────────────────────────────────────────────

_registry: Optional[EntityRegistry] = None


def get_registry() -> EntityRegistry:
    """获取全局 EntityRegistry 单例，首次调用时自动加载。"""
    global _registry
    if _registry is None:
        _registry = EntityRegistry()
        default_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "data",
            "entity_registry.yaml",
        )
        _registry.load(default_path)
    return _registry
