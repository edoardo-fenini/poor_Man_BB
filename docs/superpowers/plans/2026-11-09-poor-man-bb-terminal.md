# Poor Man's Bloomberg Terminal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local, free-data market terminal in the browser: live prices and charts, news with affected-asset tags, whale activity, conflicts, and alerts.

**Architecture:** One asyncio Python process. Feed modules emit typed events onto an in-process bus; the brain (news rules, alert rules) consumes them; everything is stored in one SQLite file and pushed to a static web page over one WebSocket.

**Tech Stack:** Python 3.12+, uv, FastAPI, uvicorn, httpx, websockets, yfinance, feedparser, python-dotenv, stdlib sqlite3/tomllib; pytest + pytest-asyncio; plain HTML/CSS/JS with vendored TradingView Lightweight Charts.

**Spec:** `docs/superpowers/specs/2026-11-09-poor-man-bb-terminal-design.md`

## Global Constraints

- Runs on Apple Silicon macOS; every dependency must have an arm64 wheel or be pure Python.
- Free data only. Keys allowed: `FINNHUB_KEY`, `ETHERSCAN_KEY`. `SEC_USER_AGENT` is a user-supplied contact string.
- Secrets only in `.env` (git-ignored); `.env.example` documents each variable.
- No Node, no build step, no Docker. Front-end assets are vendored files.
- Localhost only: server binds `127.0.0.1`.
- Read-only: no order placement anywhere.
- Content beats looks: when a UI nicety competes with data on screen, keep the data.
- Theme: black background, amber primary text, green/red direction, monospace.
- A stale value is never shown as live: every panel shows a red `STALE <age>` tag when its feed is down.
- All timestamps are float epoch seconds, UTC.
- Tests never touch the network; feed parsers are pure functions tested with files in `tests/samples/`.
- Every feed text field is untrusted: the page inserts it with `textContent`, never `innerHTML`.

## Deviations from the spec (need user sign-off)

1. **Bitcoin on-chain source.** Spec names mempool.space. Its public API does not stream every transaction with values, so this plan uses the blockchain.info unconfirmed-transaction WebSocket. Unverified in the sandbox; `doctor` confirms it on the Mac.
2. **Sentiment word list.** The full Loughran-McDonald list cannot be fetched in the sandbox and its licence restricts redistribution. The repo ships a compact hand-written finance word list in the same style; the loader also reads a full LM file if the user drops one in `terminal/data/`.
3. **Entity list size.** Starts with about 150 of the largest US companies, major ETFs and the top 50 crypto, written by hand, not the full S&P 500. It is a CSV the user can extend.
4. **13F.** v1 lists each new 13F filing with the fund name, link, and its ten largest positions. Quarter-over-quarter position changes are not computed.
5. **Exchange labels on-chain.** Supported through `terminal/data/addresses.csv`, which ships empty: wrong labels are worse than none.

## Review Focus

1. Unknown or garbage symbol typed in the command bar → a clear "not found" message; the previous chart stays. (Task 2, Task 4)
2. Malformed feed payload (missing field, null price, truncated JSON, non-JSON) → that message is skipped and counted; the feed keeps running. (Task 3, then every parser task)
3. Market closed or weekend: no stock ticks → shown as `CLOSED`, not `STALE`, and no price-jump alert fires on the first tick after the gap. (Task 13)
4. Mac sleep then wake: a time gap in the price window → the window is dropped rather than compared across the gap; feeds reconnect. (Task 3, Task 13)
5. Hostile or messy news text (HTML tags, scripts, entities, very long titles) → rendered as plain text, truncated, never executed. (Task 8, Task 9)

---

## File Structure

```
pyproject.toml  .gitignore  .env.example  README.md
terminal/
  __main__.py      CLI: `python -m terminal [run|doctor]`
  config.py        env settings
  events.py        event dataclasses
  bus.py           async pub/sub
  store.py         SQLite persistence
  symbols.py       symbol resolver
  prices.py        last price + rolling window per symbol
  app.py           FastAPI app, REST, WebSocket hub, starts feeds
  notify.py        macOS notification
  feeds/
    base.py        run_feed: backoff + FeedStatus
    yahoo.py       history + quote polling
    crypto.py      Binance / Coinbase / Kraken trade streams
    finnhub.py     stock trade stream + news
    rss.py         RSS news
    onchain.py     Bitcoin + Ethereum large transfers
    sec.py         Form 4, 13F, 8-K
    volume.py      unusual volume
    gdelt.py       conflict articles
  brain/
    analysis.py    Analysis types + Analyser protocol
    news_rules.py  rules analyser
    conflicts.py   zone heat
    alerts.py      alert engine
  data/            entities.csv themes.toml zones.toml rss_feeds.toml
                   words_positive.txt words_negative.txt addresses.csv
  web/             index.html style.css app.js vendor/lightweight-charts.js
tests/             one test file per module, samples/ for payloads
```

---

### Task 1: Project skeleton, events, bus, store, config

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `terminal/__init__.py`, `terminal/events.py`, `terminal/bus.py`, `terminal/store.py`, `terminal/config.py`
- Test: `tests/test_bus.py`, `tests/test_store.py`

**Interfaces — Produces:**

```python
# events.py — all @dataclass(frozen=True, slots=True); each has class attr `kind: ClassVar[str]`
Tick(symbol: str, price: float, size: float, ts: float, source: str)            # kind "tick"
Candle(symbol: str, interval: str, ts: float, open: float, high: float,
       low: float, close: float, volume: float)                                 # "candle"
NewsItem(id: str, title: str, url: str, source: str, ts: float, summary: str)   # "news"
WhaleEvent(id: str, type: str, symbol: str, usd: float, side: str,
           detail: str, url: str, ts: float)                                    # "whale"
    # type in {"crypto_trade","onchain","insider","fund","volume"}; side in {"buy","sell",""}
ConflictEvent(id: str, zone: str, type: str, title: str, url: str, ts: float)   # "conflict"
Alert(key: str, severity: str, title: str, body: str, symbol: str, ts: float)   # "alert"
    # severity in {"info","warn","critical"}
FeedStatus(feed: str, state: str, detail: str, ts: float)                       # "status"
    # state in {"ok","down","disabled","closed"}
def to_json(event) -> dict   # {"kind": ..., **fields}

# bus.py
class Bus:
    def publish(self, event) -> None            # never blocks; drops oldest for a full subscriber
    def subscribe(self, maxsize: int = 1000) -> AsyncIterator   # async generator, unsubscribes on close

# store.py — stdlib sqlite3, one table events(kind, key UNIQUE per kind, ts, json), plus kv(k, v)
class Store:
    def __init__(self, path: str)               # ":memory:" allowed
    def add(self, kind: str, key: str, ts: float, data: dict) -> bool   # False if key already stored
    def recent(self, kind: str, limit: int = 200) -> list[dict]         # newest first
    def get(self, k: str, default=None)         # JSON-decoded kv
    def set(self, k: str, value) -> None
    def prune(self, kind: str, keep: int) -> None

# config.py
@dataclass(frozen=True)
class Config:
    finnhub_key: str; etherscan_key: str; sec_user_agent: str
    db_path: str = "terminal.db"; host: str = "127.0.0.1"; port: int = 8787
def load_config() -> Config                     # python-dotenv then os.environ; missing keys -> ""
```

