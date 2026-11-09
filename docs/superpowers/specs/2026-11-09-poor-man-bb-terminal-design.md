# Poor Man's Bloomberg Terminal — Design

Date: 2026-11-09
Status: awaiting review

## 1. Goal

A personal market terminal for one user, running locally on an Apple Silicon
Mac, using only free data. It shows live prices and charts for stocks, ETFs,
currencies and crypto, a news feed with an estimate of which assets each story
affects, whale activity, market-relevant conflicts, and alerts that demand
attention when something important happens.

Success means: one command starts it, a browser tab shows everything, crypto
and the main US watchlist update in seconds, and an important move produces an
alert without the user watching the screen.

### Decisions made with the user

- Browser dashboard served by a local Python process.
- Whales: crypto large trades, crypto on-chain transfers, SEC insider and fund
  filings, stock unusual volume.
- News impact analysis is rules-only in v1, with a clean seam for a local AI
  model later.
- Free API keys are acceptable (Finnhub, Etherscan).
- Alerts appear in the dashboard and as macOS notifications.
- Dark, clean, Bloomberg-like look. **When look and content conflict, content
  wins.**

### Assumptions

- Single user, localhost only, no login.
- Read-only: the terminal never places trades.
- No paid data sources.

## 2. Architecture

One Python process (FastAPI on asyncio), one SQLite file, one static web page.
No Node, no build step, no Docker.

```
feeds ──▶ event bus ──▶ brain (news rules, alert rules) ──▶ store (SQLite)
                │                                              │
                └──────────────▶ WebSocket hub ◀───────────────┘
                                      │
                                 browser page
```

| Unit | Does | Depends on |
|---|---|---|
| `feeds/*` | One module per source. Fetches, parses, emits typed events. No business logic. | network, event types |
| `bus` | In-process async publish/subscribe of typed events. | — |
| `brain/news_rules` | Story in → affected assets, sentiment, weight, reasons out. | reference data files |
| `brain/alerts` | Event stream in → alerts out, with cooldown and severity. | settings |
| `store` | SQLite persistence for news, whales, conflicts, alerts, watchlist, settings. | — |
| `api` | REST for history, search, settings; one WebSocket for live push. | bus, store |
| `notify` | macOS notification via `osascript`. | — |
| `web/` | Plain HTML, CSS, JS. TradingView Lightweight Charts for charts. | api |

Each feed parser is a pure function of raw payload → events, so it is tested
with saved samples and no network. A feed that fails is isolated: the others
keep running.

Event types: `Tick`, `Candle`, `NewsItem`, `WhaleEvent`, `ConflictEvent`,
`Alert`, `FeedStatus`.

## 3. Data sources

| Need | Source | Key | Expected delay |
|---|---|---|---|
| Crypto prices and trades | Binance WebSocket; Coinbase and Kraken as fallback | none | live |
| US stock and ETF live trades | Finnhub WebSocket | free | live, about 50 symbols |
| History, FX, indices, futures, non-US, overflow symbols | Yahoo Finance (`yfinance`) | none | seconds to 15 min, by exchange |
| News | RSS from multiple outlets, Finnhub news, SEC 8-K feed | Finnhub | 30–60 s polling |
| Crypto large trades | the crypto trade stream, filtered by notional | none | live |
| On-chain transfers | mempool.space (Bitcoin), Etherscan (Ethereum) | Etherscan free | live / about 15 s |
| Insider and fund filings | SEC EDGAR Form 4 and 13F | none | minutes after filing |
| Unusual volume | Yahoo: current volume vs 20-day norm for time of day | none | about 1 min |
| Conflicts | GDELT event feed, plus conflict terms in the news feed | none | about 15 min |

Known limits, stated in the UI where relevant:

- Finnhub's free tier streams roughly 50 symbols. The watchlist's top symbols
  get the stream; the rest fall back to Yahoo polling.
- 13F holdings are published up to 45 days after quarter end.
- Real block-trade and options-flow data is paid. Unusual volume is the free
  proxy.
- Binance is unavailable in some countries; the crypto feed falls back to
  Coinbase or Kraken automatically.
- SEC EDGAR requires a contact string in the User-Agent. The user sets it in
  `.env`; the feed stays off until it is set.

Symbol handling: one resolver maps user input (`AAPL`, `BTC`, `EURUSD`,
`GC=F`) to an asset class and to each provider's symbol format.

## 4. Screen

Dark theme: black background, amber primary text, green/red for direction,
monospace font, dense panels, no decoration.

- **Top strip**: S&P 500, Nasdaq, VIX, dollar index, 10-year yield, gold, oil,
  BTC.
