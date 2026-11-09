"""Last price and a short rolling window per symbol."""
from collections import deque

from terminal.events import Tick

KEEP = 900.0  # seconds of history kept per symbol


class Prices:
    GAP = 600.0  # a longer silence (sleep, market closed) resets the window

    def __init__(self):
        self._ticks: dict[str, deque[tuple[float, float]]] = {}

    def update(self, tick: Tick) -> None:
        window = self._ticks.setdefault(tick.symbol, deque())
        if window and tick.ts - window[-1][0] > self.GAP:
            window.clear()
        window.append((tick.ts, tick.price))
        while window[0][0] < tick.ts - KEEP:
            window.popleft()

    def last(self, symbol: str) -> float | None:
        window = self._ticks.get(symbol)
        return window[-1][1] if window else None

    def age(self, symbol: str, now: float) -> float | None:
        window = self._ticks.get(symbol)
        return now - window[-1][0] if window else None

    def change(self, symbol: str, seconds: float, now: float) -> float | None:
        """Fractional move over the last `seconds`, or None without enough unbroken history."""
        window = self._ticks.get(symbol)
        if not window:
            return None
        oldest = next(((ts, px) for ts, px in window if ts >= now - seconds), None)
        if oldest is None or now - oldest[0] < seconds / 2 or oldest[1] <= 0:
            return None
        return window[-1][1] / oldest[1] - 1
