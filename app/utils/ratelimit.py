"""Límite de peticiones por ventana deslizante, en memoria y por proceso (suficiente para proteger a un servicio externo)."""
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Hashable


class SlidingWindowLimiter:
    """Permite como máximo `max_events` eventos por `window_s` segundos y clave."""

    def __init__(self, max_events: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.max_events = max_events
        self.window_s = window_s
        self._clock = clock
        self._events: dict[Hashable, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _purge(self, queue: deque[float], now: float) -> None:
        while queue and now - queue[0] >= self.window_s:
            queue.popleft()

    def peek(self, key: Hashable = None) -> float:
        """Segundos que faltan para poder registrar un evento (0 si ya se puede); no registra nada."""
        with self._lock:
            now = self._clock()
            queue = self._events[key]
            self._purge(queue, now)
            return 0.0 if len(queue) < self.max_events else queue[0] + self.window_s - now

    def hit(self, key: Hashable = None) -> float:
        """Registra un evento. Devuelve 0 si se permite o los segundos de espera si se supera el límite (sin registrar)."""
        with self._lock:
            now = self._clock()
            queue = self._events[key]
            self._purge(queue, now)
            if len(queue) >= self.max_events:
                return queue[0] + self.window_s - now
            queue.append(now)
            return 0.0

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