- [ ] **Step 1:** Write `pyproject.toml` (project `terminal`, Python `>=3.12`, deps from Tech Stack, dev group pytest + pytest-asyncio, `asyncio_mode = "auto"`), `.gitignore` (`.env`, `*.db`, `.venv`, `__pycache__`), `.env.example` listing `FINNHUB_KEY`, `ETHERSCAN_KEY`, `SEC_USER_AGENT` with one comment line each saying where to get it. Run `uv sync`; expected: resolves and installs.
- [ ] **Step 2: Failing tests.**

```python
# tests/test_bus.py
async def test_subscriber_receives_published_event():
    bus = Bus(); sub = bus.subscribe(); t = asyncio.create_task(anext(sub))
    await asyncio.sleep(0); bus.publish(Tick("BTC-USD", 1.0, 2.0, 3.0, "x"))
    assert (await t).symbol == "BTC-USD"
async def test_full_subscriber_drops_oldest_not_blocks():
    bus = Bus(); sub = bus.subscribe(maxsize=2); t = asyncio.create_task(anext(sub)); await asyncio.sleep(0)
    t.cancel()  # subscriber registered but not reading
    for i in range(5): bus.publish(Tick("A", float(i), 0, 0, "x"))   # must not raise or block

# tests/test_store.py
def test_add_dedupes_by_kind_and_key():
    s = Store(":memory:")
    assert s.add("news", "k1", 1.0, {"a": 1}) is True
    assert s.add("news", "k1", 2.0, {"a": 2}) is False
    assert s.add("whale", "k1", 1.0, {}) is True
def test_recent_is_newest_first_and_limited():
    s = Store(":memory:"); [s.add("news", str(i), float(i), {"i": i}) for i in range(5)]
    assert [r["i"] for r in s.recent("news", 2)] == [4, 3]
def test_kv_roundtrip_and_default():
    s = Store(":memory:"); assert s.get("x", 7) == 7; s.set("x", {"a": [1]}); assert s.get("x") == {"a": [1]}
def test_to_json_has_kind():
    assert to_json(Tick("A", 1, 2, 3, "x"))["kind"] == "tick"
```

- [ ] **Step 3:** Run `uv run pytest tests/test_bus.py tests/test_store.py -v`. Expected: FAIL, import errors.
- [ ] **Step 4:** Implement `events.py`, `bus.py`, `store.py`, `config.py` to the signatures above.
- [ ] **Step 5:** Run the same command. Expected: all PASS.
- [ ] **Step 6:** Commit: `feat: project skeleton, events, bus, store`.

### Task 2: Symbol resolver

**Files:** Create `terminal/symbols.py`. Test `tests/test_symbols.py`.

**Interfaces — Produces:**

```python
@dataclass(frozen=True)
class Symbol:
    id: str            # canonical: "AAPL", "BTC-USD", "EURUSD", "GC=F", "^GSPC"
    asset: str         # "stock" | "crypto" | "fx" | "index" | "future"
    yahoo: str; binance: str; coinbase: str; kraken: str; finnhub: str   # "" when not applicable
def resolve(text: str) -> Symbol | None
CRYPTO: frozenset[str]     # top-50 base tickers, e.g. {"BTC","ETH","SOL",...}
FIAT: frozenset[str]       # {"USD","EUR","GBP","JPY","CHF","AUD","CAD","NZD","CNY","SEK","NOK","MXN"}
```

Rules, in order: strip, uppercase, drop a leading `$`; reject anything not matching `^[A-Z0-9.^=\-/]{1,15}$` → `None`. Starts with `^` → index, Yahoo passthrough. Ends with `=F` → future. Base in `CRYPTO` (bare `BTC`, or `BTC-USD`, `BTCUSD`, `BTC/USD`, `BTCUSDT`) → crypto: id `BTC-USD`, yahoo `BTC-USD`, binance `BTCUSDT`, coinbase `BTC-USD`, kraken `XBT/USD` for BTC else `<BASE>/USD`. Six letters or `XXX/YYY` with both halves in `FIAT` → fx: id `EURUSD`, yahoo `EURUSD=X`. Otherwise stock: id and yahoo and finnhub equal the text.

- [ ] **Step 1: Failing tests.**

```python
@pytest.mark.parametrize("text,id_,asset,yahoo", [
    ("aapl", "AAPL", "stock", "AAPL"), ("$TSLA", "TSLA", "stock", "TSLA"), ("BRK.B", "BRK.B", "stock", "BRK.B"),
    ("btc", "BTC-USD", "crypto", "BTC-USD"), ("ETHUSDT", "ETH-USD", "crypto", "ETH-USD"),
    ("eur/usd", "EURUSD", "fx", "EURUSD=X"), ("USDJPY", "USDJPY", "fx", "USDJPY=X"),
    ("^gspc", "^GSPC", "index", "^GSPC"), ("GC=F", "GC=F", "future", "GC=F")])
def test_resolve(text, id_, asset, yahoo):
    s = resolve(text); assert (s.id, s.asset, s.yahoo) == (id_, asset, yahoo)
def test_crypto_provider_symbols():
    s = resolve("BTC"); assert (s.binance, s.coinbase, s.kraken) == ("BTCUSDT", "BTC-USD", "XBT/USD")
@pytest.mark.parametrize("bad", ["", "   ", "<script>", "A" * 16, "AAPL; DROP", "héllo"])
def test_garbage_returns_none(bad): assert resolve(bad) is None
```

- [ ] **Step 2:** Run `uv run pytest tests/test_symbols.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `resolve` and the two sets.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: symbol resolver`.

### Task 3: Feed runner, price cache, Yahoo feed

