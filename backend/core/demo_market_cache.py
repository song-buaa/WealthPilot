"""
Demo 行情缓存 — 进程内按 symbol 缓存，TTL 15 分钟。

防访客刷量的核心机制。同一标的 15 分钟内重复请求走缓存不打外部 API。
"""
import time
import threading
from typing import Any, Optional

_CACHE_TTL = 15 * 60  # 15 分钟
_cache: dict[str, tuple[float, Any]] = {}  # key → (timestamp, value)
_lock = threading.Lock()


def get(key: str) -> Optional[Any]:
    """取缓存，过期返回 None。"""
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        ts, val = entry
        if time.monotonic() - ts > _CACHE_TTL:
            del _cache[key]
            return None
        return val


def put(key: str, value: Any) -> None:
    """写缓存。"""
    with _lock:
        _cache[key] = (time.monotonic(), value)


def clear() -> None:
    """清空缓存（测试用）。"""
    with _lock:
        _cache.clear()
