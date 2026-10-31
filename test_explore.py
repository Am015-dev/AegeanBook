"""Explore page, offline: every destination from an airport, cheapest first, on a seeded database."""
import os
from datetime import date, datetime, timedelta

import pandas as pd

import explore as xp
import overview
from test_overview import pages, seed as seed_daily

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
    assert list(ret.dest) == ["SKG", "FRA", "ZRH"]  # Chania has no fares back, so no return trip
    assert ret.price[0] == 41 + 43 and ret.depart[0] == start and ret.back[0] == start + timedelta(2) and ret.nights[0] == 2
    assert ret.price[1] == 60 + 150 and ret.depart[1] == today + timedelta(10) and ret.nights[1] == 4  # the €60 day out, €150 back
    assert ret.typical[0] > ret.price[0] and ret.price[2] == 300.0
    biz = xp.destinations(legs, "ATH", start, end, "business")
    assert list(biz.dest) == ["FRA", "ZRH"] and biz.price[0] == 160.0  # SKG and CHQ have economy fares only
    assert xp.destinations(legs, "FRA", today + timedelta(40), today + timedelta(60), "economy").empty  # nothing saved that far


def test_budget_splits_within_and_over():
    t = pd.DataFrame([dict(dest="SKG", price=41.0), dict(dest="FRA", price=60.0), dict(dest="ZRH", price=150.0)])
    within, over = xp.fit(t, 100)
    assert list(within.dest) == ["SKG", "FRA"] and list(over.dest) == ["ZRH"]
    within, over = xp.fit(t, 0)
    assert within.empty and list(over.dest) == ["SKG", "FRA", "ZRH"]


def test_explore_page_lists_the_cheapest_first_and_greys_the_rest(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch, None)
    at = open_page("explore.py").run()
    assert not at.exception and "No fares are saved yet" in at.info[0].value
    seed(tmp_path / "tracker.db")
    at = open_page("explore.py").run()
    assert not at.exception
    cards = [m.value for m in at.markdown if 'class="xp"' in m.value]
    assert len(cards) == 4 and "Thessaloniki" in cards[0] and "SKG" in cards[0] and "€41" in cards[0] and "30 flight days" in cards[0]
    assert cards.index(next(c for c in cards if "FRA" in c)) < cards.index(next(c for c in cards if "ZRH" in c))
    assert at.slider[0].value == 150  # the default budget fits every destination
    at.slider[0].set_value(50).run()
    assert not at.exception and len([m for m in at.markdown if 'class="xp"' in m.value]) == 4  # over budget: still listed, greyed
    at.slider[0].set_value(0).run()
    assert "Nothing from Athens fits €0" in at.caption[0].value and "Thessaloniki at €41" in at.caption[0].value
    at.button_group(key="xp-trip").set_value("return").run()
    assert not at.exception
    cards = [m.value for m in at.markdown if 'class="xp"' in m.value]
    assert len(cards) == 3 and "€84" in cards[0] and "2 nights" in cards[0] and "CHQ" not in " ".join(cards)
    at.selectbox(key="xp-orig").set_value("SKG").run()
    assert not at.exception and "Athens" in [m.value for m in at.markdown if 'class="xp"' in m.value][0]


def test_overview_opens_the_route_from_the_query_params(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch, None)
    seed(tmp_path / "tracker.db")
    at = open_page("overview.py")
    at.query_params["from"], at.query_params["to"] = "SKG", "ATH"  # Explore's "See dates"
    at.run()
    assert not at.exception
    assert at.selectbox(key="ov-grid-orig").value == "SKG" and at.selectbox(key="ov-grid-dest-SKG").value == "ATH"
    assert "Thessaloniki → Athens → Thessaloniki" in " ".join(c.value for c in at.caption)
    at = open_page("overview.py")
    at.query_params["from"] = "XXX"  # an airport without fares is ignored
    at.run()
    assert not at.exception and at.selectbox(key="ov-grid-orig").value == "ATH"
