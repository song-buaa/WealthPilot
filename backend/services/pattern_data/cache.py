"""Thread-safe read-through cache with single-flight request deduplication."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Hashable

from .contracts import PatternDataResult, PatternDataStatus


@dataclass
class _Entry:
    value: PatternDataResult
    expires_at: float


class DailyPatternDataCache:
    """Small process-local cache; provider failures always receive the short TTL."""

    def __init__(
        self,
        *,
        positive_ttl_seconds: float = 15 * 60,
        negative_ttl_seconds: float = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._positive_ttl = positive_ttl_seconds
        self._negative_ttl = negative_ttl_seconds
        self._clock = clock
        self._entries: dict[Hashable, _Entry] = {}
        self._inflight: dict[Hashable, threading.Event] = {}
        self._lock = threading.Lock()

    def get_or_load(
        self,
        key: Hashable,
        loader: Callable[[], PatternDataResult],
        *,
        refresh: bool = False,
    ) -> PatternDataResult:
        while True:
            with self._lock:
                now = self._clock()
                entry = self._entries.get(key)
                if not refresh and entry and entry.expires_at > now:
                    return entry.value
                if entry:
                    self._entries.pop(key, None)

                event = self._inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    owner = True
                else:
                    owner = False

            if owner:
                break
            event.wait()
            refresh = False

        try:
            value = loader()
            ttl = (
                self._negative_ttl
                if value.status is PatternDataStatus.DATA_UNAVAILABLE
                else self._positive_ttl
            )
            with self._lock:
                self._entries[key] = _Entry(value, self._clock() + ttl)
            return value
        finally:
            with self._lock:
                completed = self._inflight.pop(key, None)
                if completed:
                    completed.set()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
