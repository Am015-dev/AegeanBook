"""Sky Express beside AEGEAN, offline: the network list, its fares in their own table, the comparison, the fetch script and the two pages."""
import json
import os
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

import db
import fetch_route
import gh
import overview  # noqa: F401  loaded first, so the page's `import overview` finds the root module, not views/overview.py
import skyexpress as sx
from collector import Airline

HERE = os.path.dirname(os.path.abspath(__file__))
d = lambda i: (date.today() + timedelta(i)).isoformat()


def test_network_list_and_serves():
    a = sx.airports()
    assert 55 <= len(a) <= 80 and {"ATH", "SKG", "HER", "CHQ", "RHO", "JMK", "JTR", "LCA", "CDG", "FCO", "MXP", "MUC", "VIE", "MAD", "LIS",
                                   "BER", "HAM", "LYS"} <= set(a)
    assert a["ATH"]["country"] == "Greece" and a["LIL"]["seasonal"] == "1"
    assert sx.serves("ATH", "HER") and sx.serves("HER", "ATH") and not sx.serves("ATH", "BIO") and not sx.serves("ATH", "ATH")
    assert set(pd.read_csv(os.path.join(HERE, "data", "skyexpress_airports.csv")).columns) == {"iata", "city", "country", "seasonal"}
    build = open(os.path.join(HERE, "data", "build_skyexpress_airports.py")).read()
    assert "skyexpress.gr" in build and "wikipedia.org" in build and "# ponytail:" in build


def seed(path, gq=True):
    t = db.connect(str(path))
    now = datetime.now().isoformat()
    for i in range(1, 31):
        for o, dest in (("ATH", "FRA"), ("FRA", "ATH"), ("ATH", "ZRH"), ("ZRH", "ATH")):
            eco = 60.0 if (o, dest, i) == ("ATH", "FRA", 10) else 150.0 + i % 7 * 10
            t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", (d(i), o, dest, "economy", eco, "calendar", now, 1))
            t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", (d(i), o, dest, "business", eco + 100, "calendar", now, 1))
        if gq:
            for o, dest in (("ATH", "FRA"), ("FRA", "ATH")):
                t.execute("INSERT INTO gq_prices VALUES(?,?,?,?,?,?,?,?)", (d(i), o, dest, "economy", 45.0 if i == 10 else 120.0, "calendar", now, 1))
    t.commit()
    return t


def test_cash_by_day_takes_the_newest_economy_nonstop_fare_from_its_own_table(tmp_path):
    t = seed(tmp_path / "t.db")
    old, new = (datetime.now() - timedelta(days=1)).isoformat(), datetime.now().isoformat()
    t.execute("INSERT INTO gq_prices VALUES(?,?,?,?,?,?,?,?)", (d(3), "ATH", "FRA", "economy", 99.0, "day search", new, 1))  # newer than the seed
    t.execute("INSERT INTO gq_prices VALUES(?,?,?,?,?,?,?,?)", (d(4), "ATH", "FRA", "economy", 1.0, "day search", old, 0))  # with a stop
    t.execute("INSERT INTO gq_prices VALUES(?,?,?,?,?,?,?,?)", (d(5), "ATH", "FRA", "business", 1.0, "day search", old, 1))  # not economy
    t.execute("INSERT INTO gq_prices VALUES(?,?,?,?,?,?,?,?)", (d(31), "ATH", "FRA", "economy", None, "day search", new, 1))  # no flight
    t.commit()
    s = sx.cash_by_day(t, "ATH", "FRA", date.today() + timedelta(1), date.today() + timedelta(40))
    assert len(s) == 30 and s[d(3)] == 99.0 and s[d(4)] == 120.0 and s[d(5)] == 120.0 and s[d(10)] == 45.0
    assert sx.cash_by_day(t, "ATH", "BIO", date.today(), date.today() + timedelta(40)).empty
    assert sx.saved_days(t, "ATH", "FRA", date.today() + timedelta(30), date.today() + timedelta(40)) == {d(30), d(31)}  # no flight counts
    assert t.execute("SELECT COUNT(*) FROM prices WHERE price=45").fetchone()[0] == 0  # AEGEAN's table is untouched


