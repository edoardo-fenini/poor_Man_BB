import json
import tomllib
from pathlib import Path

from terminal.feeds.base import safe
from terminal.feeds.finnhub import parse_news
from terminal.feeds.rss import FEEDS_FILE, clean, news_id, parse_feed
from terminal.feeds.sec import parse_8k

SAMPLES = Path(__file__).parent / "samples"
NOW = 1762720000.0


def test_clean_strips_html_and_scripts():
    assert clean('<b>Fed</b> &amp; <script>alert(1)</script>rates') == "Fed & rates"
    assert clean("  a\n\n b\t c ") == "a b c"
    assert clean(None) == ""


def test_clean_truncates():
    assert len(clean("x" * 1000)) == 300 and clean("x" * 1000).endswith("…")


def test_news_id_merges_outlets():
    assert news_id("Fed cuts rates!") == news_id("fed  cuts rates") != news_id("Fed holds rates")


def test_parse_feed_sample():
    items = parse_feed((SAMPLES / "feed.xml").read_bytes(), "Sample", NOW)
    assert [i.title for i in items] == ["Fed holds rates & signals patience", "Oil jumps"]
    first, second = items
    assert first.ts == 1762718400.0 and first.source == "Sample" and first.url == "https://example.com/a"
    assert first.summary == "The Federal Reserve left rates unchanged."
    assert first.id == news_id(first.title)
    assert second.ts == NOW  # undated entries take the fetch time


def test_parse_feed_rejects_non_http_links_and_future_dates():
    xml = (b'<rss version="2.0"><channel>'
           b'<item><title>Bad link</title><link>javascript:alert(1)</link></item>'
           b'<item><title>From the future</title><link>https://e.com/x</link>'
           b'<pubDate>Sun, 09 Nov 2031 20:00:00 GMT</pubDate></item></channel></rss>')
    items = parse_feed(xml, "S", NOW)
    assert [i.title for i in items] == ["From the future"] and items[0].ts == NOW


def test_parse_feed_garbage_bytes():
    for raw in (b"\x00not xml", b"", b"<html><body>502 Bad Gateway</body></html>"):
        assert safe(lambda b: parse_feed(b, "x", NOW), raw, "rss") == []


def test_parse_finnhub_news_sample():
    items = safe(parse_news, json.loads((SAMPLES / "finnhub_news.json").read_text()), "n")
    assert len(items) == 1
    item = items[0]
    assert (item.title, item.source, item.ts) == ("Nvidia shares rise on strong chip demand", "MarketWatch", 1762718400.0)
    assert item.summary == "Demand for AI chips stays strong." and item.url == "https://example.com/n1"
    assert safe(parse_news, {"error": "limit"}, "n") == []


def test_parse_8k_sample():
    items = parse_8k((SAMPLES / "edgar_8k.atom").read_bytes(), NOW)
    assert [i.title for i in items] == [
        "Apple Inc. filed 8-K: Results of Operations and Financial Condition",
        "Tesla, Inc. filed 8-K/A",
    ]
    assert items[0].source == "SEC" and items[0].url.endswith("0000320193-25-000123-index.htm")
    assert items[0].ts == 1762551005.0 and items[0].id != items[1].id
    assert safe(lambda b: parse_8k(b, NOW), b"<html>", "sec") == []


def test_feed_list_is_valid():
    feeds = tomllib.loads(FEEDS_FILE.read_text())["feed"]
    assert len(feeds) >= 12
    for f in feeds:
        assert f["url"].startswith("https://") and f["name"] and f["tier"] in (1, 2, 3)
    assert len({f["name"] for f in feeds}) == len(feeds)
