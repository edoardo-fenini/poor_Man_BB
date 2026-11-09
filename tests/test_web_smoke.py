from pathlib import Path

from terminal.events import NewsItem, Tick, WhaleEvent

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
