import pytest

from terminal.brain.analysis import Analyser
from terminal.brain.news_rules import RulesAnalyser
from terminal.events import NewsItem
from terminal.symbols import resolve

RULES = RulesAnalyser()


def analyse(title, watchlist=(), outlets=1, source="x", summary=""):
    return RULES.analyse(NewsItem("i", title, "", source, 0, summary), set(watchlist), outlets)


def symbols(a):
    return {i.symbol for i in a.impacts}


CASES = [  # title, symbols that must be tagged, symbols that must not, sentiment (None: not asserted)
    ("Apple shares jump after iPhone sales beat estimates", {"AAPL"}, set(), 1),
    ("An apple a day keeps the doctor away, study finds", set(), {"AAPL"}, 0),
    ("An Apple a Day Keeps the Doctor Away", set(), {"AAPL"}, 0),
    ("Target cuts forecast as shoppers pull back", {"TGT"}, set(), -1),
    ("Regulators target misleading ads", set(), {"TGT"}, None),
    ("$NVDA upgraded by analysts", {"NVDA"}, set(), 1),
    ("Analysts lift price targets on AMD and TSM", {"AMD", "TSM"}, set(), None),
    ("NOW IS THE TIME TO COST ALL THINGS", set(), {"NOW", "COST", "ALL"}, None),
    ("OPEC agrees surprise output cut", {"CL=F", "XLE"}, set(), 0),
    ("Fed signals rate cuts as inflation cools", {"TLT", "^TNX"}, set(), 1),
    ("Farmers fed up with new rules", set(), {"TLT", "^TNX"}, None),
    ("US tightens chip export controls on China", {"NVDA", "AMD", "TSM", "SMH"}, set(), -1),
    ("Bitcoin ETF approval sparks rally", {"BTC-USD"}, set(), 1),
    ("Hackers steal $200 million from crypto exchange", {"BTC-USD", "ETH-USD"}, set(), -1),
    ("Company does not expect losses to widen", set(), set(), 0),
    ("Microsoft and Alphabet report record cloud revenue", {"MSFT", "GOOGL"}, set(), 1),
    ("Tesla recalls 200,000 vehicles over faulty software", {"TSLA"}, set(), -1),
    ("JPMorgan profit beats as trading revenue surges", {"JPM"}, set(), 1),
    ("Meta to cut 5% of staff in performance-based layoffs", {"META"}, set(), -1),
    ("Meta-analysis shows coffee is fine", set(), {"META"}, 0),
    ("Oil prices surge after Israel strikes Iran", {"CL=F"}, {"TLT", "^TNX"}, 1),
    ("Gold hits all-time high as dollar weakens", {"GC=F"}, set(), 1),
    ("She won a gold medal in Paris", set(), {"GC=F"}, None),
    ("Boeing 737 Max grounded after door blowout", {"BA"}, set(), -1),
    ("Hermit crab finds new shell on beach", set(), {"SHEL"}, None),
    ("Visa requirements eased for tourists", set(), {"V"}, None),
]


@pytest.mark.parametrize("title,must,must_not,sent", CASES)
def test_rules(title, must, must_not, sent):
    a = analyse(title)
    assert must <= symbols(a), a.reasons
    assert not must_not & symbols(a), a.reasons
    if sent is not None:
        assert a.sentiment == sent, a.reasons


def test_theme_direction_and_entity_direction():
    a = analyse("Fed signals rate cuts as inflation cools")
    by = {i.symbol: i.direction for i in a.impacts}
    assert by["TLT"] == 1 and by["^TNX"] == -1          # theme sets direction
    a = analyse("Tesla recalls 200,000 vehicles over faulty software")
    assert {i.symbol: i.direction for i in a.impacts}["TSLA"] == -1   # entity takes the story's mood


def test_summary_is_used_for_context_and_entities():
    assert "AAPL" in symbols(analyse("Apple unveils new lineup", summary="The iPhone maker said on Tuesday"))


def test_weight_rises_with_watchlist_and_outlets():
    low = analyse("Tesla shares plunge on recall")
    high = analyse("Tesla shares plunge on recall", watchlist={"TSLA"}, outlets=3)
    assert high.weight == low.weight + 2 and low.weight == 2 and high.weight <= 5


def test_weight_tier_one_source_and_cap():
    assert analyse("Quiet day", source="CNBC").weight == analyse("Quiet day").weight + 1
    assert analyse("Tesla shares crash and plunge to record low", {"TSLA"}, 9, "CNBC").weight == 5


def test_every_impact_has_a_reason_and_valid_symbol():
    for title, *_ in CASES:
        a = analyse(title)
        assert 1 <= a.weight <= 5 and a.sentiment in (-1, 0, 1)
        for impact in a.impacts:
            assert impact.reason and impact.direction in (-1, 0, 1)
        assert len(a.reasons) >= len(a.impacts)


def test_empty_and_odd_titles_are_neutral():
    for title in ("", "   ", "!!!", "x" * 5000, "🚀🚀🚀"):
        a = analyse(title)
        assert (a.impacts, a.sentiment, a.weight) == ([], 0, 1)


def test_data_files_only_name_resolvable_symbols():
    assert len(RULES.entities) >= 150 and len(RULES.themes) >= 15
    names = [e.symbol for e in RULES.entities] + [i["symbol"] for t in RULES.themes for i in t["impacts"]]
    bad = [s for s in names if resolve(s) is None or resolve(s).id != s]
    assert bad == []
    assert len({e.symbol for e in RULES.entities}) == len(RULES.entities)


def test_rules_analyser_satisfies_protocol():
    assert isinstance(RULES, Analyser)


def test_strong_wording_needs_the_whole_word():
    assert analyse("Warner warns of warm winter in warehouse").weight == 1   # "war" is not in there
    assert analyse("War fears grip markets").weight == 2
    assert analyse("Stocks plunged at the open").weight == 2