**Files:** Create `terminal/feeds/__init__.py`, `terminal/feeds/base.py`, `terminal/prices.py`, `terminal/feeds/yahoo.py`. Test `tests/test_feed_base.py`, `tests/test_prices.py`, `tests/test_yahoo.py`.

**Interfaces — Consumes:** `Bus`, `Tick`, `Candle`, `FeedStatus`, `Symbol`.

**Interfaces — Produces:**

```python
# feeds/base.py
async def run_feed(name: str, bus: Bus, work: Callable[[], Awaitable[None]],
                   base_delay: float = 1.0, max_delay: float = 60.0,
                   sleep=asyncio.sleep) -> None
    # loop forever: publish FeedStatus(name,"ok") when work starts; on exception publish
    # FeedStatus(name,"down",str(exc)) and sleep base_delay*2**n capped at max_delay; n resets
    # after work ran for longer than max_delay. CancelledError propagates.
def safe(parse: Callable, raw, name: str) -> list   # parse(raw) or [] on any exception; counts skips in SKIPPED[name]
SKIPPED: dict[str, int]

# prices.py
class Prices:
    GAP = 120.0                                    # seconds; a larger gap between ticks resets the window
    def update(self, tick: Tick) -> None
    def last(self, symbol: str) -> float | None
    def age(self, symbol: str, now: float) -> float | None
    def change(self, symbol: str, seconds: float, now: float) -> float | None
        # fractional move vs the oldest tick inside the window; None if window has no tick older
        # than seconds/2, or the window was reset by a gap

# feeds/yahoo.py
INTERVALS: dict[str, tuple[str, str]]   # ui interval -> (yahoo interval, range):
    # "1m":("1m","5d") "5m":("5m","1mo") "15m":("15m","1mo") "1h":("60m","6mo")
    # "1d":("1d","5y") "1wk":("1wk","max") "1mo":("1mo","max")
def frame_to_candles(df, symbol: str, interval: str) -> list[Candle]   # drops rows with NaN OHLC
async def history(symbol: Symbol, interval: str) -> list[Candle]       # yfinance in asyncio.to_thread
def frame_to_ticks(df, ids: dict[str, str], now: float) -> list[Tick]  # last row per yahoo symbol -> Tick(id,...,"yahoo")
async def poll_quotes(bus: Bus, symbols: Callable[[], list[Symbol]], every: float = 15.0) -> None
    # one batched yf.download(period="1d", interval="1m") per cycle
```

- [ ] **Step 1: Failing tests.**

```python
# tests/test_feed_base.py
async def test_run_feed_reports_down_and_backs_off():
    bus = Bus(); delays = []; calls = 0
    async def work():
        nonlocal calls; calls += 1
        if calls <= 3: raise RuntimeError("boom")
        raise asyncio.CancelledError
    async def fake_sleep(d): delays.append(d)
    with pytest.raises(asyncio.CancelledError):
        await run_feed("x", bus, work, base_delay=1, max_delay=3, sleep=fake_sleep)
    assert delays == [1, 2, 3]
def test_safe_swallows_and_counts():
    assert safe(lambda r: 1 / 0, {}, "f") == [] and SKIPPED["f"] == 1
    assert safe(lambda r: [r], 5, "f") == [5]

# tests/test_prices.py
def test_change_over_window():
    p = Prices(); [p.update(Tick("A", px, 0, ts, "x")) for ts, px in [(0, 100), (60, 101), (300, 103)]]
    assert p.change("A", 300, now=300) == pytest.approx(0.03)
def test_gap_resets_window():           # Review Focus 4: sleep/wake
    p = Prices(); p.update(Tick("A", 100, 0, 0, "x")); p.update(Tick("A", 110, 0, 5000, "x"))
    assert p.change("A", 300, now=5000) is None and p.last("A") == 110
def test_unknown_symbol(): assert Prices().last("ZZ") is None

# tests/test_yahoo.py — build small pandas frames inline
def test_frame_to_candles_drops_nan_rows():      # 3 rows, middle has NaN close -> 2 candles, ts ascending
def test_frame_to_candles_empty_frame_is_empty_list():
def test_frame_to_ticks_takes_last_row_per_symbol():   # multi-index columns for ["AAPL","EURUSD=X"]
```

- [ ] **Step 2:** Run `uv run pytest tests/test_feed_base.py tests/test_prices.py tests/test_yahoo.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement to the signatures. `Prices` keeps a `deque[(ts, price)]` per symbol trimmed to 15 minutes.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: feed runner, price cache, yahoo feed`.

### Task 4: API, WebSocket hub, CLI

**Files:** Create `terminal/app.py`, `terminal/__main__.py`. Test `tests/test_app.py`.

**Interfaces — Consumes:** everything from Tasks 1–3.

**Interfaces — Produces:**

```python
# app.py
DEFAULT_WATCHLIST = ["^GSPC","^IXIC","AAPL","MSFT","NVDA","TSLA","SPY","QQQ","BTC","ETH","EURUSD","GC=F","CL=F"]
STRIP = ["^GSPC","^IXIC","^VIX","DX-Y.NYB","^TNX","GC=F","CL=F","BTC"]
def create_app(config: Config, store: Store, bus: Bus, prices: Prices,
               feeds: list[Callable[[], Awaitable[None]]] | None = None,
               history=yahoo.history) -> FastAPI
    # lifespan starts each feed as a task and cancels them on shutdown
# REST (JSON):
#   GET  /api/candles?symbol=&interval=   -> {"symbol": id, "candles":[{time,open,high,low,close,volume}]}
#                                            404 {"error":"symbol not found"} / 400 bad interval
#   GET  /api/events?kind=&limit=         -> list[dict], kind in news|whale|conflict|alert
#   GET  /api/watchlist                   -> list[{id, asset}]
#   PUT  /api/watchlist  body ["AAPL",..] -> stores resolved ids; 400 listing unresolvable entries
#   GET  /api/settings / PUT /api/settings body {key: value}
#   GET  /api/status                      -> {feed: {state, detail, ts}}
#   WS   /ws                              -> every bus event as to_json(event)
#   GET  /  and /static/*                 -> terminal/web
# __main__.py: `python -m terminal` (run) starts uvicorn on config.host:port and prints the URL
```

The app holds one bus subscriber that persists `news`, `whale`, `conflict`, `alert` events via `Store.add` and records the latest `FeedStatus` per feed.

- [ ] **Step 1: Failing tests** (FastAPI `TestClient`, fake `history` returning two candles, no feeds).