def test_compare_and_its_table_and_sentence():
    t = sx.compare(pd.Series({"2026-11-01": 100.0, "2026-11-02": 120.0}), pd.Series({"2026-11-02": 90.0, "2026-11-03": 80.0}))
    assert list(t.index) == ["2026-11-01", "2026-11-02", "2026-11-03"] and list(t.columns) == ["aegean", "skyexpress", "difference"]
    assert t.loc["2026-11-02"].difference == -30.0 and pd.isna(t.loc["2026-11-01"].difference) and pd.isna(t.loc["2026-11-03"].aegean)
    shown = sx.table(t)
    assert list(shown.loc["Mon 02 Nov"]) == ["€120", "€90", "Sky Express, €30 less"] and list(shown.loc["Tue 03 Nov"]) == ["—", "€80", "—"]
    # the head-to-head: each airline's cheapest trip, then both priced on each other's dates
    a3 = dict(depart=date(2026, 11, 2), back=date(2026, 11, 5), nights=3, price=300.0)
    gq = dict(depart=date(2026, 11, 3), back=date(2026, 11, 5), nights=2, price=150.0)
    a3_out, a3_back = pd.Series({"2026-11-02": 100.0, "2026-11-03": 400.0}), pd.Series({"2026-11-05": 200.0})
    gq_out, gq_back = pd.Series({"2026-11-02": 120.0, "2026-11-03": 70.0}), pd.Series({"2026-11-05": 80.0})
    h = sx.head_to_head(a3, gq, a3_out, a3_back, gq_out, gq_back)
    assert list(h.columns) == ["AEGEAN", "Sky Express"] and list(h.index) == ["Cheapest trip", "Dates", "Both on 2–5 Nov", "Both on 3–5 Nov"]
    assert list(h.loc["Cheapest trip"]) == ["€300", "€150"] and list(h.loc["Dates"]) == ["Mon 2 Nov → Thu 5 Nov, 3 nights", "Tue 3 Nov → Thu 5 Nov, 2 nights"]
    assert list(h.loc["Both on 2–5 Nov"]) == ["€300", "€200"] and list(h.loc["Both on 3–5 Nov"]) == ["€600", "€150"]
    assert sx.html_table(h).startswith('<table class="h2h"><tr><th></th><th>AEGEAN</th><th>Sky Express</th></tr><tr><th>Cheapest trip</th><td>€300</td>')
    assert sx.verdict(a3, gq, a3_out, a3_back, gq_out, gq_back) == "Cheapest overall: Sky Express, €150, 3–5 Nov. On the same dates AEGEAN costs €600, €450 more."
    assert sx.verdict(a3, None, a3_out, a3_back, gq_out, gq_back) == "Only AEGEAN has fares saved for these days: €300."
    assert list(sx.head_to_head(None, gq, a3_out, a3_back, pd.Series(dtype=float), None)["AEGEAN"]) == ["no fare saved", "no fare saved", "€600"]
    assert sx.trip_price(a3_out, a3_back, date(2026, 11, 2), None) == 100.0 and sx.trip_price(a3_out, a3_back, date(2026, 11, 9), None) is None
    assert sx.compare(pd.Series(dtype=float), pd.Series(dtype=float)).empty
    best = dict(depart=date(2026, 11, 2), back=date(2026, 11, 5), nights=3, price=210.0)
    assert sx.sentence(best, True) == "Sky Express: €210 on Mon 02 Nov, 3 nights (economy)"
    assert sx.sentence(dict(best, back=None, nights=None, price=90.0), True) == "Sky Express: €90 on Mon 02 Nov (economy one way)"
    assert sx.sentence(None, False).startswith("Sky Express: no economy fares")


