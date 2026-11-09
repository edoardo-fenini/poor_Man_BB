from datetime import datetime

import pytest

from terminal.brain.alerts import AlertEngine, us_market_open
from terminal.events import Tick, WhaleEvent
from terminal.notify import mac_notify
from terminal.prices import Prices
from terminal.settings import DEFAULTS, validate

T0 = datetime(2025, 11, 10, 15, 0).timestamp()  # a Monday afternoon, local time


class Rig:
    def __init__(self, **overrides):
        self.settings = {**DEFAULTS, **overrides}
        self.prices = Prices()
        self.clock = T0
        self.engine = AlertEngine(lambda: self.settings, self.prices, lambda: {"AAPL", "BTC-USD"},
                                  lambda s: "crypto" if s.endswith("-USD") else "stock", now=lambda: self.clock)

    def tick(self, symbol, price, at):
        self.clock = T0 + at
        tick = Tick(symbol, price, 1, self.clock, "x")
        self.prices.update(tick)
        return self.engine.on_event(tick)

    def ramp(self, symbol, start, end, seconds=300, step=30):
        """Steady ticks from start to end; returns every alert fired on the way."""
        out, n = [], seconds // step
        for i in range(n + 1):
            out += self.tick(symbol, start + (end - start) * i / n, i * step)
        return out


def whale(type_, usd, side="", symbol="BTC-USD", id_="w"):
    return WhaleEvent(id_, type_, symbol, usd, side, "detail", "", T0)


def story(weight, symbols, id_="n1"):
    return {"id": id_, "title": "Headline", "ts": T0,
            "analysis": {"weight": weight, "sentiment": -1, "impacts": [{"symbol": s, "direction": -1} for s in symbols]}}


def test_price_jump_fires_warn_at_threshold():
    alerts = Rig().ramp("AAPL", 100, 102.1)
    assert [(a.key, a.severity, a.symbol) for a in alerts] == [("jump:AAPL", "warn", "AAPL")]
    assert "+2." in alerts[0].title and "AAPL" in alerts[0].title


def test_price_drop_fires_too_and_says_minus():
    alerts = Rig().ramp("AAPL", 100, 97.5)
    assert len(alerts) == 1 and "-2." in alerts[0].title


def test_price_jump_critical_at_double():
    rig = Rig(cooldown_s=0)
    assert rig.ramp("AAPL", 100, 104.5)[-1].severity == "critical"


def test_small_move_is_quiet():
    assert Rig().ramp("AAPL", 100, 101.5) == []


def test_crypto_uses_crypto_threshold():
    assert Rig().ramp("BTC-USD", 100, 102.5) == []
    assert [a.severity for a in Rig().ramp("BTC-USD", 100, 103.1)] == ["warn"]


def test_non_watchlist_symbol_never_fires():
    assert Rig().ramp("ZZZZ", 100, 150) == []


def test_cooldown_suppresses_repeat_then_allows():
    rig = Rig()
    assert len(rig.ramp("AAPL", 100, 103)) == 1          # fires once although many ticks are over the line
    assert rig.tick("AAPL", 103.9, 400) == []             # still a warn-sized move, inside the cooldown
    rig.tick("AAPL", 103.9, 1100)
    assert len(rig.tick("AAPL", 107.0, 1300)) == 1        # cooldown (900 s) is over


def test_move_that_grows_to_critical_alerts_again():
    rig = Rig()
    assert [a.severity for a in rig.ramp("AAPL", 100, 104.5)] == ["warn", "critical"]


def test_no_alert_across_gap():
    rig = Rig()
    rig.tick("AAPL", 100, 0)
    assert rig.tick("AAPL", 105, 6 * 3600) == []          # market reopened / Mac woke up
    assert rig.tick("AAPL", 105.1, 6 * 3600 + 30) == []


def test_whale_trade_warn_and_critical_at_ten_times():
    assert [a.severity for a in Rig().engine.on_event(whale("crypto_trade", 2_000_000, "buy"))] == ["warn"]
    assert [a.severity for a in Rig().engine.on_event(whale("crypto_trade", 10_000_000, "buy"))] == ["critical"]
    assert [a.severity for a in Rig().engine.on_event(whale("onchain", 99_000_000))] == ["warn"]
    assert [a.severity for a in Rig().engine.on_event(whale("onchain", 100_000_000))] == ["critical"]


def test_whale_trades_on_one_symbol_share_a_cooldown():
    rig = Rig()
    first = rig.engine.on_event(whale("crypto_trade", 2_000_000, "buy", id_="a"))
    assert len(first) == 1 and rig.engine.on_event(whale("crypto_trade", 3_000_000, "sell", id_="b")) == []
    assert len(rig.engine.on_event(whale("crypto_trade", 2_000_000, "buy", symbol="ETH-USD", id_="c"))) == 1
    assert len(rig.engine.on_event(whale("crypto_trade", 50_000_000, "buy", id_="d"))) == 1   # critical breaks through


def test_insider_sell_and_small_buy_do_not_fire():
    rig = Rig()
    assert rig.engine.on_event(whale("insider", 5_000_000, "sell", "ACME")) == []
    assert rig.engine.on_event(whale("insider", 400_000, "buy", "ACME")) == []
    assert [a.severity for a in rig.engine.on_event(whale("insider", 600_000, "buy", "ACME"))] == ["warn"]


def test_volume_and_fund_severity():
    rig = Rig()
    assert [a.severity for a in rig.engine.on_event(whale("volume", 1e6, symbol="AAPL"))] == ["warn"]
    assert [a.severity for a in rig.engine.on_event(whale("volume", 1e6, symbol="ZZZZ", id_="v2"))] == ["info"]
    assert [a.severity for a in rig.engine.on_event(whale("fund", 1e9, symbol="", id_="f"))] == ["info"]