```python
def test_candles_ok():            # GET /api/candles?symbol=aapl&interval=1d -> 200, symbol "AAPL", 2 candles
def test_candles_unknown_symbol_is_404():      # symbol="<script>" -> 404 {"error": "symbol not found"}
def test_candles_bad_interval_is_400():
def test_watchlist_default_then_put_roundtrip():   # PUT ["btc","msft"] -> GET ids ["BTC-USD","MSFT"]
def test_watchlist_put_rejects_garbage():          # PUT ["AAPL","<x>"] -> 400, body names "<x>"
def test_ws_receives_bus_event():   # connect /ws, bus.publish(Tick(...)), receive_json()["kind"] == "tick"
def test_news_event_is_persisted():  # publish NewsItem, GET /api/events?kind=news returns it
def test_status_reflects_feedstatus():
```

- [ ] **Step 2:** Run `uv run pytest tests/test_app.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `create_app` and `__main__`.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: REST API, WebSocket hub, CLI`.

### Task 5: Web shell — theme, watchlist, command bar, chart, top strip

**Files:** Create `terminal/web/index.html`, `terminal/web/style.css`, `terminal/web/app.js`, `terminal/web/vendor/lightweight-charts.js`. Test `tests/test_web_smoke.py`.

**Interfaces — Consumes:** Task 4 REST and `/ws`.

Decisions:
- Vendor Lightweight Charts v4.2.x standalone build: `npm pack lightweight-charts@4.2.3`, copy `dist/lightweight-charts.standalone.production.js`, keep its licence header.
- Layout is CSS grid, no framework: top strip; command bar; left watchlist (240px); centre chart; right news (380px); bottom tabbed panel (Whales, Conflicts, Alerts) at 30% height. Panels are empty placeholders until their task.
- Colours: background `#000`, panel border `#2a2a2a`, text `#ffb000`, dim `#8a6a00`, up `#00d26a`, down `#ff3b30`, stale tag `#ff3b30`. Font: `ui-monospace, "SF Mono", Menlo, monospace`, 12px.
- Command bar is focused on load and on `/`. Enter → `GET /api/candles`; on 404 show `NOT FOUND: <text>` in the bar and leave the chart unchanged.
- Interval buttons: `1m 5m 15m 1h 1d 1wk 1mo`. Chart: candles, volume histogram, SMA 20 and SMA 50, RSI 14 in a second pane; indicators are computed in `app.js` from candles.
- `tick` events update the watchlist row, the strip, and the last candle of the open chart.
- `app.js` exposes one helper `el(tag, text, cls)` that sets `textContent`; no `innerHTML` anywhere in the file.
- Each panel header has a status slot fed by `status` events and `/api/status`: `LIVE`, `CLOSED`, or `STALE <age>`.

- [ ] **Step 1: Failing test** (Playwright, pre-installed; start the app in-process on a free port with a fake `history` and no feeds).

```python
def test_page_loads_and_chart_renders():     # goto /, type "AAPL" Enter, a <canvas> exists in #chart
def test_unknown_symbol_shows_not_found():   # type "<script>" Enter -> #cmd-msg contains "NOT FOUND"
def test_tick_updates_watchlist_row():       # bus.publish(Tick("AAPL",123.45,...)) -> row text contains "123.45"
def test_no_innerhtml_in_app_js():           # "innerHTML" not in open("terminal/web/app.js").read()
```

- [ ] **Step 2:** Run `uv run pytest tests/test_web_smoke.py -v --trace off`. Expected: FAIL.
- [ ] **Step 3:** Vendor the chart library; write the three web files.
- [ ] **Step 4:** Run again. Expected: PASS. Take one screenshot at 1440×900 and look at it: all panels visible, no overlap, amber on black.
- [ ] **Step 5:** Commit: `feat: web shell with chart, watchlist, command bar`.

### Task 6: Crypto trade streams

**Files:** Create `terminal/feeds/crypto.py`, `tests/samples/binance_trade.json`, `coinbase_match.json`, `kraken_trade.json`. Test `tests/test_crypto.py`.

**Interfaces — Produces:**

```python
@dataclass(frozen=True)
class Trade: symbol: str; price: float; size: float; side: str; ts: float; source: str   # side "buy"|"sell" (taker)
def parse_binance(msg: dict) -> list[Trade]    # combined stream aggTrade; "m" true -> taker sold
def parse_coinbase(msg: dict) -> list[Trade]   # type "match"/"last_match"; "side" is the maker side -> invert
def parse_kraken(msg: dict) -> list[Trade]     # v2 "trade" channel, data[] entries
async def stream(bus: Bus, symbols: Callable[[], list[Symbol]],
                 on_trade: Callable[[Trade], None] = lambda t: None) -> None
    # tries Binance, then Coinbase, then Kraken; a provider that fails to connect or to deliver a
    # message within 20 s is abandoned for the next; raises when all three fail (run_feed retries).
    # Publishes Tick per trade, throttled to one Tick per symbol per 250 ms; calls on_trade for every trade.
```

URLs: `wss://stream.binance.com:9443/stream?streams=<sym>@aggTrade/...`, `wss://ws-feed.exchange.coinbase.com` (subscribe `matches`), `wss://ws.kraken.com/v2` (subscribe `trade`).

- [ ] **Step 1:** Write the three sample files from each exchange's documented message shape, plus one subscription-ack message per exchange.
- [ ] **Step 2: Failing tests.**

```python
def test_binance_sample():    # -> Trade("BTC-USD", price, qty, "sell" when m is true, T/1000, "binance")
def test_coinbase_sample_inverts_maker_side():
def test_kraken_sample_maps_xbt_to_btc():
@pytest.mark.parametrize("p", [parse_binance, parse_coinbase, parse_kraken])
def test_non_trade_and_malformed_messages_yield_nothing(p):   # ack, {}, {"data": None}, price "abc"
    for m in ({}, {"data": None}, {"type": "subscriptions"}, {"data": {"p": "abc"}}): assert safe(p, m, "c") == []
```

- [ ] **Step 3:** Run `uv run pytest tests/test_crypto.py -v`. Expected: FAIL.
- [ ] **Step 4:** Implement parsers and `stream`.
- [ ] **Step 5:** Run again. Expected: PASS.
- [ ] **Step 6:** Register `stream` in `__main__` via `run_feed("crypto", ...)` with the watchlist's crypto symbols. Commit: `feat: live crypto trade streams with fallback`.

### Task 7: Finnhub stock stream

**Files:** Create `terminal/feeds/finnhub.py`, `tests/samples/finnhub_trade.json`. Test `tests/test_finnhub.py`.

**Interfaces — Produces:**