def test_fetch_route_with_airline_gq_writes_skyexpress_csv_economy_only(tmp_path, monkeypatch):
    out, d1 = tmp_path / "routes.csv", date.today() + timedelta(1)
    monkeypatch.setattr(fetch_route, "OUT", out)
    asked = []

    def fake(a, b, o, d, skip=(), nonstop=True, **kw):
        asked.append(kw)
        for cabin in kw.get("cabins", ("economy", "business")):
            yield a, cabin, 70.0, "calendar", "ok", None
    monkeypatch.setattr(fetch_route, "fetch_range", fake)
    monkeypatch.setattr(fetch_route.time, "sleep", lambda s: None)
    assert fetch_route.main(f"ATH,HER,{d1},{d1}", True, airline="GQ") == 0
    assert asked == [dict(cabins=("economy",), airline=Airline.GQ)]  # economy only, Sky Express only
    rows = fetch_route.read(tmp_path / "skyexpress.csv")
    assert [(r["orig"], r["dest"], r["cabin"], r["price"]) for r in rows] == [("ATH", "HER", "economy", "70.0")]
    assert not out.exists()  # AEGEAN's file is untouched
    assert (tmp_path / "tracked_routes.csv").read_text().splitlines()[1:] == [f"ATH,HER,1,{date.today()},{d1},{d1},GQ"]
    fetch_route.main(f"ATH,HER,{d1},{d1}", True)  # AEGEAN after it: both cabins, the default kwargs, its own file and tracked row
    assert asked[1] == {} and len(fetch_route.read(out)) == 2
    assert (tmp_path / "tracked_routes.csv").read_text().splitlines()[1:] == [f"ATH,HER,1,{date.today()},{d1},{d1},A3",
                                                                              f"ATH,HER,1,{date.today()},{d1},{d1},GQ"]
    fetch_route.save(fetch_route.read(tmp_path / "skyexpress.csv"), track=True, airline="GQ")  # the --merge step for a GQ job
    assert len(fetch_route.read(tmp_path / "skyexpress.csv")) == 1 and fetch_route.outfile("GQ") == tmp_path / "skyexpress.csv"
    # the merge step also logs every fare it saves in the airline's permanent history, so an on-request fetch is never lost
    assert fetch_route.log_history(fetch_route.read(tmp_path / "skyexpress.csv"), "GQ") == 1
    assert fetch_route.log_history(fetch_route.read(tmp_path / "skyexpress.csv"), "GQ") == 0  # the same fare again: one row, not two
    assert fetch_route.read(tmp_path / "history_skyexpress.csv")[0]["price"] == "70.0" and not (tmp_path / "history_prices.csv").exists()
    assert fetch_route.log_history(fetch_route.read(out), "A3") == 2  # AEGEAN's fetch too, into its own history
    assert fetch_route.log_history(fetch_route.read(out), "A3") == 0  # the daily run saving the same fares later adds nothing
    assert {r["cabin"] for r in fetch_route.read(tmp_path / "history_prices.csv")} == {"economy", "business"}
    main = open(os.path.join(HERE, "fetch_route.py")).read()
    merge = main[main.index('if sys.argv[1] == "--merge"'):main.index('elif sys.argv[1] == "--refresh"')]
    assert "log_history(rows, airline)" in merge and "if airline" not in merge  # every airline, not only Sky Express
    wf = open(os.path.join(HERE, ".github", "workflows", "fetch-route.yml")).read()
    assert "AIRLINE: ${{ inputs.airline }}" in wf and 'default: "A3"' in wf and "data/skyexpress.csv data/history_skyexpress.csv" in wf
    assert "git add data/routes.csv data/tracked_routes.csv data/history_prices.csv" in wf  # the AEGEAN history is committed too
    assert "inputs.airline }}\"" not in wf
    assert "data/skyexpress.csv data/history_skyexpress.csv" in open(os.path.join(HERE, ".github", "workflows", "daily.yml")).read()


