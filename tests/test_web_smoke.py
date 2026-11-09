from pathlib import Path

from terminal.events import Tick

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
