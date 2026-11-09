from pathlib import Path

from terminal.events import Alert, ConflictEvent, NewsItem, Tick, WhaleEvent

WEB = Path(__file__).parent.parent / "terminal" / "web"


def test_page_loads_and_chart_renders(page):
    page.fill("#cmd", "AAPL")
    page.press("#cmd", "Enter")
    page.wait_for_selector("#chart canvas")
    page.wait_for_function("document.querySelector('#chart-title').textContent.includes('AAPL')")


def test_unknown_symbol_shows_not_found_and_keeps_chart(page):
    page.fill("#cmd", "MSFT")
    page.press("#cmd", "Enter")
    page.wait_for_function("document.querySelector('#chart-title').textContent.includes('MSFT')")
    page.fill("#cmd", "<script>")
    page.press("#cmd", "Enter")
    page.wait_for_function("document.querySelector('#cmd-msg').textContent.includes('NOT FOUND')")
    assert "MSFT" in page.text_content("#chart-title")


def test_tick_updates_watchlist_row(page, live):
    live.publish(Tick("AAPL", 123.45, 1, 2e9, "x"))
    page.wait_for_function("document.querySelector('#watchlist [data-id=\"AAPL\"]').textContent.includes('123.45')")


def test_command_adds_and_removes_watchlist_symbol(page):
    page.fill("#cmd", "+AMD")
    page.press("#cmd", "Enter")
    page.wait_for_selector('#watchlist [data-id="AMD"]')
    page.fill("#cmd", "-AMD")
    page.press("#cmd", "Enter")
    page.wait_for_selector('#watchlist [data-id="AMD"]', state="detached")


def test_no_innerhtml_in_app_js():
    js = (WEB / "app.js").read_text()
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js


def test_news_title_with_html_is_shown_as_text(page, live):
    live.publish(NewsItem("x1", "<img src=x onerror=alert(1)> Nvidia surges", "https://example.com/a", "CNBC", 2e9, ""))
    page.wait_for_selector("#news .news")
    assert "<img" in page.text_content("#news .news .title")
    assert page.query_selector("#news img") is None


def test_news_row_shows_chips_and_reasons_and_chip_loads_chart(page, live):
    live.publish(NewsItem("x2", "Nvidia shares soar on record revenue", "https://example.com/b", "CNBC", 2e9, ""))
    page.wait_for_selector('#news .chip[data-symbol="NVDA"]')
    assert "up" in page.get_attribute('#news .chip[data-symbol="NVDA"]', "class")
    assert page.get_attribute("#news .news a", "href") == "https://example.com/b"
    page.click("#news .news .title")
    page.wait_for_function("document.querySelector('#news .why').textContent.includes('entity NVDA')")
    page.click('#news .chip[data-symbol="NVDA"]')
    page.wait_for_function("document.querySelector('#chart-title').textContent.includes('NVDA')")


def test_news_update_replaces_row_instead_of_duplicating(page, live):
    for source in ("a", "b", "c"):
        live.publish(NewsItem("x3", "Tesla shares plunge on recall", "https://example.com/c", source, 2e9, ""))
    page.wait_for_function("document.querySelector('#news .news .src')?.textContent.includes('+2')")
    assert len(page.query_selector_all("#news .news")) == 1


def test_whale_row_renders_and_filters(page, live):
    live.publish(WhaleEvent("w1", "onchain", "BTC-USD", 1_234_000, "", "12 BTC <b>a</b> → b", "https://mempool.space/tx/ab", 2e9))
    live.publish(WhaleEvent("w2", "insider", "ACME", 600_000, "buy", "DOE JANE bought 10,000 @ $60.00", "", 2e9 + 1))
    page.wait_for_selector('#whales .row[data-id="w2"]')
    row = page.text_content('#whales .row[data-id="w1"]')
    assert "$1.2M" in row and "<b>a</b>" in row and "BTC" in row
    assert page.get_attribute('#whales .row[data-id="w1"] a', "href") == "https://mempool.space/tx/ab"
    assert "up" in page.get_attribute('#whales .row[data-id="w2"] .side', "class")
    page.click('#whale-filters [data-type="onchain"]')
    page.wait_for_selector('#whales .row[data-id="w1"]', state="hidden")
    assert page.is_visible('#whales .row[data-id="w2"]')
    page.click('#whales .row[data-id="w2"] .sym')
    page.wait_for_function("document.querySelector('#chart-title').textContent.includes('ACME')")


