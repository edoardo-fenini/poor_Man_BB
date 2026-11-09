import time

from terminal.events import ConflictEvent, FeedStatus, NewsItem, Tick, WhaleEvent


def wait_for(fn, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if out := fn():
            return out
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_candles_ok(ctx):
    r = ctx.get("/api/candles", params={"symbol": "aapl", "interval": "1d"})
    body = r.json()
    assert r.status_code == 200 and body["symbol"] == "AAPL" and len(body["candles"]) == 60
    assert set(body["candles"][0]) == {"time", "open", "high", "low", "close", "volume"}


def test_candles_unknown_symbol_is_404(ctx):
    r = ctx.get("/api/candles", params={"symbol": "<script>", "interval": "1d"})
    assert r.status_code == 404 and r.json() == {"error": "symbol not found"}


def test_candles_bad_interval_is_400(ctx):
    assert ctx.get("/api/candles", params={"symbol": "AAPL", "interval": "7s"}).status_code == 400


def test_watchlist_default_then_put_roundtrip(ctx):
    assert {"id": "AAPL", "asset": "stock"} in ctx.get("/api/watchlist").json()
    assert ctx.put("/api/watchlist", json=["btc", "msft", "BTC"]).status_code == 200
    assert [w["id"] for w in ctx.get("/api/watchlist").json()] == ["BTC-USD", "MSFT"]


def test_watchlist_put_rejects_garbage(ctx):
    r = ctx.put("/api/watchlist", json=["AAPL", "<x>"])
    assert r.status_code == 400 and "<x>" in r.json()["error"]
    assert ctx.put("/api/watchlist", json={"a": 1}).status_code == 400


def test_settings_roundtrip(ctx):
    assert ctx.get("/api/settings").json() == {}
    assert ctx.put("/api/settings", json={"muted": True}).status_code == 200
    assert ctx.get("/api/settings").json() == {"muted": True}


def test_ws_receives_bus_event(ctx):
    with ctx.websocket_connect("/ws") as ws:
        ctx.portal.call(ctx.bus.publish, Tick("AAPL", 123.45, 1, 2, "x"))
        assert ws.receive_json() == {"kind": "tick", "symbol": "AAPL", "price": 123.45, "size": 1, "ts": 2, "source": "x"}


def test_news_and_whale_events_are_persisted(ctx):
    ctx.portal.call(ctx.bus.publish, NewsItem("n1", "Title", "http://x", "src", 5.0, ""))
    ctx.portal.call(ctx.bus.publish, WhaleEvent("w1", "onchain", "BTC-USD", 2e7, "", "d", "", 6.0))
    assert wait_for(lambda: ctx.get("/api/events", params={"kind": "news"}).json())[0]["title"] == "Title"
    assert ctx.get("/api/events", params={"kind": "whale"}).json()[0]["id"] == "w1"
    assert ctx.get("/api/events", params={"kind": "tick"}).status_code == 400


def news(ctx):
    return ctx.get("/api/events", params={"kind": "news"}).json()


def test_news_is_stored_with_its_analysis(ctx):
    ctx.portal.call(ctx.bus.publish, NewsItem("n1", "Apple shares jump after iPhone sales beat estimates", "http://x", "src", 5.0, ""))
    item = wait_for(lambda: news(ctx))[0]
    assert item["analysis"]["sentiment"] == 1 and item["analysis"]["weight"] == 2  # +1: AAPL is on the watchlist
    assert {"symbol": "AAPL", "direction": 1} == {k: item["analysis"]["impacts"][0][k] for k in ("symbol", "direction")}
    assert item["outlets"] == ["src"]


def test_same_story_from_more_outlets_raises_weight_and_updates_in_place(ctx):
    title = "Tesla shares plunge on recall"
    with ctx.websocket_connect("/ws") as ws:
        for source in ("a", "b", "c", "c"):
            ctx.portal.call(ctx.bus.publish, NewsItem("n2", title, "http://x", source, 5.0, ""))
        weights = [ws.receive_json()["analysis"]["weight"] for _ in range(3)]
    assert weights == [3, 3, 4]            # third outlet adds a point; the repeat from "c" sends nothing
    stored = news(ctx)
    assert len(stored) == 1 and stored[0]["outlets"] == ["a", "b", "c"] and stored[0]["source"] == "a"


def test_status_reflects_feedstatus(ctx):
    ctx.portal.call(ctx.bus.publish, FeedStatus("crypto", "down", "boom", 9.0))
    status = wait_for(lambda: ctx.get("/api/status").json())
    assert status["crypto"] == {"state": "down", "detail": "boom", "ts": 9.0}


def test_index_served(ctx):
    assert ctx.get("/").status_code == 200


def test_conflict_events_feed_zone_heat(ctx):
    now = time.time()
    for i in range(3):
        ctx.portal.call(ctx.bus.publish, ConflictEvent(f"c{i}", "Taiwan Strait", "clash", f"t{i}", "https://e.com", now))
    ctx.portal.call(ctx.bus.publish, ConflictEvent("c0", "Taiwan Strait", "clash", "t0", "https://e.com", now))  # repeat
    zones = wait_for(lambda: [z for z in ctx.get("/api/conflicts").json() if z["count"] == 3])
    assert zones[0]["zone"] == "Taiwan Strait" and {"symbol": "TSM", "direction": -1} in zones[0]["assets"]
    assert len(ctx.get("/api/conflicts").json()) >= 8
