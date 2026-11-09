from terminal.events import Tick, to_json
from terminal.store import Store


def test_add_dedupes_by_kind_and_key():
    s = Store(":memory:")
    assert s.add("news", "k1", 1.0, {"a": 1}) is True
    assert s.add("news", "k1", 2.0, {"a": 2}) is False
    assert s.add("whale", "k1", 1.0, {}) is True


def test_recent_is_newest_first_and_limited():
    s = Store(":memory:")
    for i in range(5):
        s.add("news", str(i), float(i), {"i": i})
    assert [r["i"] for r in s.recent("news", 2)] == [4, 3]


def test_kv_roundtrip_and_default():
    s = Store(":memory:")
    assert s.get("x", 7) == 7
    s.set("x", {"a": [1]})
    assert s.get("x") == {"a": [1]}


def test_prune_keeps_newest():
    s = Store(":memory:")
    for i in range(5):
        s.add("news", str(i), float(i), {"i": i})
    s.prune("news", 2)
    assert [r["i"] for r in s.recent("news")] == [4, 3]


def test_to_json_has_kind():
    assert to_json(Tick("A", 1, 2, 3, "x"))["kind"] == "tick"


def test_add_with_replace_overwrites_data_but_reports_not_new():
    s = Store(":memory:")
    s.add("news", "k", 1.0, {"w": 1})
    assert s.add("news", "k", 1.0, {"w": 2}, replace=True) is False
    assert s.recent("news") == [{"w": 2}]
    assert s.add("news", "k2", 2.0, {"w": 3}, replace=True) is True