def test_conflicts_tab_shows_zone_heat_and_headlines(page, live):
    import time
    live.publish(ConflictEvent("k1", "Taiwan Strait", "clash", "Jets <i>cross</i> median line", "https://example.com/k1", time.time()))
    page.click('#tabs [data-tab="conflicts"]')
    page.wait_for_function("""() => { const r = document.querySelector('#zones [data-zone="Taiwan Strait"]');
        return r && r.querySelector('.count').textContent.trim() === '1'; }""")
    assert page.query_selector('#zones [data-zone="Taiwan Strait"] .chip[data-symbol="TSM"]')
    page.click('#zones [data-zone="Taiwan Strait"] .zone')
    page.wait_for_selector("#zone-news .row")
    assert "<i>cross</i>" in page.text_content("#zone-news .row")


def test_alert_banner_appears_for_critical_only(page, live):
    import time
    live.publish(Alert("k:info", "info", "Quiet <b>note</b>", "", "", time.time()))
    live.publish(Alert("k:crit", "critical", "BTC-USD -6.0% in 5 min", "now 95,000.00", "BTC-USD", time.time() + 1))
    page.wait_for_selector("#banner.critical")
    assert "BTC-USD -6.0%" in page.text_content("#banner") and "Quiet" not in page.text_content("#banner")
    page.click("#banner")
    page.wait_for_selector("#banner", state="hidden")
    page.wait_for_function("document.querySelector('#chart-title').textContent.includes('BTC-USD')")
    page.click('#tabs [data-tab="alerts"]')
    rows = page.query_selector_all("#alerts .row")
    assert len(rows) == 2 and "Quiet <b>note</b>" in rows[1].text_content()


def test_mute_button_and_settings_dialog_save(page, live):
    import json
    import urllib.request

    def settings():
        return json.load(urllib.request.urlopen(live.url + "/api/settings"))

    page.click("#mute")
    page.wait_for_function("document.querySelector('#mute').textContent.includes('MUTED')")
    assert settings()["muted"] is True
    page.click("#open-settings")
    page.fill('#settings [name="price_jump_pct"]', "1.5")
    page.fill('#settings [name="new-level-symbol"]', "aapl")
    page.fill('#settings [name="new-level-price"]', "250")
    page.click("#settings #add-level")
    page.click("#settings #save-settings")
    page.wait_for_selector("#settings", state="hidden")
    saved = settings()
    assert saved["price_jump_pct"] == 1.5 and saved["levels"] == [{"symbol": "AAPL", "price": 250.0, "above": True}]


def test_settings_dialog_shows_server_error(page):
    page.click("#open-settings")
    page.fill('#settings [name="quiet_start"]', "99:99")
    page.click("#settings #save-settings")
    page.wait_for_function("document.querySelector('#settings-error').textContent.includes('quiet_start')")


def test_valid_symbol_without_data_is_reported_as_no_data(page, live):
    async def empty(symbol, interval):
        return []
    live.app.state.history = empty
    page.fill("#cmd", "MSFT")
    page.press("#cmd", "Enter")
    page.wait_for_function("document.querySelector('#cmd-msg').textContent.startsWith('NO DATA: MSFT')")


def test_daily_chart_starts_a_new_bar_on_a_new_day(page, live):
    page.wait_for_function("state.candles.length > 0 && state.symbol")
    before = page.evaluate("[state.candles.length, state.candles[state.candles.length - 1].time, state.symbol]")
    live.publish(Tick(before[2], 55.5, 1, before[1] + 10 * 86400 + 3600, "x"))   # later than any seeded quote
    page.wait_for_function(f"state.candles.length === {before[0] + 1}")
    assert page.evaluate("state.candles[state.candles.length - 1].close") == 55.5
    assert page.evaluate(f"state.candles[{before[0] - 1}].close") != 55.5      # yesterday's bar is untouched


def test_first_down_time_is_kept_while_a_feed_stays_down(page, live):
    from terminal.events import FeedStatus
    live.publish(FeedStatus("rss", "down", "boom", 1000.0))
    live.publish(FeedStatus("rss", "down", "boom", 2000.0))
    page.wait_for_function("state.feeds.rss && state.feeds.rss.state === 'down'")
    page.wait_for_timeout(200)
    assert page.evaluate("state.feeds.rss.ts") == 1000.0
    live.publish(FeedStatus("rss", "ok", "", 3000.0))
    live.publish(FeedStatus("rss", "down", "boom", 4000.0))
    page.wait_for_function("state.feeds.rss.ts === 4000")


def test_panels_go_stale_when_the_server_disappears(page, live):
    from terminal.events import FeedStatus
    live.publish(FeedStatus("crypto", "ok", "", 1.0))
    page.wait_for_function("document.querySelector('#tag-prices').textContent === 'LIVE'")
    live.stop()
    for tag in ("#tag-prices", "#tag-news", "#tag-bottom"):
        page.wait_for_function(f"document.querySelector('{tag}').textContent.startsWith('STALE')")
