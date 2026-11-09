"""User-tunable thresholds. Stored overrides live in the kv table under "settings"."""
from terminal.store import Store

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
