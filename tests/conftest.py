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


@pytest.fixture
def ctx():
    """A running app with no feeds and no network."""
    bus, store, prices = Bus(), Store(":memory:"), Prices()
    app = create_app(Config("", "", ""), store, bus, prices, feeds=[], history=fake_history)
    with TestClient(app) as client:
        client.bus, client.store = bus, store
        yield client
