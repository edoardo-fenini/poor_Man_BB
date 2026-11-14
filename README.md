# Poor Man's Bloomberg Terminal

A market terminal that runs on your Mac and uses only free data. One Python
process, one browser tab.

- **Charts** for stocks, ETFs, currencies, crypto, indices and futures, 1 minute to monthly, with SMA 20/50 and RSI 14.
- **Live prices**: crypto streams in real time with no key; US stocks stream in real time with a free Finnhub key.
- **News** from 15 outlets, tagged with the assets each story affects, the mood, a 1–5 weight, and the reasons.
- **Whales**: large crypto trades, large Bitcoin/Ethereum/stablecoin transfers, SEC insider trades and 13F fund filings, unusual stock volume.
- **Conflicts**: headline heat per zone against the last week, with the assets each zone moves.
- **Functions**, typed like on a Bloomberg: `AAPL DES`, `FA`, `OMON`, `WEI`, `GC`, `ECO`… 37 screens, listed by `HELP`.
- **Computed analytics** that are normally paid: option greeks, max pain, skew and unusual option activity; DCF fair value and WACC; Altman Z, Piotroski F and Merton default probability; beta, correlation, VaR, Sharpe; the Fed path priced by futures; FX cross rates; sea-lane ship counts.
- **Map** (`MAP`): news pinned to the places it names, conflict zones, live ships at the main sea lanes, planes (civil and military), earthquakes.
- **Alerts**: price jumps, price levels, whales, heavy news on your watchlist, heating conflicts. Banner and sound in the page, macOS notification for critical ones.

It never places trades. Nothing here is financial advice.

## Install (Apple Silicon Mac)

```bash
brew install uv
```

```bash
git clone https://github.com/edoardo-fenini/poor_Man_BB.git && cd poor_Man_BB
```

```bash
uv sync
```

## Keys (free, optional)

```bash
cp .env.example .env
```

Then fill in `.env`. The terminal runs without any of these; each one switches on more data.

| Variable | What it unlocks | Where to get it |
|---|---|---|
| `FINNHUB_KEY` | Real-time US stock trades (about 50 symbols), extra news | finnhub.io → sign up → Dashboard |
| `ETHERSCAN_KEY` | Large Ethereum, USDT and USDC transfers | etherscan.io/myapikey |
| `AISSTREAM_KEY` | Live ships on the map, ship counts per sea lane | aisstream.io → sign in → API Keys |
| `FRED_KEY` | US economic statistics (`ECST`) | fred.stlouisfed.org/docs/api/api_key.html |
| `SEC_USER_AGENT` | Insider trades, 13F filings, 8-K filings | Not a key: your contact, e.g. `Jane Doe jane@example.com`. The SEC requires one. |

## Run

```bash
uv run python -m terminal
```

Open http://127.0.0.1:8787. Stop with Ctrl+C.

Check which data sources work from your network:

```bash
uv run python -m terminal doctor
```

Each line is `OK`, `FAIL` (with the error) or `SKIP` (a key is missing, with where to get it).

## Using it

| Do | How |
|---|---|
| Load a symbol | Type it in the command bar, press Enter: `AAPL`, `BTC`, `EURUSD`, `GC=F`, `^VIX`, `ENI.MI` |
| Run a function | `AAPL DES`, then just `FA`, `OMON`… — the last symbol stays loaded. `HELP` lists them all |
| Back to the dashboard | `Esc`, or `GP` for the chart |
| Open the map | `MAP`. Toggle layers at the top; zoom in for civil planes; click a sea lane to fly there |
| Pick an option expiry | `AAPL OMON 3` (third expiry) |
| Filter the calendar | `ECO USD`, `ECO HIGH` |
| Focus the command bar | `/` |
| Add / remove a watchlist symbol | `+AMD` / `-AMD` |
| Change chart interval | Buttons above the chart |
| See why a story was tagged | Click its headline |
| Jump to an asset | Click any ticker chip or symbol |
| Filter news | `W1+` cycles the minimum weight; `WATCHLIST` keeps only stories that hit your list |
| Filter whales | Toggle the type buttons next to the tabs |
| Tune alerts, set price levels, quiet hours | `SETTINGS` |
| Silence alerts | `SOUND ON` / `MUTED` |

The browser plays alert sounds only after you have clicked once on the page.

### Functions

| Group | Functions |
|---|---|
| Company | `DES` description · `FA` financials and ratios · `RV` peers · `ANR` analysts · `ERN` earnings · `DVD` dividends · `HDS` holders and insiders · `SI` short interest · `CN` news |
| Valuation and credit | `DCF` fair value · `WACC` cost of capital · `DRSK` default risk |
| Price and risk | `HP` price table · `TRA` total return · `BETA` · `HVG` realised vs implied vol · `TECH` indicators · `SEAG` seasonality |
| Options | `OMON` chain, greeks, max pain, unusual activity · `SKEW` skew and term structure |
| Portfolio (watchlist) | `CORR` correlation matrix · `PORT` risk · `EVTS` earnings and dividend dates |
| Markets | `WEI` indices · `WCR` currencies · `FXC` cross rates · `GLCO` commodities · `CRYP` crypto · `IMAP` sectors · `MOST` movers |
| Rates and economy | `WB` Treasury yields · `GC` yield curve · `WIRP` Fed path · `ECO` calendar · `ECST` statistics |
| News and map | `TOP` top news · `MAP` world map |