```python
LIVE_LIMIT = 50
def parse_trades(msg: dict) -> list[Tick]              # type "trade", data[] of {s,p,v,t(ms)}; source "finnhub"
def split_live(symbols: list[Symbol]) -> tuple[list[Symbol], list[Symbol]]
    # (live, polled): US stocks/ETFs in watchlist order up to LIVE_LIMIT go live; everything else polled
async def stream(bus: Bus, key: str, symbols: Callable[[], list[Symbol]]) -> None
    # wss://ws.finnhub.io?token=<key>; resubscribes when the watchlist changes
```

In `__main__`: when `finnhub_key` is empty publish `FeedStatus("stocks","disabled","Set FINNHUB_KEY in .env — free key at finnhub.io")` and poll all non-crypto symbols through Yahoo; otherwise live symbols stream and the rest are polled.

- [ ] **Step 1: Failing tests.**

```python
def test_parse_trades_sample():               # two trades -> two Ticks, ts in seconds
def test_parse_ping_and_malformed():          # {"type":"ping"}, {}, {"type":"trade","data":None} -> []
def test_split_live_caps_at_50_and_excludes_non_stock():
    syms = [resolve("BTC"), resolve("EURUSD"), resolve("^GSPC")] + [resolve(f"S{i}") for i in range(60)]
    live, polled = split_live(syms)
    assert len(live) == 50 and all(s.asset == "stock" for s in live) and len(polled) == 13
```

- [ ] **Step 2:** Run `uv run pytest tests/test_finnhub.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: live US stock stream via Finnhub with Yahoo fallback`.

### Task 8: News feeds

**Files:** Create `terminal/feeds/rss.py`, `terminal/data/rss_feeds.toml`, `tests/samples/feed.xml`, `tests/samples/finnhub_news.json`, `tests/samples/edgar_8k.atom`; modify `terminal/feeds/finnhub.py`, create `terminal/feeds/sec.py`. Test `tests/test_news_feeds.py`.

**Interfaces — Produces:**

```python
# rss.py
def clean(text: str, limit: int = 300) -> str     # strip tags, unescape entities, collapse spaces, truncate
def news_id(title: str) -> str                    # sha1 of lowercase alphanumerics of the title; merges outlets
def parse_feed(xml: bytes, source: str) -> list[NewsItem]      # feedparser; skips entries with no title or link
async def poll(bus: Bus, feeds: list[dict], every: float = 45.0) -> None
# finnhub.py
def parse_news(items: list[dict]) -> list[NewsItem]            # /api/v1/news?category=general
async def poll_news(bus: Bus, key: str, every: float = 60.0) -> None
# sec.py
SEC_HEADERS: Callable[[str], dict]                             # {"User-Agent": sec_user_agent}
def parse_8k(atom: bytes) -> list[NewsItem]                    # title "<Company> filed 8-K", source "SEC"
async def poll_8k(bus: Bus, user_agent: str, every: float = 60.0) -> None
```

`rss_feeds.toml` holds `[[feed]] name, url, tier` (tier 1–3) for: Yahoo Finance, CNBC top news, CNBC markets, MarketWatch top stories, BBC business, BBC world, Al Jazeera, Guardian business, Federal Reserve press releases, CoinDesk, Cointelegraph, The Block, Investing.com news, Nasdaq markets. Each feed fails independently. SEC feeds are disabled with a `FeedStatus` naming `SEC_USER_AGENT` when it is empty.

- [ ] **Step 1: Failing tests.**

```python
def test_clean_strips_html_and_scripts():     # Review Focus 5
    assert clean('<b>Fed</b> &amp; <script>alert(1)</script>rates') == "Fed & rates"
def test_clean_truncates():  assert len(clean("x" * 1000)) == 300
def test_news_id_merges_outlets():
    assert news_id("Fed cuts rates!") == news_id("fed  cuts rates") != news_id("Fed holds rates")
def test_parse_feed_sample():                 # 3 entries, one without link -> 2 items, ts parsed, source set
def test_parse_feed_garbage_bytes(): assert safe(lambda b: parse_feed(b, "x"), b"\x00not xml", "rss") == []
def test_parse_finnhub_news_sample():
def test_parse_8k_sample():
```

- [ ] **Step 2:** Run `uv run pytest tests/test_news_feeds.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement; register the three pollers in `__main__`.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: news feeds (RSS, Finnhub, SEC 8-K)`.

### Task 9: News rules and news panel

**Files:** Create `terminal/brain/__init__.py`, `terminal/brain/analysis.py`, `terminal/brain/news_rules.py`, `terminal/data/entities.csv`, `themes.toml`, `words_positive.txt`, `words_negative.txt`; modify `terminal/app.py`, `terminal/web/app.js`, `style.css`. Test `tests/test_news_rules.py`.

**Interfaces — Produces:**

```python
# analysis.py
@dataclass(frozen=True)
class Impact: symbol: str; direction: int; reason: str        # direction -1 | 0 | +1
@dataclass(frozen=True)
class Analysis: impacts: list[Impact]; sentiment: int; weight: int; reasons: list[str]   # weight 1..5
class Analyser(Protocol):
    def analyse(self, item: NewsItem, watchlist: set[str], outlets: int = 1) -> Analysis
# news_rules.py
class RulesAnalyser:                                           # implements Analyser
    def __init__(self, data_dir: Path = DATA_DIR)
```

Data formats:
- `entities.csv`: `symbol,names,ambiguous,context` — `names` and `context` are `|`-separated. Example row: `AAPL,Apple|Apple Inc,1,iphone|mac|tim cook|shares|stock|earnings|app store`.
- `themes.toml`: `[[theme]] name, keywords = [...], impacts = [{symbol, direction}]`. Ship at least: rates/Fed, inflation, oil supply, chip export controls, bank stress, tariffs, crypto regulation, crypto hack, ETF approval, earnings season, AI capex, natural gas, gold/safe haven, China stimulus, shipping disruption.
- Word lists: one lowercase word per line.

Rules:
- Entity match is case-sensitive on names with word boundaries; `$TICKER` always matches; a bare ticker matches only if it is 3+ letters, uppercase in the text, and not ambiguous. An ambiguous entity needs one `context` term anywhere in title + summary.
- Sentiment: `+1` if positive words − negative words ≥ 1, `−1` if ≤ −1, else `0`; a negator (`not`, `no`, `never`, `fails to`) within three words before a term flips it. Entity impacts take the story's sentiment as direction; theme impacts take the theme's direction.
- Weight starts at 1; `+1` source tier 1; `+1` any of `{crash, plunge, surge, soar, halt, default, bankrupt, emergency, war, invasion, record}`; `+1` when `outlets ≥ 3`; `+1` when any impact is in the watchlist; capped at 5.
- `reasons` holds one string per fired rule, e.g. `entity AAPL: "Apple" + context "iphone"`, `theme oil supply: "OPEC"`.

