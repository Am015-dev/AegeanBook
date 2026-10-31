"""Anywhere on the Overview (the former Explore page), offline: every destination from an airport, cheapest first, on a seeded database."""
import os
from datetime import date, datetime, timedelta

import explore as xp
import overview
from test_overview import pages, routes, seed as seed_daily

HERE = os.path.dirname(os.path.abspath(__file__))


def seed(path):
    """The daily legs (ATH-FRA €60 on day 10, else €150-210; ATH-ZRH €150-210), plus Thessaloniki both ways from €41 and Chania one way only
    from €46."""
    t = seed_daily(path)
    now, today = datetime.now().isoformat(), date.today()
    for i in range(1, 31):
        d = (today + timedelta(i)).isoformat()
        for o, dest, base in (("ATH", "SKG", 40.0), ("SKG", "ATH", 40.0), ("ATH", "CHQ", 45.0)):
            t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", (d, o, dest, "economy", base + i, "calendar", now, 1))
    t.commit()
    return t


def test_destinations_one_way_and_return_cheapest_first(tmp_path):
    t = seed(tmp_path / "t.db")
    today = date.today()
    legs = overview.legs(t, today)
    assert xp.origins(legs) == ["ATH", "FRA", "ZRH", "SKG"]
    start, end = today + timedelta(1), today + timedelta(30)
    one = xp.destinations(legs, "ATH", start, end, "economy")
    assert list(one.dest) == ["SKG", "CHQ", "FRA", "ZRH"] and list(one.price) == [41.0, 46.0, 60.0, 150.0]
    assert one.depart[0] == start and one.back[0] is None and one.nights[0] is None and list(one.days) == [30, 30, 30, 30]
    assert one.typical[2] == 175.0  # the median ATH -> FRA fare, not the one €60 day
    ret = xp.destinations(legs, "ATH", start, end, "economy", xp.NIGHTS)
    assert list(ret.dest) == ["CHQ", "SKG", "FRA", "ZRH"]  # Chania has no fares back, so its one-way is listed (searched one way only)
    assert ret.price[0] == 46 and ret.back[0] is None and ret.typical[0] == 60.5
    assert ret.price[1] == 41 + 43 and ret.depart[1] == start and ret.back[1] == start + timedelta(2) and ret.nights[1] == 2
    assert ret.price[2] == 60 + 150 and ret.depart[2] == today + timedelta(10) and ret.nights[2] == 4  # the €60 day out, €150 back
    assert ret.typical[1] > ret.price[1] and ret.price[3] == 300.0
    assert list(xp.destinations(legs, "ATH", start, end, "economy", [3]).nights.dropna()) == [3, 3, 3]  # the nights picked in the bar
    biz = xp.destinations(legs, "ATH", start, end, "business")
    assert list(biz.dest) == ["FRA", "ZRH"] and biz.price[0] == 160.0  # SKG and CHQ have economy fares only
    assert xp.destinations(legs, "FRA", today + timedelta(40), today + timedelta(60), "economy").empty  # nothing saved that far


def test_overview_anywhere_lists_the_cheapest_first_and_see_dates_opens_the_route(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch, None)
    seed(tmp_path / "tracker.db")
    at = open_page("overview.py").run()
    at.selectbox(key="ov-grid-dest-ATH").set_value("ANY").run()
    assert not at.exception
    rows = lambda: next(m.value for m in at.markdown if 'class="fares"' in m.value).split('<div class="fare">')[1:]  # one grouped list
    cards = rows()
    assert len(cards) == 4 and "CHQ" in cards[0] and "€46" in cards[0] and "one way" in cards[0]  # searched one way only
    assert "Thessaloniki" in cards[1] and "SKG" in cards[1] and "€84" in cards[1] and "2 nights" in cards[1] and "flights on 30 of 30 days" in cards[1]
    assert "usually €" in cards[1] and cards.index(next(c for c in cards if "FRA" in c)) < cards.index(next(c for c in cards if "ZRH" in c))
    assert not [m for m in at.markdown if 'class="pass"' in m.value] and "From Athens, the next 30 days, economy" in " ".join(m.value for m in at.markdown)
    assert routes(at)[0][0] == "ATH → FRA"  # the route table still closes the page
    at.slider(key="ov-nights").set_value((5, 5)).run()
    assert not at.exception and "5 nights" in rows()[1]
    at.button_group(key="ov-grid-cabin").set_value("business").run()
    assert len(rows()) == 2  # business fares are saved to FRA and ZRH only
    at.button_group(key="ov-grid-cabin").set_value("economy").run()
    at.button_group(key="xp-see").set_value("SKG").run()  # See dates for Thessaloniki: the same page, Thessaloniki in To
    assert not at.exception and at.selectbox(key="ov-grid-dest-ATH").value == "SKG"
    assert "Athens → Thessaloniki → Athens" in " ".join(c.value for c in at.caption)
    at.selectbox(key="ov-grid-orig").set_value("SKG").run()
    at.selectbox(key="ov-grid-dest-SKG").set_value("ANY").run()
    assert not at.exception and "Athens" in rows()[0]


def test_overview_opens_the_route_from_the_query_params(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch, None)
    seed(tmp_path / "tracker.db")
    at = open_page("overview.py")
    at.query_params["from"], at.query_params["to"] = "SKG", "ATH"  # a link from the Expert, or an old Explore link
    at.run()
    assert not at.exception
    assert at.selectbox(key="ov-grid-orig").value == "SKG" and at.selectbox(key="ov-grid-dest-SKG").value == "ATH"
    assert "Thessaloniki → Athens → Thessaloniki" in " ".join(c.value for c in at.caption)
    assert "Pick a route, or Anywhere" not in " ".join(c.value for c in at.caption)  # the route was chosen, by the link
    at = open_page("overview.py")
    at.query_params["from"] = "XXX"  # an airport without fares is ignored
    at.run()
    assert not at.exception and at.selectbox(key="ov-grid-orig").value == "ATH"
