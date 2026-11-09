"""Turns the event stream into alerts: what deserves attention, how loudly, and not twice."""
import time
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from terminal.events import Alert, Tick, WhaleEvent
from terminal.prices import Prices

NEW_YORK = ZoneInfo("America/New_York")
MIN_ZONE_HEADLINES = 5  # a ratio on two headlines is noise
DAY = 86400


def us_market_open(ts: float) -> bool:
    """US stocks trade (including extended hours) 04:00-20:00 New York time on weekdays.
    shortcut: exchange holidays count as open, upgrade when a holiday calendar is worth a dependency."""
    local = datetime.fromtimestamp(ts, NEW_YORK)
    return local.weekday() < 5 and 4 <= local.hour < 20


def _usd(value: float) -> str:
    return f"${value / 1e9:.2f}B" if value >= 1e9 else f"${value / 1e6:.1f}M" if value >= 1e6 else f"${value:,.0f}"


def _minutes(text) -> int | None:
    try:
        hours, minutes = str(text).split(":")
        return int(hours) * 60 + int(minutes)
    except ValueError:
        return None


class AlertEngine:
    def __init__(self, settings: Callable[[], dict], prices: Prices, watchlist: Callable[[], set[str]],
                 asset_of: Callable[[str], str], now=time.time):
        self.settings, self.prices, self.watchlist, self.asset_of, self.now = settings, prices, watchlist, asset_of, now
        self._fired: dict[str, float] = {}
        self._last: dict[str, float] = {}

    # ---- plumbing
    def _num(self, key: str, fallback: float) -> float:
        try:
            return float(self.settings()[key])
        except (KeyError, TypeError, ValueError):
            return fallback

    def _quiet(self) -> bool:
        settings = self.settings()
        if settings.get("muted") is True:
            return True
        start, end = _minutes(settings.get("quiet_start")), _minutes(settings.get("quiet_end"))
        if start is None or end is None or start == end:
            return False
        local = datetime.fromtimestamp(self.now())
        minute = local.hour * 60 + local.minute
        return start <= minute < end if start < end else minute >= start or minute < end

    def _alert(self, key: str, severity: str, title: str, body: str, symbol: str) -> list[Alert]:
        now = self.now()
        # a critical alert has its own cooldown, so it is never swallowed by an earlier, milder one
        slot = key + ":critical" if severity == "critical" else key
        if now - self._fired.get(slot, float("-inf")) < self._num("cooldown_s", 900):
            return []
        self._fired[slot] = now
        if len(self._fired) > 5000:
            self._fired = dict(list(self._fired.items())[-2500:])
        if severity != "info" and self._quiet():
            severity = "info"
        return [Alert(key, severity, title[:200], body[:400], symbol, now)]

    # ---- rules
    def on_event(self, event) -> list[Alert]:
        if isinstance(event, Tick):
            return self._tick(event)
        if isinstance(event, WhaleEvent):
            return self._whale(event)
        return []

    def _tick(self, tick: Tick) -> list[Alert]:
        previous, self._last[tick.symbol] = self._last.get(tick.symbol), tick.price
        out = []
        for level in self.settings().get("levels") or []:
            try:
                if level["symbol"] != tick.symbol or previous is None:
                    continue
                price, above = float(level["price"]), level["above"]
            except (KeyError, TypeError, ValueError):
                continue
            crossed = previous < price <= tick.price if above else previous > price >= tick.price
            if crossed:
                out += self._alert(f"level:{tick.symbol}:{price}", "critical",
                                   f"{tick.symbol} crossed {'above' if above else 'below'} {price:,.2f}",
                                   f"now {tick.price:,.2f}", tick.symbol)
        if tick.symbol not in self.watchlist():
            return out
        crypto = self.asset_of(tick.symbol) == "crypto"
        threshold = self._num("price_jump_pct_crypto" if crypto else "price_jump_pct", 3.0 if crypto else 2.0) / 100
        window = self._num("price_jump_window_s", 300)
        change = self.prices.change(tick.symbol, window, tick.ts)
        if change is None or threshold <= 0 or abs(change) < threshold:
            return out
        severity = "critical" if abs(change) >= 2 * threshold else "warn"
        return out + self._alert(f"jump:{tick.symbol}", severity,
                                 f"{tick.symbol} {change:+.1%} in {window / 60:.0f} min", f"now {tick.price:,.2f}", tick.symbol)

    def _whale(self, whale: WhaleEvent) -> list[Alert]:
        kind, title = whale.type, f"{whale.symbol or 'Fund'} {_usd(whale.usd)}"
        if kind in ("crypto_trade", "onchain"):
            floor = self._num("whale_trade_usd" if kind == "crypto_trade" else "whale_onchain_usd", 1e6)
            severity = "critical" if whale.usd >= 10 * floor else "warn"
            label = f"{whale.side} trade" if kind == "crypto_trade" else "on-chain transfer"
            # one alert per symbol per cooldown: a busy tape must not ring all day
            return self._alert(f"whale:{kind}:{whale.symbol}", severity, f"Whale {label}: {title}", whale.detail, whale.symbol)
        if kind == "insider":
            floor = self._num("whale_insider_usd", 5e5)
            if whale.side != "buy" or whale.usd < floor:
                return []  # insiders sell for many reasons; buying is the signal
            severity = "critical" if whale.usd >= 10 * floor else "warn"
            return self._alert(f"whale:{whale.id}", severity, f"Insider buy: {title}", whale.detail, whale.symbol)
        if kind == "volume":
            severity = "warn" if whale.symbol in self.watchlist() else "info"
            return self._alert(f"whale:{whale.id}", severity, f"Unusual volume: {whale.symbol}", whale.detail, whale.symbol)
        return self._alert(f"whale:{whale.id}", "info", f"13F filed: {_usd(whale.usd)}", whale.detail, whale.symbol)

    def on_news(self, story: dict) -> list[Alert]:
        analysis = story["analysis"]
        watched = self.watchlist()
        hit = next((i["symbol"] for i in analysis["impacts"] if i["symbol"] in watched), None)
        if hit is None or analysis["weight"] < self._num("news_weight", 4):
            return []
        severity = "critical" if analysis["weight"] >= 5 else "warn"
        return self._alert(f"news:{story['id']}", severity, f"News on {hit}: {story['title']}", "", hit)

    def on_zone(self, zone: dict) -> list[Alert]:
        if zone["count"] < MIN_ZONE_HEADLINES or zone["ratio"] < self._num("conflict_ratio", 2.0):
            return []
        assets = zone.get("assets") or [{}]
        names = ", ".join(a["symbol"] for a in assets if "symbol" in a)
        return self._alert(f"conflict:{zone['zone']}:{int(self.now() // DAY)}", "warn",
                           f"Conflict heating up: {zone['zone']} ({zone['ratio']:.1f}× usual)",
                           f"{zone['count']} headlines today. Watch {names}", assets[0].get("symbol", ""))
