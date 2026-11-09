"""`python -m terminal` starts the terminal; `python -m terminal doctor` checks the feeds."""
import functools
import logging
import sys
from collections.abc import Awaitable, Callable

import uvicorn

from terminal.app import create_app, strip_symbols, watchlist_symbols
from terminal.bus import Bus
from terminal.config import Config, load_config
from terminal.feeds import crypto, yahoo
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
        return [s for s in tracked() if s.asset != "crypto"]

    feeds = {
        "crypto": lambda: crypto.stream(bus, cryptos),
        "quotes": lambda: yahoo.poll_quotes(bus, polled),
    }
    return [functools.partial(run_feed, name, bus, work) for name, work in feeds.items()]


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
