import time

import pytest

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
    assert ctx.get("/api/settings").json()["price_jump_pct"] == 2.0     # defaults are included
    assert ctx.put("/api/settings", json={"muted": True}).status_code == 200
    assert ctx.get("/api/settings").json()["muted"] is True


def test_settings_put_rejects_bad_values(ctx):
    r = ctx.put("/api/settings", json={"price_jump_pct": -5})
    assert r.status_code == 400 and "price_jump_pct" in r.json()["error"]
    assert ctx.put("/api/settings", json=[1]).status_code == 400
    assert ctx.get("/api/settings").json()["price_jump_pct"] == 2.0


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


def alerts(ctx):
    return ctx.get("/api/events", params={"kind": "alert"}).json()


def test_whale_raises_alert_and_only_critical_notifies(ctx):
    ctx.portal.call(ctx.bus.publish, WhaleEvent("w1", "crypto_trade", "BTC-USD", 2e6, "buy", "d", "", time.time()))
    first = wait_for(lambda: alerts(ctx))
    assert first[0]["severity"] == "warn" and first[0]["symbol"] == "BTC-USD" and ctx.notified == []
    ctx.portal.call(ctx.bus.publish, WhaleEvent("w2", "crypto_trade", "ETH-USD", 5e7, "sell", "d", "", time.time()))
    wait_for(lambda: len(alerts(ctx)) == 2)
    assert len(ctx.notified) == 1 and "ETH-USD" in ctx.notified[0][0]


def test_heavy_news_on_watchlist_raises_alert_once(ctx):
    title = "Tesla shares plunge on recall"
    with ctx.websocket_connect("/ws") as ws:
        for source in ("a", "b", "c"):
            ctx.portal.call(ctx.bus.publish, NewsItem("n9", title, "http://x", source, time.time(), ""))
        kinds = [ws.receive_json()["kind"] for _ in range(4)]
    assert kinds == ["news", "news", "news", "alert"]     # weight reaches 4 with the third outlet
    assert [a["key"] for a in alerts(ctx)] == ["news:n9"]


def test_price_jump_from_ticks_raises_alert(ctx):
    now = time.time()
    for i in range(11):
        ctx.portal.call(ctx.bus.publish, Tick("AAPL", 100 + 0.3 * i, 1, now - 300 + 30 * i, "x"))
    assert wait_for(lambda: alerts(ctx))[0]["key"] == "jump:AAPL"


def test_muted_setting_keeps_alerts_silent(ctx):
    ctx.put("/api/settings", json={"muted": True})
    ctx.portal.call(ctx.bus.publish, WhaleEvent("w3", "crypto_trade", "BTC-USD", 9e7, "buy", "d", "", time.time()))
    assert wait_for(lambda: alerts(ctx))[0]["severity"] == "info" and ctx.notified == []


def test_server_stops_promptly_with_a_page_connected(live):
    from websockets.sync.client import connect

    with connect(live.url.replace("http", "ws") + "/ws"):
        started = time.time()
        live.stop()
        assert time.time() - started < 2 and not live.thread.is_alive()


def test_closes_gives_previous_and_last_close_for_tracked_symbols_in_one_call(ctx):
    closes = ctx.get("/api/closes").json()
    assert closes["AAPL"] == {"prev": 11.0, "last": 12.0, "ts": 1000.0 + 2 * 86400}
    assert {"BTC-USD", "^VIX", "EURUSD"} <= set(closes)        # watchlist and strip
    ctx.put("/api/watchlist", json=["AAPL", "NODATA"])
    assert "NODATA" not in ctx.get("/api/closes").json()


def test_closes_survives_a_failing_data_source(ctx):
    async def boom(symbols, period, interval):
        raise ConnectionError("down")
    ctx.app.state.bars = boom
    ctx.put("/api/watchlist", json=["AAPL"])                    # clears the cache
    assert ctx.get("/api/closes").json() == {}


def test_known_symbol_without_data_says_no_data_not_unknown(ctx):
    async def empty(symbol, interval):
        return []
    ctx.app.state.history = empty
    r = ctx.get("/api/candles", params={"symbol": "AAPL", "interval": "1d"})
    assert r.status_code == 404 and r.json() == {"error": "no data"}


# M1 — only pages served by the terminal itself may talk to it
def test_foreign_host_header_is_refused(ctx):
    assert ctx.get("/api/watchlist", headers={"Host": "evil.example"}).status_code == 403
    assert ctx.put("/api/watchlist", json=["AAPL"], headers={"Host": "attacker.test:8787"}).status_code == 403
    assert ctx.get("/api/watchlist", headers={"Host": "localhost:8787"}).status_code == 200


def test_websocket_from_another_origin_is_refused(ctx):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with ctx.websocket_connect("/ws", headers={"Origin": "https://evil.example"}):
            pass
    with ctx.websocket_connect("/ws", headers={"Origin": "http://127.0.0.1:8787"}):
        pass


def test_conflict_news_feeds_zone_heat_without_gdelt(ctx):
    now = time.time()
    title = "Israel launches airstrike on Iran"
    ctx.portal.call(ctx.bus.publish, NewsItem("s1", title, "https://e.com/s1", "BBC World", now, ""))
    ctx.portal.call(ctx.bus.publish, NewsItem("s1", title, "https://e.com/s1b", "Al Jazeera", now, ""))   # second outlet
    ctx.portal.call(ctx.bus.publish, NewsItem("s2", "Apple unveils new iPhone", "https://e.com/s2", "CNBC", now, ""))
    zone = wait_for(lambda: [z for z in ctx.get("/api/conflicts").json() if z["count"]])
    assert [(z["zone"], z["count"]) for z in zone] == [("Middle East / Gulf", 1)]
    events = ctx.get("/api/events", params={"kind": "conflict"}).json()
    assert [(e["title"], e["type"], e["url"]) for e in events] == [(title, "strike", "https://e.com/s1")]


def test_same_headline_from_news_and_gdelt_counts_once(ctx):
    from terminal.feeds.gdelt import parse_artlist
    now = time.time()
    title = "Missile strike hits Taiwan port"
    ctx.portal.call(ctx.bus.publish, NewsItem("s3", title, "https://e.com/s3", "BBC World", now, ""))
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    for event in parse_artlist({"articles": [{"title": title, "url": "https://g.com/1", "seendate": stamp}]}, "Taiwan Strait"):
        ctx.portal.call(ctx.bus.publish, event)
    wait_for(lambda: ctx.get("/api/events", params={"kind": "conflict"}).json())
    time.sleep(0.2)
    assert [z["count"] for z in ctx.get("/api/conflicts").json() if z["zone"] == "Taiwan Strait"] == [1]