def test_news_needs_weight_and_watchlist():
    rig = Rig()
    assert rig.engine.on_news(story(3, ["AAPL"])) == []
    assert rig.engine.on_news(story(5, ["MSFT"])) == []
    assert [(a.severity, a.key, a.symbol) for a in rig.engine.on_news(story(4, ["MSFT", "AAPL"]))] == [("warn", "news:n1", "AAPL")]
    assert rig.engine.on_news(story(4, ["AAPL"])) == []                       # same story again
    assert [a.severity for a in rig.engine.on_news(story(5, ["AAPL"], "n2"))] == ["critical"]


def test_conflict_needs_ratio_and_volume_of_headlines():
    rig = Rig()
    zone = {"zone": "Taiwan Strait", "count": 12, "ratio": 2.4, "assets": [{"symbol": "TSM", "direction": -1}]}
    assert [(a.severity, a.symbol) for a in rig.engine.on_zone(zone)] == [("warn", "TSM")]
    assert rig.engine.on_zone({**zone, "count": 13}) == []                   # once per zone per day
    assert rig.engine.on_zone({**zone, "zone": "Other", "ratio": 1.9}) == []
    assert rig.engine.on_zone({**zone, "zone": "Tiny", "count": 3, "ratio": 9.0}) == []
    assert rig.engine.on_zone({**zone, "zone": "New", "count": 30, "ratio": 0.0}) == []   # no history to compare


def test_level_cross_fires_in_the_set_direction_only():
    rig = Rig(levels=[{"symbol": "AAPL", "price": 200.0, "above": True}], price_jump_pct=99)
    assert rig.tick("AAPL", 199, 0) == [] and rig.tick("AAPL", 198, 10) == []
    fired = rig.tick("AAPL", 200.5, 20)
    assert [(a.severity, a.key) for a in fired] == [("critical", "level:AAPL:200.0")]
    assert rig.tick("AAPL", 199, 30) == [] and rig.tick("AAPL", 201, 40) == []   # cooldown
    below = Rig(levels=[{"symbol": "AAPL", "price": 200.0, "above": False}], price_jump_pct=99)
    assert below.tick("AAPL", 199, 0) == [] and below.tick("AAPL", 201, 10) == []
    assert len(below.tick("AAPL", 199.9, 20)) == 1


def test_first_tick_never_crosses_a_level():
    rig = Rig(levels=[{"symbol": "AAPL", "price": 200.0, "above": True}])
    assert rig.tick("AAPL", 250, 0) == []


def test_muted_downgrades_to_info():
    assert {a.severity for a in Rig(muted=True).ramp("AAPL", 100, 105)} == {"info"}


def test_quiet_hours_wrap_midnight():
    rig = Rig(quiet_start="22:00", quiet_end="07:00")
    rig.clock = datetime(2025, 11, 11, 2, 0).timestamp()
    assert [a.severity for a in rig.engine.on_event(whale("crypto_trade", 50_000_000, "buy"))] == ["info"]
    rig.clock = datetime(2025, 11, 11, 12, 0).timestamp()
    assert [a.severity for a in rig.engine.on_event(whale("crypto_trade", 50_000_000, "buy", id_="x", symbol="ETH-USD"))] == ["critical"]


def test_bad_settings_do_not_break_the_engine():
    rig = Rig(quiet_start="banana", quiet_end="07:00", levels=[{"oops": 1}, "x"], price_jump_pct="2")
    assert len(rig.ramp("AAPL", 100, 103)) == 1


def test_us_market_hours():
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    assert us_market_open(datetime(2025, 11, 10, 10, 0, tzinfo=ny).timestamp())       # Monday 10:00
    assert us_market_open(datetime(2025, 11, 10, 4, 30, tzinfo=ny).timestamp())       # pre-market
    assert not us_market_open(datetime(2025, 11, 10, 21, 0, tzinfo=ny).timestamp())   # after hours end
    assert not us_market_open(datetime(2025, 11, 9, 12, 0, tzinfo=ny).timestamp())    # Sunday


def test_mac_notify_passes_text_as_arguments_not_script():
    calls = []
    evil = 'a" & (do shell script "touch /tmp/x") & "'
    mac_notify(evil, "body", run=lambda argv, **kw: calls.append(argv), platform="darwin")
    argv = calls[0]
    assert argv[0] == "osascript" and argv[-2:] == [evil, "body"]
    assert all("do shell script" not in part for part in argv[:-2])
    mac_notify("t", "b", run=lambda argv, **kw: calls.append(argv), platform="linux")
    assert len(calls) == 1


def test_mac_notify_survives_a_missing_osascript():
    def boom(argv, **kw):
        raise FileNotFoundError("osascript")
    mac_notify("t", "b", run=boom, platform="darwin")


def test_validate_settings():
    assert validate({"price_jump_pct": 1.5, "muted": True, "quiet_start": "22:00",
                     "levels": [{"symbol": "aapl", "price": 200, "above": True}]}) == {
        "price_jump_pct": 1.5, "muted": True, "quiet_start": "22:00",
        "levels": [{"symbol": "AAPL", "price": 200.0, "above": True}]}
    assert validate({"quiet_start": ""}) == {"quiet_start": ""}
    for bad in ({"price_jump_pct": -1}, {"price_jump_pct": "x"}, {"muted": "yes"}, {"quiet_start": "25:99"},
                {"nope": 1}, {"levels": [{"symbol": "<x>", "price": 1, "above": True}]}, {"levels": "x"},
                {"cooldown_s": float("inf")}, {"price_jump_pct": True}):
        with pytest.raises(ValueError):
            validate(bad)
