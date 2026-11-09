import pytest

from terminal.events import Tick
from terminal.prices import Prices


def feed(p, pairs, symbol="A"):
    for ts, px in pairs:
        p.update(Tick(symbol, px, 0, ts, "x"))


def test_change_over_window():
    p = Prices()
    feed(p, [(0, 100), (60, 101), (300, 103)])
    assert p.change("A", 300, now=300) == pytest.approx(0.03)


def test_change_ignores_ticks_older_than_window():
    p = Prices()
    feed(p, [(0, 50), (400, 100), (600, 100), (700, 102)])
    assert p.change("A", 300, now=700) == pytest.approx(0.02)


def test_change_needs_enough_history():
    p = Prices()
    feed(p, [(0, 100), (10, 105)])
    assert p.change("A", 300, now=10) is None


def test_gap_resets_window():  # sleep/wake, market reopen
    p = Prices()
    feed(p, [(0, 100), (5000, 110)])
    assert p.change("A", 300, now=5000) is None and p.last("A") == 110


def test_last_and_age():
    p = Prices()
    assert p.last("ZZ") is None and p.age("ZZ", 10) is None
    feed(p, [(100, 5)])
    assert p.age("A", 130) == 30