Bloomberg habits work: `AAPL US EQUITY DES GO` is read as `AAPL DES`. A ticker that is also a function name
(`HP`, `GC`) loads as a stock when followed by a yellow-key word: `HP US`.

## How fresh is the data

| Data | Source | Delay |
|---|---|---|
| Crypto prices and trades | Binance, falling back to Coinbase, then Kraken | live |
| US stocks and ETFs | Finnhub stream (with key) | live, about 50 symbols |
| Everything else, and charts | Yahoo Finance | seconds to 15 minutes, depending on the exchange |
| News | RSS, Finnhub, SEC 8-K | 45–60 s |
| Bitcoin transfers | blockchain.info stream | live |
| Ethereum transfers | Etherscan | about 15 s |
| Insider and fund filings | SEC EDGAR | minutes after filing |
| Unusual volume | Yahoo | about 10 minutes |
| Company data, options, movers (functions) | Yahoo Finance | minutes; option quotes 15 minutes or more |
| Yield curve | US Treasury | daily |
| Economic calendar | Forex Factory weekly file | 30 minutes |
| Economic statistics | FRED (with key) | 1 hour |
| Ships | aisstream.io (with key), main sea lanes only | live |
| Planes | adsb.lol | 10–30 s |
| Earthquakes | USGS | 5 minutes |
| Map background | OpenStreetMap tiles, loaded by your browser | — |
| Conflicts | Your news feeds (place + conflict word in the headline), plus GDELT when reachable | 45 s to 15 minutes |

Every panel says `LIVE`, `STALE <feed> <age>`, `CLOSED` or `OFF`. A price older than two minutes shows its age.

## Make it yours

Plain files in `terminal/data/`; restart after editing.

| File | What it controls |
|---|---|
| `entities.csv` | Companies and assets the news rules recognise. `ambiguous=1` means the name needs a context word. |
| `themes.toml` | Keyword groups and the assets they move, with direction. |
| `words_positive.txt`, `words_negative.txt` | Mood words and phrases. |
| `zones.toml` | Conflict zones, the keywords that find them in headlines, GDELT search terms, affected assets. |
| `rss_feeds.toml` | News sources and their tier. |
| `places.csv` | Place names and coordinates that pin news to the map. |
| `addresses.csv` | Labels for on-chain addresses (e.g. exchanges). Ships empty: add only ones you trust. |

## Known limits

- The news rules are word matching, not understanding. A story's mood is applied to every company it names, and themes assume US markets. `terminal/brain/analysis.py` defines the one interface a local AI model would implement to replace them.
- Finnhub's free stream covers about 50 US symbols; the rest of the watchlist is polled from Yahoo.
- 13F filings show holdings up to 45 days after quarter end, and only the ten largest positions. Changes against the previous quarter are not computed.
- Real block trades and options flow are paid data. Unusual volume is the free stand-in.
- Computed figures are models, not quotes. Greeks use Black-Scholes at Yahoo's implied vol; `DCF` takes growth from past revenue; `DRSK`'s Merton model treats all debt as due in a year; `WIRP` shows month averages, not per-meeting odds.
- Not here because no free source exists: bond and CDS prices, real-time option flow, broker research, chat, order entry, supply-chain data. One window at a time, not four.
- Ships appear only inside the listed sea lanes (the whole ocean is too much data for a free feed). Planes come from volunteer receivers: coverage is thin over oceans.
- News is pinned by the first place name in the headline. A headline naming no place is not on the map.
- The map background comes from openstreetmap.org, so that site sees your IP address while the map is open.
- Exchange holidays are treated as normal trading days.
- The terminal answers only on `127.0.0.1` / `localhost` and refuses requests from other websites. It has no login: do not expose the port to a network.
- Crypto whales off your watchlist (stablecoins, for instance) are logged without sound unless they are ten times the threshold.
- The news source URLs and some APIs could not be tested where this was built. Run `doctor`; a dead feed only switches itself off.

## Develop

```bash
uv run pytest
```

The browser tests need Chromium: `uv run playwright install chromium` (they skip without it).
Design and plan are in `docs/superpowers/`.

Charts by [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) (Apache 2.0).
Map by [Leaflet](https://leafletjs.com) (BSD 2-Clause), map data © OpenStreetMap contributors.
Place names from [Natural Earth](https://www.naturalearthdata.com) (public domain).
