import pytest
from fastapi.testclient import TestClient

from terminal.app import create_app
from terminal.bus import Bus
from terminal.config import Config
from terminal.events import Candle
from terminal.prices import Prices
from terminal.store import Store


async def fake_history(symbol, interval):
    return [Candle(symbol.id, interval, 1000.0 + i * 60, 10 + i, 12 + i, 9 + i, 11 + i, 100.0) for i in range(60)]


async def fake_bars(symbols, period, interval):
    return {s.id: [Candle(s.id, interval, 1000.0 + i * 86400, 10, 12, 9, 10 + i, 100.0) for i in range(3)]
            for s in symbols if s.id != "NODATA"}


class FakeSrc:
    """Stands in for terminal.feeds.fundamentals: fixed company data, no network."""

    @staticmethod
    def info(symbol):
        return {"longName": "Test Corp", "sector": "Technology", "industry": "Software", "regularMarketPrice": 100.0,
                "marketCap": 1e9, "totalDebt": 2e8, "totalCash": 5e7, "beta": 1.2, "freeCashflow": 8e7,
                "sharesOutstanding": 1e7, "trailingPE": 20.0, "fiftyTwoWeekLow": 80.0, "fiftyTwoWeekHigh": 120.0,
                "ebitda": 1.5e8, "industryKey": "software"}

    @staticmethod
    def statements(symbol):
        if symbol == "SPY":
            return {k: {"periods": [], "rows": {}} for k in ("income", "balance", "cash")}
        return {
            "income": {"periods": ["2025-12-31", "2024-12-31"], "rows": {
                "Total Revenue": [5e8, 4e8], "Gross Profit": [3e8, 2.2e8], "Operating Income": [1.2e8, 9e7],
                "EBIT": [1.2e8, 9e7], "Net Income": [9e7, 6e7], "Interest Expense": [1e7, 1e7], "Tax Rate For Calcs": [0.2, 0.2]}},
            "balance": {"periods": ["2025-12-31", "2024-12-31"], "rows": {
                "Total Assets": [1e9, 9e8], "Current Assets": [4e8, 3e8], "Current Liabilities": [2e8, 2e8],
                "Total Liabilities Net Minority Interest": [5e8, 5e8], "Stockholders Equity": [5e8, 4e8],
                "Retained Earnings": [3e8, 2e8], "Working Capital": [2e8, 1e8], "Long Term Debt": [1.5e8, 2e8],
                "Total Debt": [2e8, 2.5e8], "Ordinary Shares Number": [1e7, 1e7], "Invested Capital": [7e8, 6.5e8]}},
            "cash": {"periods": ["2025-12-31", "2024-12-31"], "rows": {"Operating Cash Flow": [1.1e8, 8e7], "Free Cash Flow": [8e7, 5e7]}},
        }

    @staticmethod
    def options(symbol, index=0):
        def side(call):
            return [{"strike": float(k), "last": 5.0, "bid": 4.9, "ask": 5.1, "volume": 2000.0 if k == 100 and call else 10.0,
                     "oi": 100.0, "iv": 0.3} for k in (90, 95, 100, 105, 110)]
        return {"expiries": ["2099-01-15", "2099-02-19"], "expiry": ["2099-01-15", "2099-02-19"][min(index, 1)],
                "calls": side(True), "puts": side(False)}


@pytest.fixture
def ctx():
    """A running app with no feeds and no network."""
    bus, store, prices = Bus(), Store(":memory:"), Prices()
    notified = []
    app = create_app(Config("", "", ""), store, bus, prices, feeds=[], history=fake_history, bars=fake_bars,
                     notify=lambda title, body: notified.append((title, body)), src=FakeSrc)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        client.bus, client.store, client.notified = bus, store, notified
        connect = client.websocket_connect  # TestClient ignores base_url for WebSockets: give the real host
        client.websocket_connect = lambda path, **kw: connect("ws://127.0.0.1:8787" + path, **kw)
        yield client


class Live:
    """The app served on a real port, for browser tests."""

    def __init__(self):
        import asyncio
        import socket
        import threading

        import uvicorn

        self.bus, self.store, self.prices = Bus(), Store(":memory:"), Prices()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.app = create_app(Config("", "", "", port=port), self.store, self.bus, self.prices, feeds=[], history=fake_history, bars=fake_bars,
                              notify=lambda title, body: None, src=FakeSrc)
        self.url = f"http://127.0.0.1:{port}"
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="error"))
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_until_complete, args=(self.server.serve(),), daemon=True)
        self.thread.start()
        import time
        while not self.server.started:
            time.sleep(0.01)

    def publish(self, event):
        self.loop.call_soon_threadsafe(self.bus.publish, event)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(5)


@pytest.fixture
def live():
    server = Live()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def browser():
    import os

    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        path = os.environ.get("CHROMIUM_PATH") or ("/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None)
        try:
            b = p.chromium.launch(executable_path=path) if path and os.path.isfile(path) else p.chromium.launch()
        except Exception as exc:  # no browser installed: skip the browser tests
            pytest.skip(f"chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, live):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(live.url)
    page.wait_for_selector("#watchlist .row")
    page.wait_for_selector("body[data-live]")  # the live channel is open: published events will arrive
    yield page
    page.close()
    assert errors == [], errors