`app.py` change: the persistence subscriber runs the analyser on each `NewsItem`, counts outlets per `news_id`, stores and broadcasts `{...item, "analysis": {...}}`. News panel: newest first, each row shows time, source, title, ticker chips coloured by direction, weight as 1–5 bars; click expands the reasons; clicking a chip loads the chart.

- [ ] **Step 1: Failing tests.**

```python
CASES = [  # title, expected symbols subset, sentiment
 ("Apple shares jump after iPhone sales beat estimates", {"AAPL"}, 1),
 ("An apple a day keeps the doctor away, study finds", set(), 0),
 ("Target cuts forecast as shoppers pull back", {"TGT"}, -1),
 ("Regulators target misleading ads", set(), 0),
 ("$NVDA upgraded by analysts", {"NVDA"}, 1),
 ("OPEC agrees surprise output cut", {"CL=F", "XLE"}, 0),
 ("Fed signals rate cuts as inflation cools", {"TLT", "^TNX"}, 1),
 ("US tightens chip export controls on China", {"NVDA", "AMD", "TSM", "SMH"}, -1),
 ("Bitcoin ETF approval sparks rally", {"BTC-USD"}, 1),
 ("Company does not expect losses to widen", set(), 0),
]
@pytest.mark.parametrize("title,symbols,sent", CASES)
def test_rules(title, symbols, sent):
    a = RulesAnalyser().analyse(NewsItem("i", title, "", "x", 0, ""), set())
    assert symbols <= {i.symbol for i in a.impacts} and a.sentiment == sent
    if not symbols: assert not {i.symbol for i in a.impacts} & {"AAPL", "TGT"}
def test_weight_rises_with_watchlist_and_outlets():
    item = NewsItem("i", "Tesla shares plunge on recall", "", "x", 0, "")
    low = RulesAnalyser().analyse(item, set()); high = RulesAnalyser().analyse(item, {"TSLA"}, outlets=3)
    assert high.weight == low.weight + 2 and 1 <= low.weight and high.weight <= 5
def test_every_impact_has_a_reason():
def test_empty_title_gives_neutral_weight_one():
```

