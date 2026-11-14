from terminal.events import ConflictEvent, NewsItem
from terminal.feeds import econ, planes, quakes, ships
from terminal.geo import locate


def test_locate_finds_places_and_prefers_the_longer_name():
    assert locate("Missiles hit Tehran overnight")[0] == "Tehran"
    assert locate("South Korea raises rates")[0] == "South Korea"
    assert locate("Turkey prices rise before Thanksgiving") is None   # the country is listed as Türkiye
    assert locate("Stocks rally") is None
    name, lat, lon = locate("Taiwan chip exports jump")
    assert name == "Taiwan" and 21 < lat < 26 and 119 < lon < 123


def test_planes_parse_keeps_positions_and_flags_military():
    payload = {"ac": [
        {"hex": "ae1234", "flight": "RCH123  ", "t": "C17", "lat": 50.1, "lon": 8.5, "alt_baro": 31000, "gs": 450.2, "track": 91, "dbFlags": 1},
        {"hex": "4d02dd", "flight": "JFA10D", "lat": 45.1, "lon": 6.6, "alt_baro": "ground"},
        {"hex": "nopos"}]}
    out = planes.parse(payload)
    assert [p["id"] for p in out] == ["ae1234", "4d02dd"]
    assert out[0]["mil"] and out[0]["flight"] == "RCH123" and not out[1]["mil"] and out[1]["alt"] == 0
    assert planes.parse({}) == []


def test_ships_parse_and_chokepoint_traffic():
    position = {"MessageType": "PositionReport", "MetaData": {"MMSI": 111, "ShipName": "FRONT ALTA  ", "latitude": 26.5, "longitude": 56.4},
                "Message": {"PositionReport": {"Sog": 0.2, "Cog": 180.0}}}
    static = {"MessageType": "ShipStaticData", "MetaData": {"MMSI": 111, "ShipName": "FRONT ALTA"},
              "Message": {"ShipStaticData": {"Type": 84, "Destination": "FUJAIRAH@@@"}}}
    fleet = ships.Fleet()
    fleet.update(ships.parse(position), 1000.0)
    fleet.update(ships.parse(static), 1001.0)
    assert ships.parse({"MessageType": "Other", "MetaData": {"MMSI": 1}, "Message": {}}) is None
    ship = fleet.positions(1002.0)[0]
    assert ship["name"] == "FRONT ALTA" and ship["cls"] == "tanker" and ship["dest"] == "FUJAIRAH"
    hormuz = next(t for t in fleet.traffic(1002.0) if t["name"] == "Strait of Hormuz")
    assert (hormuz["ships"], hormuz["tankers"], hormuz["stopped"]) == (1, 1, 1)
    assert fleet.positions(1002.0 + ships.KEEP_S + 1) == []   # silent ships drop off the map


def test_a_ship_known_only_by_name_is_not_drawn():
    fleet = ships.Fleet()
    fleet.update({"id": "9", "name": "GHOST", "cls": "cargo", "dest": ""}, 1.0)
    assert fleet.positions(2.0) == []


def test_quakes_parse():
    payload = {"features": [{"id": "us1", "properties": {"mag": 5.5, "place": "Mid-Atlantic Ridge", "time": 1794648373073, "url": "https://x"},
                             "geometry": {"coordinates": [-30.1, 40.2, 10]}},
                            {"id": "bad", "properties": {"mag": None}, "geometry": {"coordinates": [1, 2]}}]}
    assert quakes.parse(payload) == [{"id": "us1", "lat": 40.2, "lon": -30.1, "mag": 5.5, "place": "Mid-Atlantic Ridge",
                                      "ts": 1794648373.073, "url": "https://x"}]


def test_econ_parsers():
    events = econ.parse_calendar([
        {"title": "CPI y/y", "country": "USD", "date": "2026-11-10T08:30:00-05:00", "impact": "High", "forecast": "3.1%", "previous": "3.0%"},
        {"title": "Bank Holiday", "country": "JPY", "date": "2026-11-09T00:00:00-05:00", "impact": "Holiday", "forecast": "", "previous": ""}])
    assert [e["title"] for e in events] == ["Bank Holiday", "CPI y/y"] and events[1]["impact"] == "High"
    curve = econ.parse_curve('Date,"1 Mo","2 Yr","10 Yr"\n11/13/2026,4.10,4.88,5.15\n11/12/2026,4.12,N/A,5.10\n')
    assert curve == [("2026-11-12", {"1 Mo": 4.12, "10 Yr": 5.10}), ("2026-11-13", {"1 Mo": 4.10, "2 Yr": 4.88, "10 Yr": 5.15})]
    fred = econ.parse_fred({"observations": [{"date": "2026-10-01", "value": "4.2"}, {"date": "2026-09-01", "value": "."},
                                             {"date": "2026-08-01", "value": "4.1"}]})
    assert fred == [("2026-08-01", 4.1), ("2026-10-01", 4.2)]


def test_map_news_groups_stories_by_place(ctx):
    ctx.app.state.handle(NewsItem("m1", "Oil jumps after strike on Iran port", "https://example.com/1", "Reuters", 2e9, ""))
    ctx.app.state.handle(NewsItem("m2", "Iran talks resume", "https://example.com/2", "BBC", 2e9 + 1, ""))
    ctx.app.state.handle(NewsItem("m3", "Stocks drift", "https://example.com/3", "BBC", 2e9 + 2, ""))
    places = ctx.get("/api/map/news").json()
    assert [p["place"] for p in places] == ["Iran"] and len(places[0]["stories"]) == 2
    assert {"title", "url", "ts", "source", "weight", "sentiment"} == set(places[0]["stories"][0])


def test_map_zones_carry_coordinates_and_heat(ctx):
    ctx.app.state.handle(ConflictEvent("c1", "Taiwan Strait", "clash", "Drills near Taiwan", "https://example.com", 2e9))
    zones = {z["zone"]: z for z in ctx.get("/api/map/zones").json()}
    assert len(zones) == 9 and 20 < zones["Taiwan Strait"]["lat"] < 27


def test_map_ships_is_empty_without_a_feed_and_planes_reject_bad_positions(ctx):
    assert ctx.get("/api/map/ships").json() == {"ships": [], "traffic": []}
    assert ctx.get("/api/map/planes?lat=200&lon=0").status_code == 400
