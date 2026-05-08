"""
简单内存 TTL 缓存。
- 富途 snapshot:5 分钟 TTL
- AV OVERVIEW:24 小时 TTL
"""
import time
from typing import Any, Optional


class TTLCache:
    def __init__(self):
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Optional[Any]:
        if key not in self._store:
            return None
        value, expires_at = self._store[key]
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: int):
        self._store[key] = (value, time.time() + ttl_seconds)

    def clear(self):
        self._store.clear()


_cache = TTLCache()

QUOTE_TTL = 5 * 60
FUNDAMENTALS_TTL = 24 * 3600


def get_cached(key: str) -> Optional[Any]:
    return _cache.get(key)


def set_cached(key: str, value: Any, ttl_seconds: int):
    _cache.set(key, value, ttl_seconds)
