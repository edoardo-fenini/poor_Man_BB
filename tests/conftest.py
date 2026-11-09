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


@pytest.fixture
def ctx():
    """A running app with no feeds and no network."""
    bus, store, prices = Bus(), Store(":memory:"), Prices()
    notified = []
    app = create_app(Config("", "", ""), store, bus, prices, feeds=[], history=fake_history, bars=fake_bars,
                     notify=lambda title, body: notified.append((title, body)))
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
                              notify=lambda title, body: None)
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
