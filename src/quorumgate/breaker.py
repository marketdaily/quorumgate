"""Per-seat circuit breaker.

In a long batch run, a dead model endpoint (exhausted quota, missing key,
billing wall) fails identically on every call. Without a breaker each item in
the batch pays the latency and log noise of the same doomed call. The breaker
opens a seat after ``max_strikes`` consecutive failures -- or immediately when
the error message contains a *dead marker*, a substring that identifies a
non-transient failure (e.g. ``"quota exceeded"``, ``"402"``, ``"invalid api
key"``). Open seats are skipped for the rest of the run.
"""
from __future__ import annotations

import threading
from typing import Dict, Iterable, Tuple


class CircuitBreaker:
    def __init__(self, max_strikes: int = 3, dead_markers: Iterable[str] = ()):
        if max_strikes < 1:
            raise ValueError("max_strikes must be >= 1")
        self.max_strikes = max_strikes
        self.dead_markers: Tuple[str, ...] = tuple(dead_markers)
        self._strikes: Dict[str, int] = {}
        self._open: Dict[str, str] = {}
        self._lock = threading.Lock()

    def is_open(self, name: str) -> bool:
        return name in self._open

    @property
    def open_reasons(self) -> Dict[str, str]:
        """Mapping of open seat name -> truncated reason it was opened."""
        return dict(self._open)

    def record_success(self, name: str) -> None:
        with self._lock:
            self._strikes[name] = 0

    def record_failure(self, name: str, error: Exception) -> bool:
        """Record a failure; return True if the seat is now open."""
        msg = str(error)
        with self._lock:
            if name in self._open:
                return True
            self._strikes[name] = self._strikes.get(name, 0) + 1
            fatal = any(marker in msg for marker in self.dead_markers)
            if fatal or self._strikes[name] >= self.max_strikes:
                self._open[name] = msg[:120]
                return True
            return False

    def reset(self) -> None:
        with self._lock:
            self._strikes.clear()
            self._open.clear()
