"""User-tunable thresholds. Stored overrides live in the kv table under "settings"."""
import math
import re

from terminal.prices import KEEP
from terminal.store import Store
from terminal.symbols import resolve

DEFAULTS = {
    "price_jump_pct": 2.0, "price_jump_pct_crypto": 3.0, "price_jump_window_s": 300,
    "volume_ratio": 3.0,
    "whale_trade_usd": 1_000_000, "whale_onchain_usd": 10_000_000, "whale_insider_usd": 500_000,
    "news_weight": 4, "conflict_ratio": 2.0,
    "cooldown_s": 900, "muted": False, "quiet_start": "", "quiet_end": "",  # "HH:MM", local time
    "levels": [],  # [{"symbol": "AAPL", "price": 200.0, "above": true}]
}


def load_settings(store: Store) -> dict:
    stored = store.get("settings", {})
    return {key: stored.get(key, default) for key, default in DEFAULTS.items()}


_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("expected a number, zero or above")
    return value


def _level(item) -> dict:
    symbol = resolve(item["symbol"]) if isinstance(item, dict) and isinstance(item.get("symbol"), str) else None
    if not symbol or not isinstance(item.get("above"), bool):
        raise ValueError("a level needs a known symbol, a price and above: true/false")
    price = float(_number(item.get("price")))
    if price <= 0:
        raise ValueError("a level price must be above zero")
    return {"symbol": symbol.id, "price": price, "above": item["above"]}


def validate(update: dict) -> dict:
    """Check a settings update from the page. Returns it cleaned, or raises ValueError naming the key."""
    out = {}
    for key, value in update.items():
        try:
            if key not in DEFAULTS:
                raise ValueError("unknown setting")
            if key == "levels":
                if not isinstance(value, list) or len(value) > 50:
                    raise ValueError("expected a list of at most 50 levels")
                out[key] = [_level(item) for item in value]
            elif isinstance(DEFAULTS[key], bool):
                if not isinstance(value, bool):
                    raise ValueError("expected true or false")
                out[key] = value
            elif isinstance(DEFAULTS[key], str):
                if value != "" and not (isinstance(value, str) and _TIME.match(value)):
                    raise ValueError("expected HH:MM or empty")
                out[key] = value
            else:
                out[key] = _number(value)
                if key == "price_jump_window_s" and not 30 <= value <= KEEP:
                    raise ValueError(f"must be between 30 and {KEEP:.0f} seconds")
        except (KeyError, TypeError) as exc:
            raise ValueError(f"{key}: malformed") from exc
        except ValueError as exc:
            raise ValueError(f"{key}: {exc}") from None
    return out