def test_refresh_keeps_sky_express_routes_fresh_economy_only(tmp_path, monkeypatch):
    out, today = tmp_path / "routes.csv", date.today()
    monkeypatch.setattr(fetch_route, "OUT", out)
    (tmp_path / "tracked_routes.csv").write_text("orig,dest,nonstop,last_requested,airline\n"
                                                 f"ATH,HER,1,{today},A3\nATH,HER,1,{today},GQ\nATH,FRA,1,{today},GQ\nATH,SKG,1,{today}\n")
    asked = []

    def fake(a, b, o, d, skip=(), nonstop=True, **kw):
        asked.append((o, d, kw.get("airline", Airline.A3).name))
        for cabin in kw.get("cabins", ("economy", "business")):
            yield a, cabin, 88.0, "calendar", "ok", None
    monkeypatch.setattr(fetch_route, "fetch_range", fake)
    assert fetch_route.refresh() == 0
    # the daily legs are skipped for AEGEAN only (ATH-FRA is a daily leg; Sky Express's ATH-FRA is not); a row without the column is AEGEAN's
    assert sorted(asked) == [("ATH", "FRA", "GQ"), ("ATH", "HER", "A3"), ("ATH", "HER", "GQ"), ("ATH", "SKG", "A3")]
    assert {(r["orig"], r["dest"], r["cabin"]) for r in fetch_route.read(out)} == {("ATH", "HER", "economy"), ("ATH", "HER", "business"),
                                                                                  ("ATH", "SKG", "economy"), ("ATH", "SKG", "business")}
    assert {(r["orig"], r["dest"], r["cabin"]) for r in fetch_route.read(tmp_path / "skyexpress.csv")} == {("ATH", "FRA", "economy"),
                                                                                                           ("ATH", "HER", "economy")}
    assert "GQ" not in (tmp_path / "history_prices.csv").read_text()  # the AEGEAN history stays AEGEAN's
    hist = fetch_route.read(tmp_path / "history_skyexpress.csv")  # Sky Express has its own permanent history, written the same way
    assert {(r["orig"], r["dest"], r["cabin"], r["price"]) for r in hist} == {("ATH", "FRA", "economy", "88.0"), ("ATH", "HER", "economy", "88.0")}
    assert fetch_route.refresh() == 0 and len(fetch_route.read(tmp_path / "history_skyexpress.csv")) == 2  # unchanged prices: no new rows


def test_daily_fetches_sky_express_on_the_daily_legs_it_serves(tmp_path, monkeypatch):
    out, today = tmp_path / "routes.csv", date.today()
    monkeypatch.setattr(fetch_route, "OUT", out)
    (tmp_path / "skyexpress_airports.csv").write_text("iata,city,country,seasonal\nATH,Athens,Greece,0\nFRA,Frankfurt,Germany,0\n")
    asked = []

    def fake(a, b, o, d, skip=(), nonstop=True, **kw):
        asked.append((o, d, kw["airline"].name, a, b))
        for cabin in kw["cabins"]:
            yield a, cabin, 66.0, "calendar", "ok", None
    monkeypatch.setattr(fetch_route, "fetch_range", fake)
    assert fetch_route.daily() == 0
    first, last = today + timedelta(1), today + timedelta(90)  # collect_daily.DAYS; ZRH is not in Sky Express's network
    assert asked == [("ATH", "FRA", "GQ", first, last), ("FRA", "ATH", "GQ", first, last)]
    assert {(r["orig"], r["dest"], r["cabin"]) for r in fetch_route.read(tmp_path / "skyexpress.csv")} == {("ATH", "FRA", "economy"),
                                                                                                           ("FRA", "ATH", "economy")}
    assert len(fetch_route.read(tmp_path / "history_skyexpress.csv")) == 2 and not out.exists()
    assert not (tmp_path / "tracked_routes.csv").exists()  # not tracked: --refresh must not fetch them a second time
    assert "AIRLINE=GQ python fetch_route.py --daily" in open(os.path.join(HERE, ".github", "workflows", "daily.yml")).read()


def test_github_dispatch_carries_the_airline(monkeypatch):
    sent = []
    monkeypatch.setattr(gh, "_api", lambda tok, method, path, body=None, **k: sent.append((path, body)))
    d1 = date(2026, 11, 3)
    gh.fetch("tok", [("ATH", "HER", d1, d1)], True, airline="GQ")
    gh.fetch("tok", [("ATH", "HER", d1, d1)], True)
    assert sent[0][1]["inputs"]["airline"] == "GQ" and sent[1][1]["inputs"]["airline"] == "A3"
    assert sent[0][1]["inputs"]["legs"] == "ATH,HER,2026-11-03,2026-11-03"


