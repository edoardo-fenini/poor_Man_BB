"""`python -m terminal` starts the terminal; `python -m terminal doctor` checks the feeds."""
import functools
import logging
import sys
import time
from collections.abc import Awaitable, Callable

import uvicorn

from terminal.app import create_app, strip_symbols, watchlist_symbols
from terminal.bus import Bus
from terminal.events import FeedStatus
from terminal.config import Config, load_config
from terminal.feeds import crypto, finnhub, rss, sec, yahoo
from terminal.feeds.base import run_feed
from terminal.prices import Prices
from terminal.store import Store
from terminal.symbols import Symbol


def build_feeds(config: Config, store: Store, bus: Bus, prices: Prices) -> list[Callable[[], Awaitable[None]]]:
    """Every background feed, each wrapped so it restarts by itself."""

    def tracked() -> list[Symbol]:
        seen = {}
        for s in watchlist_symbols(store) + strip_symbols():
            seen.setdefault(s.id, s)
        return list(seen.values())

    def cryptos() -> list[Symbol]:
        return [s for s in tracked() if s.asset == "crypto"]

    def polled() -> list[Symbol]:
        rest = [s for s in tracked() if s.asset != "crypto"]
        return finnhub.split_live(rest)[1] if config.finnhub_key else rest

    notices: list[FeedStatus] = []

    def disabled(feed: str, key: str, where: str) -> None:
        notices.append(FeedStatus(feed, "disabled", f"Set {key} in .env — {where}", time.time()))

    async def announce() -> None:  # runs once the app is up, so the page hears about disabled feeds
        for notice in notices:
            bus.publish(notice)

    feeds = {
        "crypto": lambda: crypto.stream(bus, cryptos),
        "quotes": lambda: yahoo.poll_quotes(bus, polled),
        "rss": lambda: rss.poll(bus, rss.load_feeds()),
    }
    if config.finnhub_key:
        feeds["stocks"] = lambda: finnhub.stream(bus, config.finnhub_key, tracked)
        feeds["finnhub-news"] = lambda: finnhub.poll_news(bus, config.finnhub_key)
    else:
        disabled("stocks", "FINNHUB_KEY", "free key at finnhub.io; stocks use slower Yahoo polling meanwhile")
        disabled("finnhub-news", "FINNHUB_KEY", "free key at finnhub.io")
    if config.sec_user_agent:
        feeds["sec-8k"] = lambda: sec.poll_8k(bus, config.sec_user_agent)
    else:
        disabled("sec-8k", "SEC_USER_AGENT", 'your contact, e.g. "Jane Doe jane@example.com" (SEC requires it)')
    return [announce] + [functools.partial(run_feed, name, bus, work) for name, work in feeds.items()]


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    config = load_config()
    command = argv[0] if argv else "run"
    if command != "run":
        print(f"unknown command: {command}\nusage: python -m terminal [run]")
        return 2
    store, bus, prices = Store(config.db_path), Bus(), Prices()
    app = create_app(config, store, bus, prices, feeds=build_feeds(config, store, bus, prices))
    print(f"Terminal running at http://{config.host}:{config.port}  (Ctrl+C to stop)")
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
