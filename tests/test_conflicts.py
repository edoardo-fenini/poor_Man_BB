import json
import tomllib
from pathlib import Path

from terminal.brain.conflicts import ZONES_FILE, Zones, heat, load_zones
from terminal.events import ConflictEvent
from terminal.feeds.base import safe
from terminal.feeds.gdelt import classify, parse_artlist, query_for
from terminal.store import Store
from terminal.symbols import resolve

SAMPLES = Path(__file__).parent / "samples"
DAY = 86400.0
ZONE = {"name": "Testland", "query": "testland", "assets": [{"symbol": "GC=F", "direction": 1}]}


def test_classify():
    assert classify("Missile strike hits port") == "strike"
    assert classify("Troops clash near the border") == "clash"
    assert classify("New sanctions announced") == "sanction"
    assert classify("Navy imposes blockade") == "blockade"
    assert classify("Protesters riot in capital") == "unrest"
    assert classify("Talks resume") == "other"


def test_parse_artlist_sample():
    events = parse_artlist(json.loads((SAMPLES / "gdelt_artlist.json").read_text()), "Testland")
    assert [(e.type, e.zone, e.url) for e in events] == [
        ("strike", "Testland", "https://example.com/1"), ("sanction", "Testland", "https://example.com/3")]
    assert events[0].ts == 1762718400.0 and events[0].id != events[1].id


def test_parse_artlist_empty_or_error():
    for bad in ({}, {"articles": None}, {"articles": [{"title": "x"}]}, None, "Your query was too short"):
        assert safe(lambda p: parse_artlist(p, "Z"), bad, "g") == []


def test_query_restricts_to_conflict_terms_and_english():
    q = query_for(ZONE)
    assert q.startswith("testland (") and "sourcelang:english" in q and "missile" in q


def test_heat_ratio():
    counts = {str(20000 + d): 10 for d in range(7)} | {"20007": 25}
    assert heat(counts, 20007) == (25, 2.5)


def test_heat_no_history_is_zero_ratio():
    assert heat({"20007": 5}, 20007) == (5, 0.0)
    assert heat({}, 20007) == (0, 0.0)


def test_zones_record_counts_once_per_event_and_persists():
    store = Store(":memory:")
    zones = Zones([ZONE], store)
    ev = ConflictEvent("e1", "Testland", "strike", "t", "https://e.com", 20007 * DAY + 5)
    state = zones.record(ev)
    zones.record(ConflictEvent("e2", "Testland", "clash", "t2", "https://e.com", 20007 * DAY + 9))
    assert state["zone"] == "Testland" and state["count"] == 1
    again = Zones([ZONE], store).state(now=20007 * DAY + 10)
    assert again == [{"zone": "Testland", "count": 2, "ratio": 0.0,
                      "assets": [{"symbol": "GC=F", "direction": 1}]}]


def test_zones_ignore_unknown_zone_and_prune_old_days():
    store = Store(":memory:")
    zones = Zones([ZONE], store)
    assert zones.record(ConflictEvent("e", "Nowhere", "strike", "t", "", 1.0)) is None
    for day in range(19900, 20008):
        zones.record(ConflictEvent(f"e{day}", "Testland", "strike", "t", "", day * DAY))
    assert len(store.get("conflict_counts")["Testland"]) <= 30


def test_shipped_zones_are_valid():
    zones = load_zones()
    assert len(zones) >= 8 and len({z["name"] for z in zones}) == len(zones)
    for z in zones:
        assert z["query"] and z["assets"]
        for a in z["assets"]:
            assert resolve(a["symbol"]).id == a["symbol"] and a["direction"] in (-1, 0, 1)
    assert tomllib.loads(ZONES_FILE.read_text())["zone"] == zones


def test_zones_match_needs_a_place_and_a_conflict_word():
    zones = Zones(load_zones(), Store(":memory:"))
    assert zones.match("Missile strike hits Taiwan port") == ["Taiwan Strait"]
    assert zones.match("Taiwan chipmaker posts record profit") == []       # place, no conflict
    assert zones.match("Missile test announced") == []                     # conflict, no place
    assert zones.match("Houthi drone attack on tanker in Red Sea") == ["Red Sea / shipping"]
    assert zones.match("Nigeria troops clash with bandits") == []          # "Niger" must not match inside "Nigeria"
    assert zones.match("") == []


def test_every_shipped_zone_has_news_keywords():
    assert all(z.get("keywords") for z in load_zones())


def test_zone_matching_on_real_looking_headlines():
    zones = Zones(load_zones(), Store(":memory:"))
    assert zones.match("Russia strikes deal with India on oil") == []           # "strikes deal" is not a strike
    assert zones.match("Taiwan reports 30 Chinese military jets near island") == ["Taiwan Strait"]
    assert zones.match("Israeli strike on Gaza kills dozens") == ["Middle East / Gulf"]
    assert classify("Workers strike deal after walkout") == "other"