def test_db_loads_sky_express_fares_into_their_own_table(tmp_path, monkeypatch):
    now = datetime.now().isoformat()
    (tmp_path / "skyexpress.csv").write_text("day,orig,dest,cabin,price,source,fetched,nonstop\n"
                                             f"{d(2)},ATH,HER,economy,55.0,calendar,{now},1\n{d(3)},ATH,HER,economy,,day search,{now},1\n")
    monkeypatch.setattr(db, "SKYEXPRESS", str(tmp_path / "skyexpress.csv"))
    t = db.connect(str(tmp_path / "t.db"))
    assert t.execute("SELECT COUNT(*) FROM gq_prices").fetchone()[0] == 2 and t.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 0
    assert t.execute("SELECT label FROM runs").fetchone()[0] == "Sky Express: 1 routes"
    db.connect(str(tmp_path / "t.db"))  # loading is idempotent, once per file version
    assert t.execute("SELECT COUNT(*) FROM gq_prices").fetchone()[0] == 2
    assert sx.cash_by_day(t, "ATH", "HER", date.today(), date.today() + timedelta(9)).to_dict() == {d(2): 55.0}


def test_price_chart_draws_an_extra_dashed_series_without_rings():
    from ui import price_chart
    df = pd.DataFrame({"economy": [100.0, 90.0], "business": [None, None], "skyexpress": [80.0, None]}, index=["2026-11-01", "2026-11-02"])
    fig = price_chart(df, {"economy": ("Economy", "#00f"), "business": ("Business", "#f00")}, extra={"skyexpress": ("Sky Express economy", "#c00")})
    dashed = [t for t in fig.data if t.line.dash == "dash"]
    assert len(dashed) == 1 and dashed[0].name == "Sky Express economy" and len([t for t in fig.data if t.marker.size == 15]) == 1  # one ring: economy
    assert len(price_chart(df, {"economy": ("Economy", "#00f")}).data) == 2  # existing callers: unchanged


