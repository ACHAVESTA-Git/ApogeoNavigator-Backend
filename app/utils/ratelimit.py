"""Límite de peticiones por ventana deslizante, en memoria y por proceso (suficiente para proteger a un servicio externo).

Memoria acotada: nunca guarda más de `max_keys` claves. Una clave cuyo historial queda vacío se borra, `peek` no crea
claves y, si se llena, se expulsa la clave usada hace más tiempo. Así un atacante que falsee cabeceras para inventar
clientes no puede hacer crecer el proceso sin límite (a cambio, puede expulsar el historial de otros clientes: la defensa
real de JPL es el límite global).
"""
import threading
import time
from collections import OrderedDict, deque
from typing import Callable, Hashable

from app.core import config as c


class SlidingWindowLimiter:
    """Permite como máximo `max_events` eventos por `window_s` segundos y clave."""

    def __init__(self, max_events: int, window_s: float, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = c.HORIZONS_RATE_MAX_KEYS):
        self.max_events = max_events
        self.window_s = window_s
        self.max_keys = max_keys
        self._clock = clock
        self._events: "OrderedDict[Hashable, deque[float]]" = OrderedDict()
        self._lock = threading.Lock()

    def _purged(self, key: Hashable, now: float) -> deque[float] | None:
        """Historial vigente de la clave (None si no hay); borra la clave si ya no le quedan eventos."""
        queue = self._events.get(key)
        if queue is None:
            return None
        while queue and now - queue[0] >= self.window_s:
            queue.popleft()
        if not queue:
            del self._events[key]
            return None
        return queue

    def _wait(self, queue: deque[float] | None, now: float) -> float:
        if queue is None or len(queue) < self.max_events:
            return 0.0
        return queue[0] + self.window_s - now

    def peek(self, key: Hashable = None) -> float:
        """Segundos que faltan para poder registrar un evento (0 si ya se puede); no registra nada ni crea la clave."""
        with self._lock:
            now = self._clock()
            return self._wait(self._purged(key, now), now)

    def hit(self, key: Hashable = None) -> float:
        """Registra un evento. Devuelve 0 si se permite o los segundos de espera si se supera el límite (sin registrar)."""
        with self._lock:
            now = self._clock()
            queue = self._purged(key, now)
            wait = self._wait(queue, now)
            if wait > 0:
                self._events.move_to_end(key)
                return wait
            if queue is None:
                queue = self._events[key] = deque()
                while len(self._events) > self.max_keys:
                    self._events.popitem(last=False)
            queue.append(now)
            self._events.move_to_end(key)
            return 0.0

    def forget(self, key: Hashable = None) -> None:
        """Olvida el historial de una clave (p. ej. tras un login correcto)."""
        with self._lock:
            self._events.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
