"""Límite de envíos en ventana deslizante, en memoria (una sola instancia)."""
import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """Registra el intento y devuelve False si se superó el límite."""
        now = self._clock()
        with self._lock:
            if len(self._hits) > 10_000:
                self._purge(now, window_seconds)
            hits = self._hits[key]
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def _purge(self, now: float, window_seconds: int) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - window_seconds]:
            del self._hits[key]