def pages(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.chdir(tmp_path)
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return lambda name: AppTest.from_file(os.path.join(HERE, "views", name), default_timeout=60)


def test_overview_compares_with_sky_express_when_fares_are_saved(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch)
    seed(tmp_path / "tracker.db")
    at = open_page("overview.py").run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Sky Express" not in text  # off by default
    at.toggle(key="ov-gq-ATHFRA").set_value(True).run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    # the pass: AEGEAN €60 out on day 10 + €150 back = €210; Sky Express €45 out on day 10 + €120 back = €165, the same filters
    assert "€210" in text and "AEGEAN Cheapest return" in text  # the pass is AEGEAN's; the comparison follows
    head = next(m.value for m in at.markdown if 'class="h2h"' in m.value)  # the head-to-head table, right under the pass
    assert head.count("<tr>") == 5 and "<th>Cheapest trip</th><td>€210</td><td>€165</td>" in head and "<th>Both on " in head
    assert "Cheapest overall: Sky Express, €165" in text and "On the same dates AEGEAN costs €" in text
    days = [m.value for m in at.markdown if 'class="h2h"' in m.value][1]  # the day-by-day table, under the head-to-head
    assert "<th>AEGEAN</th><th>Sky Express</th><th>Cheaper</th>" in days and days.count("<td>€45</td>") == 1
    assert days.count("<td>Sky Express, €15 less</td>") == 1  # day 10: €60 against €45
    assert not at.button  # nothing to fetch: the fares are there
    at.button_group(key="ov-grid-cabin").set_value("business").run()
    assert not at.exception and "Sky Express" not in " ".join(m.value for m in at.markdown)  # economy only for now


def test_overview_offers_to_fetch_sky_express_fares_on_github_when_none_are_saved(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch)
    seed(tmp_path / "tracker.db", gq=False)
    started, state = [], {"run": None}
    monkeypatch.setattr(gh, "token", lambda: "tok")
    monkeypatch.setattr(gh, "fetch", lambda tok, legs, nonstop, airline="A3": started.append((legs, nonstop, airline)) or "gq123456")
    monkeypatch.setattr(gh, "run", lambda tok, request, workflow="fetch-route.yml": state["run"])
    at = open_page("overview.py").run()
    assert not at.exception and not at.toggle  # nothing to compare yet, so no toggle: the fetch button sits in its place
    fetch = [b for b in at.button if b.label == "Search Sky Express fares"]
    assert len(fetch) == 1
    fetch[0].click().run()
    assert not at.exception and len(started) == 1
    legs, nonstop, airline = started[0]
    assert airline == "GQ" and nonstop is True and [(o, d_) for o, d_, _, _ in legs] == [("ATH", "FRA"), ("FRA", "ATH")]
    assert legs[0][2] == date.today() + timedelta(1) and legs[0][3] == date.today() + timedelta(30)  # the saved AEGEAN range
    assert "Searching on GitHub" in " ".join(i.value for i in at.info) and not [b for b in at.button if b.label == "Search Sky Express fares"]
    state["run"] = dict(status="completed", conclusion="success", url="u")
    at.run()
    assert not at.exception and "Searching on GitHub" not in " ".join(i.value for i in at.info)
    # without a token the fares cannot be fetched from here (the Flights page does it on your own computer): neither button nor toggle
    monkeypatch.setattr(gh, "token", lambda: None)
    at = open_page("overview.py").run()
    assert not at.exception and not [b for b in at.button if b.label == "Search Sky Express fares"] and not at.toggle
    at.selectbox(key=f"ov-grid-dest-ATH").set_value("ZRH").run()  # Zurich is not in Sky Express's network: no toggle at all
    assert not at.exception and not at.toggle


def test_flights_page_fetches_sky_express_too_and_draws_it_dashed(tmp_path, monkeypatch):
    import collector
    open_page = pages(tmp_path, monkeypatch)
    t = seed(tmp_path / "tracker.db")
    started = []
    monkeypatch.setattr(gh, "token", lambda: "tok")
    monkeypatch.setattr(gh, "fetch", lambda tok, legs, nonstop, airline="A3": started.append((legs, airline)) or f"r{len(started)}")
    monkeypatch.setattr(gh, "run", lambda tok, request, workflow="fetch-route.yml": None)
    monkeypatch.setattr(collector, "fetch_range", lambda *a, **k: pytest.fail("must not search from the app's own server"))
    at = open_page("flights.py").run()
    assert not at.exception and at.toggle(key="fl-gq-ATHFRA").value is False
    assert "Sky Express fares can be fetched too" not in " ".join(c.value for c in at.caption)  # fares saved: the toggle is in the card
    at.toggle(key="fl-gq-ATHFRA").set_value(True).run()
    assert not at.exception
    assert "Dashed: Sky Express economy, cheapest €45" in " ".join(c.value for c in at.caption)
    at.button[1].click().run()  # Update prices: the AEGEAN days are all saved (30 of 30), Sky Express's too, so nothing is dispatched
    assert not at.exception and not started and "already saved" in " ".join(i.value for i in at.info)
    t.execute(f"DELETE FROM gq_prices WHERE day > ?", (d(20),))  # ten Sky Express days missing
    t.commit()
    at.button[1].click().run()
    assert not at.exception and len(started) == 1
    legs, airline = started[0]
    assert airline == "GQ" and legs == [("ATH", "FRA", date.today() + timedelta(21), date.today() + timedelta(30)),
                                        ("FRA", "ATH", date.today() + timedelta(21), date.today() + timedelta(30))]
    assert "Sky Express fares appear here" in " ".join(i.value for i in at.info)


def test_flights_page_keeps_the_sky_express_toggle_under_options_until_its_fares_are_saved(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch)
    seed(tmp_path / "tracker.db", gq=False)
    at = open_page("flights.py").run()
    assert not at.exception and at.toggle(key="fl-gq-ATHFRA").value is False  # still there, under Options
    assert "Sky Express fares can be fetched too: Options → Compare with Sky Express" in " ".join(c.value for c in at.caption)
    at.toggle(key="fl-gq-ATHFRA").set_value(True).run()
    assert not at.exception and "No Sky Express fares saved for these days yet" in " ".join(c.value for c in at.caption)
    at.selectbox(key="dest").set_value("ZRH").run()  # Zurich is not in Sky Express's network: no toggle, no caption
    assert not at.exception and not at.toggle and "Sky Express" not in " ".join(c.value for c in at.caption)