- [ ] **Step 2:** Run `uv run pytest tests/test_news_rules.py -v`. Expected: FAIL.
- [ ] **Step 3:** Write the data files and implement `RulesAnalyser`.
- [ ] **Step 4:** Run again. Expected: PASS. Then wire `app.py` and the panel; add to `tests/test_web_smoke.py`: `test_news_title_with_html_is_shown_as_text` (publish a NewsItem titled `<img src=x onerror=alert(1)>`; the row's text contains `<img` and the page has no `img` element in `#news`). Run `uv run pytest -v --trace off`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: rules-based news impact analysis and news panel`.

### Task 10: Crypto whales — large trades and on-chain

**Files:** Create `terminal/feeds/onchain.py`, `terminal/data/addresses.csv` (header only: `chain,address,label`), `tests/samples/btc_utx.json`, `eth_block.json`, `eth_logs.json`; modify `terminal/feeds/crypto.py` wiring in `__main__`. Test `tests/test_whales_crypto.py`.

**Interfaces — Produces:**

```python
# crypto.py (add)
def big_trade(trade: Trade, min_usd: float) -> WhaleEvent | None
    # usd = price*size; id f"{source}:{symbol}:{ts}:{price}:{size}"; type "crypto_trade"
# onchain.py
def parse_btc_tx(msg: dict, btc_usd: float, min_usd: float, labels: dict[str, str]) -> WhaleEvent | None
    # blockchain.info "utx": value = sum of outputs not returning to an input address (excludes change)
def parse_eth_block(block: dict, eth_usd: float, min_usd: float, labels: dict) -> list[WhaleEvent]
def parse_token_logs(logs: list[dict], token: str, decimals: int, min_usd: float, labels: dict) -> list[WhaleEvent]
    # ERC-20 Transfer logs for USDT (6) and USDC (6); 1 token = 1 USD
async def btc_stream(bus: Bus, prices: Prices, settings: Callable[[], dict]) -> None   # wss://ws.blockchain.info/inv
async def eth_poll(bus: Bus, key: str, prices: Prices, settings: Callable[[], dict], every: float = 12.0) -> None
```

Thresholds come from settings keys `whale_trade_usd` (default 1_000_000) and `whale_onchain_usd` (default 10_000_000). `detail` reads `<amount> <asset> <from label or short address> → <to …>`; `url` links to the explorer. On-chain events are skipped while the price is unknown. `eth_poll` is disabled with a `FeedStatus` naming `ETHERSCAN_KEY` when the key is empty.

- [ ] **Step 1: Failing tests.**

```python
def test_big_trade_threshold():
    t = Trade("BTC-USD", 100_000, 12, "buy", 1.0, "binance")
    assert big_trade(t, 1_000_000).usd == 1_200_000 and big_trade(t, 2_000_000) is None
def test_btc_tx_excludes_change_output():      # input addr A; outputs 200 BTC to B and 300 BTC back to A -> 200 BTC
def test_btc_tx_below_threshold_is_none():
def test_btc_tx_applies_label():               # labels {"B": "Exchange X"} -> detail contains "Exchange X"
def test_eth_block_sample():                   # 3 txs, one above threshold -> 1 event, usd = eth * price
def test_token_logs_decimals():                # 25_000_000 * 10**6 raw USDT -> usd 25_000_000
def test_malformed_inputs_are_skipped():       # {}, missing "out", hex garbage -> None / []
```

- [ ] **Step 2:** Run `uv run pytest tests/test_whales_crypto.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement; in `__main__` pass `on_trade` to `crypto.stream` so big trades publish `WhaleEvent`s, and register `btc_stream` and `eth_poll`.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: crypto whale tracking (large trades, on-chain)`.

### Task 11: Stock whales — SEC filings, unusual volume, whales panel

**Files:** Modify `terminal/feeds/sec.py`; create `terminal/feeds/volume.py`, `tests/samples/edgar_form4.atom`, `form4.xml`, `edgar_13f.atom`, `infotable.xml`; modify `terminal/web/app.js`. Test `tests/test_whales_stock.py`.

**Interfaces — Produces:**

```python
# sec.py (add)
def parse_current(atom: bytes) -> list[dict]             # [{accession, cik, company, form, url, ts}]
def parse_form4(xml: bytes, filing: dict) -> list[WhaleEvent]
    # non-derivative transactions with code P (buy) or S (sell); usd = shares*price; type "insider";
    # one event per filing per side, summed; detail "<owner> (<title>) bought 10,000 @ $52.10"
def parse_13f(xml: bytes, filing: dict, top: int = 10) -> WhaleEvent
    # type "fund"; usd = total value; detail = top positions "NAME 12.3%"; value units detected:
    # filings from 2023 on report dollars, earlier ones thousands
async def poll_filings(bus: Bus, user_agent: str, every: float = 60.0) -> None   # ≤ 5 requests/second to sec.gov
# volume.py
def profile(history: dict[str, list[Candle]]) -> dict[str, list[float]]
    # per symbol: mean cumulative volume at each 5-minute slot of the session over the last 20 sessions
def unusual(symbol: str, today: list[Candle], prof: list[float], ratio: float) -> WhaleEvent | None
    # cumulative volume now / profile at the same slot ≥ ratio; needs ≥ 3 slots today and profile > 0;
    # id f"vol:{symbol}:{date}"; type "volume"; detail "3.4× normal volume"
async def poll(bus: Bus, symbols: Callable[[], list[Symbol]], settings: Callable[[], dict], every: float = 60.0) -> None
```

Settings: `whale_insider_usd` (default 500_000) is applied by the alert engine, not here: every parsed insider trade is shown. `volume_ratio` default 3.0. The volume universe is watchlist stocks plus every stock in `entities.csv`; the profile is rebuilt once per day.

Whales panel: table of time, type, symbol, USD (abbreviated `$12.3M`), side coloured, detail, link. A filter row toggles the five types.

- [ ] **Step 1: Failing tests.**

```python
def test_parse_current_sample():               # 2 entries -> accession, form "4", url ends ".txt" or index
def test_form4_purchase():                     # code P, 10000 sh @ 52.10 -> side "buy", usd 521_000
def test_form4_ignores_grants_and_derivatives():   # codes A, M, F and derivativeTable -> []
def test_13f_top_positions_and_total():        # 3 holdings -> usd = sum, detail lists largest first
def test_profile_and_unusual():                # 20 flat sessions of 100/slot; today 400/slot for 3 slots -> ratio 4.0
def test_unusual_needs_three_slots_and_nonzero_profile():
def test_sec_parsers_survive_garbage():        # b"", b"<html>", truncated xml -> [] via safe
```

- [ ] **Step 2:** Run `uv run pytest tests/test_whales_stock.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement; register pollers; build the panel.
- [ ] **Step 4:** Run again plus `tests/test_web_smoke.py::test_whale_row_renders` (publish one WhaleEvent, row shows `$1.2M`). Expected: PASS.
- [ ] **Step 5:** Commit: `feat: insider, fund and unusual-volume tracking; whales panel`.

### Task 12: Conflicts

**Files:** Create `terminal/feeds/gdelt.py`, `terminal/brain/conflicts.py`, `terminal/data/zones.toml`, `tests/samples/gdelt_artlist.json`; modify `terminal/web/app.js`. Test `tests/test_conflicts.py`.

**Interfaces — Produces:**

```python
# gdelt.py
TYPES: dict[str, tuple[str, ...]]    # "strike": ("airstrike","missile","drone","strike"), "clash": (...),
                                     # "sanction": (...), "blockade": (...), "unrest": (...)
def classify(title: str) -> str      # first matching type, else "other"
def parse_artlist(payload: dict, zone: str) -> list[ConflictEvent]   # id = news_id(title)
async def poll(bus: Bus, zones: list[dict], every: float = 900.0) -> None
    # GDELT DOC 2.0 artlist, one query per zone, timespan 1h, English, sort datedesc
# conflicts.py
def heat(day_counts: dict[str, int], today: str) -> tuple[int, float]
    # (today's count, ratio vs mean of the previous 7 days; ratio 0.0 when that mean is 0)
class Zones:
    def __init__(self, zones: list[dict], store: Store)
    def record(self, ev: ConflictEvent) -> dict     # increments today's count, returns zone_state
    def state(self) -> list[dict]                    # [{zone, count, ratio, assets:[{symbol,direction}]}]
```

`zones.toml`: `[[zone]] name, query, assets = [{symbol, direction}]`. Ship: Middle East / Gulf (`CL=F +`, `BZ=F +`, `GC=F +`, `XLE +`, `ITA +`, `JETS −`), Red Sea / shipping (`BZ=F +`, `ZIM +`), Taiwan Strait (`TSM −`, `SMH −`, `NVDA −`, `GC=F +`), Ukraine / Black Sea (`ZW=F +`, `NG=F +`, `ITA +`, `EZU −`), Korean peninsula (`EWY −`, `GC=F +`), South China Sea (`FXI −`, `GC=F +`), India–Pakistan (`INDA −`), Sahel / West Africa (`GC=F +`, `URA +`).

Add `GET /api/conflicts` → `Zones.state()`. Conflicts panel: left a zone table (name, heat today, ratio with ▲/▼, asset chips), right the latest headlines for the selected zone.

- [ ] **Step 1: Failing tests.**

```python
def test_classify():  assert classify("Missile strike hits port") == "strike" and classify("Talks resume") == "other"
def test_parse_artlist_sample():          # 3 articles, one duplicate title -> 2 distinct ids
def test_parse_artlist_empty_or_html_error_page():   # {} and {"articles": None} -> []
def test_heat_ratio():
    counts = {f"2026-11-0{d}": 10 for d in range(1, 8)} | {"2026-11-08": 25}
    assert heat(counts, "2026-11-08") == (25, 2.5)
def test_heat_no_history_is_zero_ratio():  assert heat({"2026-11-08": 5}, "2026-11-08") == (5, 0.0)
def test_zones_record_persists_counts():   # two Zones instances on one Store see the same count
```

- [ ] **Step 2:** Run `uv run pytest tests/test_conflicts.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement; register the poller; build the panel.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: conflict zones with heat and affected assets`.

### Task 13: Alert engine, notifications, settings panel

**Files:** Create `terminal/brain/alerts.py`, `terminal/notify.py`; modify `terminal/app.py`, `terminal/web/app.js`, `style.css`. Test `tests/test_alerts.py`.

**Interfaces — Produces:**

```python
# alerts.py
DEFAULTS = {"price_jump_pct": 2.0, "price_jump_pct_crypto": 3.0, "price_jump_window_s": 300,
            "volume_ratio": 3.0, "whale_trade_usd": 1_000_000, "whale_onchain_usd": 10_000_000,
            "whale_insider_usd": 500_000, "news_weight": 4, "conflict_ratio": 2.0,
            "cooldown_s": 900, "muted": False, "quiet_start": "", "quiet_end": "",   # "HH:MM" local
            "levels": []}                                                             # [{symbol, price, above: bool}]
class AlertEngine:
    def __init__(self, settings: Callable[[], dict], prices: Prices, watchlist: Callable[[], set[str]],
                 asset_of: Callable[[str], str], now=time.time)
    def on_event(self, event) -> list[Alert]
# notify.py
def mac_notify(title: str, body: str, run=subprocess.run) -> None
    # osascript -e 'display notification ... with title ... sound name "Glass"'; arguments passed as
    # argv to a fixed script (no string interpolation of feed text); no-op when not on macOS
```

Rules:
- Price jump: on each `Tick` for a watchlist symbol, `prices.change(symbol, window)`; fires when `abs(change) ≥ pct` (crypto uses the crypto pct). `None` change never fires. Severity `critical` at ≥ 2× the threshold, else `warn`. Key `jump:<symbol>`.
- Whale: `crypto_trade` and `onchain` already exceed their thresholds → `warn`, `critical` at ≥ 10× the threshold; `insider` fires only for buys ≥ `whale_insider_usd`; `volume` → `warn`; `fund` → `info`. Key `whale:<id>`.
- News: analysis weight ≥ `news_weight` and an impact in the watchlist → `warn`, `critical` at weight 5. Key `news:<id>`.
- Conflict: zone ratio ≥ `conflict_ratio` → `warn`. Key `conflict:<zone>:<date>`.
- Level: crossing in the configured direction between two consecutive ticks → `critical`. Key `level:<symbol>:<price>`.
- Cooldown: a key that fired within `cooldown_s` is suppressed.
- `muted` or inside quiet hours: `warn` and `critical` are downgraded to `info`.
- Market closed: `__main__` publishes `FeedStatus("stocks","closed")` outside 04:00–20:00 New York time on weekdays; the page shows `CLOSED` instead of `STALE` for stock rows.

`app.py`: route every bus event through the engine; publish resulting `Alert`s; call `mac_notify` for `critical`. Page: `warn` and `critical` show a top banner for 15 s and play a short beep generated with WebAudio (no audio file); the Alerts tab lists all; a Settings dialog edits every `DEFAULTS` key and the level list via `PUT /api/settings`; a mute button sits in the top strip.

- [ ] **Step 1: Failing tests** (`now` injected; a helper feeds ticks).

```python
def test_price_jump_fires_warn_at_threshold():      # AAPL 100 -> 102.1 within 300 s -> one warn, key "jump:AAPL"
def test_price_jump_critical_at_double():           # 100 -> 104.5 -> critical
def test_crypto_uses_crypto_threshold():            # BTC +2.5% -> nothing; +3.1% -> warn
def test_non_watchlist_symbol_never_fires():
def test_cooldown_suppresses_repeat_then_allows():  # second jump at +100 s -> []; at +901 s -> fires
def test_no_alert_across_gap():                     # Review Focus 3/4: tick, 6 h gap, tick +5% -> []
def test_insider_sell_and_small_buy_do_not_fire():  # sell $5M -> []; buy $400k -> []; buy $600k -> warn
def test_whale_critical_at_ten_times():
def test_news_needs_weight_and_watchlist():
def test_level_cross_up_only_once():
def test_muted_downgrades_to_info():
def test_quiet_hours_wrap_midnight():               # 22:00–07:00, now 02:00 local -> info
def test_mac_notify_passes_text_as_argv():
    calls = []; mac_notify('a"; do shell script "x', "b", run=lambda *a, **k: calls.append(a[0]))
    assert calls == [] or 'do shell script "x' not in calls[0][2]   # script arg is fixed
```

- [ ] **Step 2:** Run `uv run pytest tests/test_alerts.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement engine and `notify`; wire `app.py`; build banner, Alerts tab, Settings dialog.
- [ ] **Step 4:** Run `uv run pytest -v --trace off`. Expected: all PASS, including a new smoke test `test_alert_banner_appears` (publish a `critical` Alert; `#banner` becomes visible with its title).
- [ ] **Step 5:** Commit: `feat: alert engine, macOS notifications, settings`.

### Task 14: Doctor, README, final check

**Files:** Modify `terminal/__main__.py`; create `README.md`. Test `tests/test_doctor.py`.

**Interfaces — Produces:**

```python
async def doctor(config: Config, checks: list[tuple[str, Callable[[], Awaitable[str]]]] | None = None) -> int
    # runs each check with a 10 s timeout; prints "OK   <name>  <detail>" / "FAIL <name>  <error>" /
    # "SKIP <name>  <missing key and where to get it>"; returns the number of FAILs
```

Default checks, one small real request each: Yahoo history, Binance, Coinbase, Kraken, Finnhub stream, Finnhub news, one RSS feed, blockchain.info stream, Etherscan, SEC current filings, GDELT.

README covers: what it is; install (`brew install uv`, `uv sync`); `.env` keys and where to get each; `uv run python -m terminal`; `uv run python -m terminal doctor`; keyboard shortcuts; the data-delay table from the spec; which data files to edit (`entities.csv`, `themes.toml`, `zones.toml`, `rss_feeds.toml`, `addresses.csv`); known limits; "not financial advice".

- [ ] **Step 1: Failing tests.**

```python
async def test_doctor_counts_failures_and_skips(capsys):
    async def ok(): return "fine"
    async def bad(): raise RuntimeError("no route")
    async def slow(): await asyncio.sleep(60)
    n = await doctor(Config("", "", ""), [("a", ok), ("b", bad)])
    out = capsys.readouterr().out
    assert n == 1 and "OK   a" in out and "FAIL b" in out
def test_default_checks_skip_without_keys():        # Config("", "", "") -> Finnhub, Etherscan, SEC lines are SKIP
```

- [ ] **Step 2:** Run `uv run pytest tests/test_doctor.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `doctor`; write README.
- [ ] **Step 4:** Run `uv run pytest -v --trace off`. Expected: all PASS. Start the app (`uv run python -m terminal`), open the page with Playwright, screenshot at 1440×900, confirm every panel renders and shows `STALE` or `disabled` rather than blank when feeds are unreachable.
- [ ] **Step 5:** Commit: `feat: doctor command and README`. Push the branch.