- **Command bar**: type a symbol and press Enter to load it. Keyboard-first.
- **Left**: watchlist with live price and percent change.
- **Centre**: candlestick chart with volume, intervals from 1 minute to
  monthly, moving averages and RSI.
- **Right**: news feed. Each story shows affected tickers, direction, and
  weight.
- **Bottom tabs**: Whales, Conflicts, Alerts log.

Clicking any ticker loads it in the chart. Clicking a story shows why it was
tagged. Every panel shows a red "stale" tag with the age of its last data when
its feed is down.

## 5. News rules (v1)

Each story passes four steps:

1. **Entities**: match tickers and company names against a reference list
   (S&P 500, major ETFs, top 50 crypto, the user's watchlist). Ambiguous names
   (Apple, Target, Meta, Visa) need a supporting context word or a `$TICKER`
   form to match.
2. **Themes**: keyword groups map to assets with a direction, for example
   Fed/rates/CPI → bonds, dollar, banks; OPEC/pipeline/refinery → oil, energy,
   airlines (inverse); chip export controls → NVDA, AMD, TSM, SMH.
3. **Sentiment**: Loughran-McDonald finance word list gives positive,
   negative or neutral.
4. **Weight** 1–5: source tier, strength of terms, number of outlets carrying
   the same story, and whether it hits the watchlist.

Every tag carries its reasons (which words fired which rule), shown in the UI.

Duplicate stories across outlets are merged by normalised title.

**AI seam**: the analyser is one interface, `analyse(story) -> Analysis`. The
rules engine is the only implementation in v1. A local model (Ollama) can be
added as a second implementation without changing callers.

Theme and entity lists live in plain data files the user can edit.

## 6. Conflicts

- GDELT events are filtered to conflict types (armed clash, strike, sanction,
  blockade, unrest) and grouped into named zones.
- Each zone shows a heat value and its trend against the previous 7 days.
- Each zone maps to affected assets, for example Middle East / Hormuz / Red
  Sea → oil, gold, shipping, defence; Taiwan Strait → semiconductors, TSM,
  Asian indices; Ukraine / Black Sea → wheat, natural gas, European equities,
  defence.
- The zone-to-asset map is a plain data file.

## 7. Alerts

| Alert | Default trigger |
|---|---|
| Price jump | watchlist symbol moves 2% in 5 min (crypto 3%) |
| Volume spike | 3× normal volume for the time of day |
| Whale | crypto trade ≥ $1M; on-chain transfer ≥ $10M; insider purchase ≥ $500k |
| News | weight ≥ 4 and hits the watchlist |
| Conflict | zone heat doubles within a day |
| Price level | user-set level crossed |

- All thresholds are editable in a settings panel and stored in SQLite.
- Cooldown: the same alert key does not fire again within 15 minutes.
- Severity: info (log only), warn (banner and sound), critical (banner, sound
  and macOS notification).
- Mute toggle and quiet hours.

## 8. Failure handling

- Feed errors: reconnect with exponential backoff; emit `FeedStatus` so the
  UI marks the panel stale. A stale price is never shown as live.
- Rate limits: the affected feed slows down; others are unaffected.
- Missing key: that feed is disabled and its panel names the missing key and
  where to get it.
- Secrets live in `.env`, which is git-ignored. `.env.example` documents each
  variable.

## 9. Testing

- Feed parsers: saved sample payloads → expected events. No network.
- News rules: a table of real headlines → expected tickers, direction and
  weight band, including the ambiguous-name cases.
- Alert rules: synthetic event streams → expected alerts, including cooldown
  and severity.
- Smoke test: start the app, load the page, inject a fake tick, see it reach
  the WebSocket.
- `python -m terminal doctor`: run on the Mac, reports which feeds connect
  and which keys are missing.

The development sandbox blocks market-data hosts, so live behaviour is
verified on the user's Mac with `doctor`; everything else is covered by the
sample-based tests.

## 10. Build order

Each step leaves a working terminal.

1. Shell: server, page, watchlist, symbol resolver, historical charts.
2. Live prices: crypto stream, Finnhub stream, Yahoo polling fallback.
3. News feed, then news rules.
4. Whales: crypto trades, on-chain, SEC filings, unusual volume.
5. Conflicts, then the alert engine and notifications.

## 11. Out of scope for v1

- Order placement, broker integration, portfolio P&L.
- Options flow, level 2 order book.
- Phone push, multiple users, remote hosting.
- Backtesting.
- AI-based news analysis (seam only).

Candidates for after step 5: earnings and economic calendar, sector heat map.

## 12. Running it

- Python 3.12+, dependencies managed with `uv`. All dependencies have native
  Apple Silicon wheels.
- `uv run python -m terminal` starts the server and prints the local URL.
