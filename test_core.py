"""Offline tests: no network. Run with `pytest`. Live checks are in the app's Health page."""
import time
from datetime import date, timedelta

import collector
import db
import hotels
import jobs
import par
import pytest


@pytest.fixture(autouse=True)
def no_daily_file(monkeypatch):  # the committed data/daily.csv must not leak into the test databases
    monkeypatch.setattr(db, "DAILY", "/nonexistent/daily.csv")
    monkeypatch.setattr(db, "DAILY_HOTEL", "/nonexistent/daily_hotel.csv")


def test_parallel_retries_and_reports_errors():
    seen = {"n": 0}

    def flaky(x):
        if x == 1 and seen["n"] == 0:
            seen["n"] += 1
            raise ValueError("once")
        if x == 2:
            raise ValueError("always")
        return x * 10

    got = {i: (r, e) for i, r, e in par.parallel([0, 1, 2], flaky)}
    assert got[0] == (0, None) and got[1] == (10, None) and got[2][0] is None and "always" in got[2][1]


def test_every_calendar_gap_is_checked_once(monkeypatch):
    """The calendar leaves out real flights (ATH-BIO summer Sundays), so every gap is searched; once, as the calendar already said empty."""
    d0, asked = date(2026, 11, 1), []
    monkeypatch.setattr(collector, "fetch_cabin", lambda *a: {"2026-11-01": 100.0})
    monkeypatch.setattr(collector, "fetch_day", lambda o, d, c, day, n: asked.append(day) or (222.0 if day == d0 + timedelta(3) else None))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    got = {g[0]: (g[2], g[3], g[4]) for g in collector.fetch_range(d0, d0 + timedelta(8), "ATH", "BIO", cabins=("economy",))}
    assert got[d0] == (100.0, "calendar", "ok") and got[d0 + timedelta(3)] == (222.0, "day search", "ok")  # a Wednesday the calendar missed
    assert got[d0 + timedelta(1)] == (None, "", "none") and len(got) == 9
    assert sorted(asked) == [d0 + timedelta(i) for i in range(1, 9)]  # each gap once, not twice


def test_business_is_not_searched_on_days_without_an_economy_flight(monkeypatch):
    d0, asked = date(2026, 11, 1), []
    monkeypatch.setattr(collector, "fetch_cabin", lambda o, d, c, *a: {"2026-11-01": 100.0})
    monkeypatch.setattr(collector, "fetch_day", lambda o, d, c, day, n: asked.append((day, c)) or None)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    rows = list(collector.fetch_range(d0, d0 + timedelta(7), "ATH", "BIO"))
    assert sorted(asked) == [(d0 + timedelta(i), "economy") for i in range(1, 8)]  # business is not asked on those days
    assert len(rows) == 16 and all(r[4] in ("ok", "none") for r in rows)


def test_empty_answer_is_asked_twice_when_the_calendar_failed(monkeypatch):
    calls = []
    monkeypatch.setattr(collector, "fetch_cabin", lambda *a: (_ for _ in ()).throw(RuntimeError("Google returned no date grid")))
    monkeypatch.setattr(collector, "fetch_day", lambda *a: calls.append(1) or (150.0 if len(calls) > 1 else None))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    d0 = date(2026, 11, 1)
    assert next(collector.fetch_range(d0, d0, "ATH", "FRA", cabins=("economy",)))[2:5] == (150.0, "day search", "ok")


def test_route_without_a_calendar_is_settled_by_three_probes(monkeypatch):
    """ATH-LCY: Google has no date grid because AEGEAN does not fly it. Three spread-out empty days say so for the whole range."""
    asked = []
    monkeypatch.setattr(collector, "fetch_cabin", lambda *a: (_ for _ in ()).throw(RuntimeError("Google returned no date grid")))
    monkeypatch.setattr(collector, "fetch_day", lambda o, d, c, day, n: asked.append((day, c)) or None)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    d0 = date(2026, 11, 1)
    rows = list(collector.fetch_range(d0, d0 + timedelta(29), "ATH", "LCY"))
    assert len(rows) == 60 and all(r[2] is None and r[4] == "none" and r[5] is None for r in rows)
    assert sorted(r[0] for r in rows if r[1] == "economy") == [d0 + timedelta(i) for i in range(30)]
    assert all(r[3] == "probe" for r in rows if r[1] == "economy")
    assert len(asked) <= 3 and {c for _, c in asked} == {"economy"}  # first, middle, last; business needs no asking at all
    assert {d for d, _ in asked} == {d0, d0 + timedelta(15), d0 + timedelta(29)}


def test_route_without_a_calendar_but_with_a_flight_is_searched_day_by_day(monkeypatch):
    """No date grid, yet a probe finds a flight (Google has calendar holes): every day is still searched, as before."""
    asked = []
    monkeypatch.setattr(collector, "fetch_cabin", lambda *a: (_ for _ in ()).throw(RuntimeError("Google returned no date grid")))
    monkeypatch.setattr(collector, "fetch_day", lambda o, d, c, day, n: asked.append(day) or (99.0 if day == d0 + timedelta(15) else None))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    d0 = date(2026, 11, 1)
    got = {g[0]: g[2:5] for g in collector.fetch_range(d0, d0 + timedelta(29), "ATH", "BIO", cabins=("economy",))}
    assert len(got) == 30 and got[d0 + timedelta(15)] == (99.0, "day search", "ok") and got[d0] == (None, "", "none")
    assert set(asked) == {d0 + timedelta(i) for i in range(30)} and "probe" not in {v[1] for v in got.values()}


def test_rate_limit_during_the_probes_stops_everything(monkeypatch):
    asked = []

    def limited(o, d, c, day, n):
        asked.append(day)
        raise RuntimeError("SearchHTTPError: HTTP 429")
    monkeypatch.setattr(collector, "fetch_cabin", lambda *a: (_ for _ in ()).throw(RuntimeError("Google returned no date grid")))
    monkeypatch.setattr(collector, "fetch_day", limited)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    d0 = date(2026, 11, 1)
    rows = list(collector.fetch_range(d0, d0 + timedelta(29), "ATH", "LCY", workers=1))
    assert len(rows) == 60 and all(r[4] == "error" and "429" in r[5] for r in rows)
    assert len(asked) <= 3


def test_total_failure_is_reported_not_hidden(monkeypatch):
    def boom(*a):
        raise RuntimeError("blocked")
    monkeypatch.setattr(collector, "fetch_cabin", boom)
    monkeypatch.setattr(collector, "fetch_day", boom)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    d0 = date(2026, 11, 1)
    rows = list(collector.fetch_range(d0, d0 + timedelta(1), "ATH", "FRA", cabins=("economy", "business")))
    assert len(rows) == 4 and all(r[4] == "error" and "blocked" in r[5] for r in rows)


def test_hotel_failure_and_empty(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    d0 = date(2026, 11, 1)
    monkeypatch.setattr(hotels, "fetch_day", lambda *a: None)
    assert next(hotels.fetch_range(d0, d0, "q", "f"))[3] == "none"

    def boom(*a):
        raise RuntimeError("x")
    monkeypatch.setattr(hotels, "fetch_day", boom)
    assert next(hotels.fetch_range(d0, d0, "q", "f"))[3] == "error"


def test_job_never_dies_silently_and_can_stop():
    jobs.start_job("t-bad", 1, lambda j: 1 / 0)
    time.sleep(0.3)
    assert "ZeroDivisionError" in jobs.JOBS["t-bad"]["error"] and not jobs.JOBS["t-bad"]["running"]

    def work(j):
        for _ in range(100):
            if j["cancel"]:
                return
            jobs.record(j, "ok")
            time.sleep(0.02)
    jobs.start_job("t-stop", 100, work)
    time.sleep(0.1)
    jobs.JOBS["t-stop"]["cancel"] = True
    time.sleep(0.2)
    assert not jobs.JOBS["t-stop"]["running"] and jobs.JOBS["t-stop"]["done"] < 100


def test_old_rows_are_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "RETENTION_DAYS", 5)  # the real default, whatever RETENTION_DAYS the shell running the tests has set
    p = str(tmp_path / "t.db")
    d = db.connect(p)
    old = (__import__("datetime").datetime.now() - timedelta(days=6)).isoformat()
    d.execute("INSERT INTO prices(day, orig, dest, cabin, price, airlines, fetched) VALUES('2026-11-01','A','B','economy',1,'x',?)", (old,))
    d.commit()
    assert db.connect(p).execute("SELECT count(*) FROM prices").fetchone()[0] == 0


def test_hotel_library_failure_is_an_error_not_no_hotel(monkeypatch):
    monkeypatch.setattr(hotels.stays, "search_hotels", lambda *a, **k: {"success": False, "error": "Network error", "hotels": []})
    try:
        hotels.fetch_day("q", "f", date(2026, 11, 1))
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "Network error" in str(e)


def test_dead_network_gives_up_fast(monkeypatch):
    def dead(*a):
        raise ConnectionError("no network")
    monkeypatch.setattr(collector, "fetch_cabin", dead)
    monkeypatch.setattr(collector, "fetch_day", dead)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    d0 = date(2026, 11, 1)
    rows = list(collector.fetch_range(d0, d0 + timedelta(29), "ATH", "FRA"))
    assert len(rows) == 60 and all(r[4] == "error" for r in rows) and any("Gave up" in r[5] for r in rows)


def test_rate_limit_stops_everything_immediately(monkeypatch):
    calls = []

    def limited(*a):
        if "BIO" in a:  # threads left over from earlier tests may still call in; count only this test's route
            calls.append(1)
        raise RuntimeError("SearchHTTPError: HTTP 429")
    monkeypatch.setattr(collector, "fetch_cabin", limited)
    monkeypatch.setattr(collector, "fetch_day", limited)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    d0 = date(2026, 11, 1)
    rows = list(collector.fetch_range(d0, d0 + timedelta(29), "ATH", "BIO"))
    assert len(rows) == 60 and all(r[4] == "error" and "429" in r[5] for r in rows)
    assert len(calls) <= 2  # only the two calendar calls; no day-by-day hammering


def test_saved_days_only_count_the_chosen_dates(tmp_path):
    """Regression: with Nov and Dec saved, a January update used to say "already saved" and fetch nothing."""
    from datetime import datetime
    d = db.connect(str(tmp_path / "t.db"))
    now = datetime.now().isoformat()
    for i in range(61):
        for cab in ("economy", "business"):
            d.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)",
                      ((date(2026, 11, 1) + timedelta(i)).isoformat(), "ATH", "FRA", cab, 100.0, "calendar", now, 1))
    assert db.saved_flight_days(d, "ATH", "FRA", True, date(2027, 1, 1), date(2027, 1, 31)) == set()
    assert len(db.saved_flight_days(d, "ATH", "FRA", True, date(2026, 12, 1), date(2026, 12, 31))) == 62
    d.execute("INSERT INTO hotel VALUES('2026-11-14','q','f',1,'H',150,?,2)", (now,))
    assert db.saved_hotel_days(d, "q", "f", 1, 2, date(2026, 12, 1), date(2026, 12, 31)) == set()
    assert db.saved_hotel_days(d, "q", "f", 1, 2, date(2026, 11, 1), date(2026, 11, 30)) == {"2026-11-14"}


def test_awards_can_be_cleared_and_bad_values_are_ignored(tmp_path):
    d = db.connect(str(tmp_path / "t.db"))
    nan = float("nan")
    db.save_awards(d, [("2026-11-01", 25000, nan), ("2026-11-02", 30000.0, 60), ("2026-11-03", 0, 10)], "ATH", "FRA")
    assert d.execute("SELECT day, miles, taxes FROM awards ORDER BY day").fetchall() == [("2026-11-01", 25000, 0), ("2026-11-02", 30000, 60)]
    db.save_awards(d, [("2026-11-01", nan, nan), ("2026-11-02", 30000, 60)], "ATH", "FRA")  # user cleared the first cell
    assert d.execute("SELECT day FROM awards").fetchall() == [("2026-11-02",)]


def test_page_text_is_escaped(monkeypatch):
    import ui
    shown = []
    monkeypatch.setattr(ui.st, "markdown", lambda s, **k: shown.append(s))
    ui.route('<img src=x onerror=alert(1)>', None, "a & b")
    ui.kpis([("Cheapest", "€1", '<script>x</script>', "#000")])
    assert "<img" not in shown[0] and "&lt;img" in shown[0] and "a &amp; b" in shown[0]
    assert "<script>" not in shown[1] and "&lt;script&gt;" in shown[1]


def test_excel_export_is_in_memory():
    import io as _io
    import pandas as pd
    import ui
    data = ui.excel_bytes(pd.DataFrame({"x": [1, 2]}))
    assert pd.read_excel(_io.BytesIO(data)).x.tolist() == [1, 2]


def test_running_job_is_only_shown_on_its_own_page():
    jobs.JOBS.clear()
    jobs.JOBS["hotel|q"] = dict(running=True, ended=None)
    page = "flights|ATH".split("|", 1)[0] + "|"
    assert next((k for k, v in jobs.JOBS.items() if v["running"] and k.startswith(page)), None) is None
    jobs.JOBS["old"] = dict(running=False, ended=time.time() - 7200)
    jobs.start_job("new", 1, lambda j: None)
    assert "old" not in jobs.JOBS and "hotel|q" in jobs.JOBS
    jobs.JOBS.clear()


def test_every_page_loads(tmp_path, monkeypatch):
    """Catches a page that does not even compile or crashes on open (no network: nothing is fetched on load)."""
    import os
    from streamlit.testing.v1 import AppTest
    here = os.path.dirname(os.path.abspath(__file__))
    monkeypatch.chdir(tmp_path)
    for page in ("views/flights.py", "views/hotel.py", "views/health.py", "views/stats.py"):
        at = AppTest.from_file(os.path.join(here, page), default_timeout=60).run()
        assert not at.exception, (page, at.exception)


def test_hotels_fall_back_to_booking_when_google_is_rate_limited(tmp_path, monkeypatch):
    """On Streamlit Cloud Google Hotels answers HTTP 429; the page must still show prices from Booking.com (Xotelo)."""
    import os
    from datetime import date as _date
    from streamlit.testing.v1 import AppTest
    import xotelo

    def limited(*a, **k):
        raise RuntimeError("TransientBatchExecuteError: HTTP 429 from endpoint")
    monkeypatch.setattr(hotels, "fetch_day", limited)
    monkeypatch.setattr(xotelo, "heatmap_range", lambda key, s, e: {})
    monkeypatch.setattr(xotelo, "booking_rate", lambda key, d, n=1, adults=2: 100.0 + d.day)
    here = os.path.dirname(os.path.abspath(__file__))
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(os.path.join(here, "views/hotel.py"), default_timeout=60).run()
    at.text_input(key="ho-link").set_value("g187337-d202275").run()  # the field is empty by default now: this hotel's key, pasted
    first, days = _date.today() + timedelta(1), 30  # default window: the next 30 days from tomorrow
    next(b for b in at.button if b.label == "Update prices").click().run()   # by label: the position moves when buttons are added
    deadline = time.time() + 20
    while any(v["running"] for v in jobs.JOBS.values()) and time.time() < deadline:
        time.sleep(0.2)
    at.run()
    cards = " ".join(m.value for m in at.markdown)
    cheapest = min(100 + (first + timedelta(i)).day for i in range(days))
    assert f"€{cheapest}" in cards and "(Booking.com)" in cards
    assert any("HTTP 429" in m.value and "Booking.com" in m.value for m in at.success)
    assert not at.warning  # no "press Update again" advice that cannot help while Google blocks the server
    assert not at.exception
    jobs.JOBS.clear()



def test_near_cheapest_keeps_ties_and_close_days():
    import pandas as pd
    import ui
    s = pd.Series({"2026-11-02": 100.0, "2026-11-03": 104.0, "2026-11-04": 130.0, "2026-11-05": None, "2026-11-06": 100.0})
    assert list(ui.near_cheapest(s, 0).index) == ["2026-11-02", "2026-11-06"]  # two days tie for cheapest
    assert list(ui.near_cheapest(s, 0.05).index) == ["2026-11-02", "2026-11-06", "2026-11-03"]
    assert list(ui.on_weekdays(s.to_frame("p"), ["Mon"]).index) == ["2026-11-02"]


def test_airport_search_finds_cities_and_names():
    import ui
    names = ui.airports()
    assert len(names) > 3000 and {"ATH", "FRA", "BIO"} <= set(names)
    assert "MMI" not in names  # McMinn County (Athens, USA) has no scheduled flights: typing Athens must not pick it
    assert "Athens" in names["ATH"] and "Bilbao" in names["BIO"] and "Frankfurt" in names["FRA"]


def test_airport_picker_offers_only_aegean_destinations():
    """ATH -> London City found nothing after a 3-minute search: AEGEAN flies to Heathrow and, in summer, Gatwick."""
    import ui
    names = ui.aegean_airports()
    assert 100 < len(names) < 200 and {"ATH", "SKG", "LHR", "LGW", "FRA", "ZRH", "IST", "BIO", "LCA", "CHQ"} <= set(names)
    assert "LCY" not in names and "MMI" not in names
    assert names["LHR"] == "LHR · London Heathrow Airport — London, United Kingdom"
    assert names["LGW"] == "LGW · London Gatwick Airport — London, United Kingdom (seasonal)"
    assert "Athens" in names["ATH"] and "Greece" in names["ATH"] and "Bilbao" in names["BIO"]


def test_airport_lists_name_the_city_not_the_airport():
    """Chania showed as 'Souda (CHQ)': the app must say the city travellers book by, from the airline lists' city column, never the
    airport's own town or name (OurAirports' municipality for CHQ is Souda, for TIA Rinas, for BRU Zaventem)."""
    import csv
    import importlib.util
    import os
    import sys
    import trip_plan as tp
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "data"))
    build = importlib.import_module("build_aegean_airports")  # stdlib only: the words a city cell must never contain
    assert "souda" in build.AIRPORT_WORDS and build.airport_not_city("Souda") and build.airport_not_city("Athens International Airport")
    assert not build.airport_not_city("Chania") and not build.airport_not_city("Thessaloniki")
    assert "airport_not_city" in open(os.path.join(here, "data", "build_skyexpress_airports.py")).read()  # a rebuild of either list checks
    for name in ("aegean_airports.csv", "skyexpress_airports.csv"):
        with open(os.path.join(here, "data", name), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows and all(r["city"] and not build.airport_not_city(r["city"]) for r in rows), name
        assert all(not build.airport_not_city(tp.city(r["iata"])) for r in rows), name  # what the pages show for each airport
    with open(os.path.join(here, "data", "aegean_airports.csv"), encoding="utf-8") as f:
        assert all(tp.city(r["iata"]) == r["city"] or r["iata"] in tp.CITY for r in csv.DictReader(f))  # AEGEAN's spelling (Cephalonia) wins
    assert tp.city("CHQ") == "Chania" and tp.city("TIA") == "Tirana" and tp.city("BRU") == "Brussels" and tp.city("ATH") == "Athens"
    assert tp.city("FRA") == "Frankfurt" and tp.city("LCY") == "London" and tp.city("XXX") == "XXX"  # airports.csv, then the code itself



def test_start_date_plus_window(tmp_path, monkeypatch):
    """Pick a start date (e.g. 29 Nov) and 15 days: the route header shows 29 Nov to 13 Dec."""
    import os
    from datetime import date as _date
    from streamlit.testing.v1 import AppTest
    here = os.path.dirname(os.path.abspath(__file__))
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(os.path.join(here, "views/flights.py"), default_timeout=60).run()
    first = _date.today() + timedelta(31)
    at.date_input(key="fl-when").set_value((first, first + timedelta(14))).run()  # 15 days, as two dates
    sub = " ".join(m.value for m in at.markdown if 'class="sub"' in m.value)
    assert f"{first:%a %d %b}" in sub and f"{first + timedelta(14):%a %d %b %Y}" in sub and "15 days" in sub, sub
    assert not at.exception



def test_round_trips_pair_departures_with_returns():
    import pandas as pd
    import ui
    out = pd.DataFrame({"economy": [100, None, 90], "business": [300, 320, None]}, index=["2026-11-01", "2026-11-02", "2026-11-03"])
    back = pd.DataFrame({"economy": [80, 70, 60], "business": [250, None, 260]}, index=["2026-11-03", "2026-11-04", "2026-11-05"])
    t = ui.round_trips(out, back, [2])
    assert [(r.cabin, r.depart, r.back, r.total) for r in t.itertuples()] == [
        ("economy", "2026-11-03", "2026-11-05", 150), ("economy", "2026-11-01", "2026-11-03", 180),
        ("business", "2026-11-01", "2026-11-03", 550)]


def test_return_search_end_to_end_and_statistics(tmp_path, monkeypatch):
    """Offline: Google is faked. A return search fetches both directions, shows the cheapest pair, and is logged for Statistics."""
    import os
    from datetime import date as _date
    from streamlit.testing.v1 import AppTest
    here = os.path.dirname(os.path.abspath(__file__))
    monkeypatch.chdir(tmp_path)

    def grid(orig, dest, cabin, start, end, nonstop=True):  # outbound 100 € + day of month, way back 50 €; business x3
        base = (100 if orig == "ATH" else 50) * (3 if cabin == "business" else 1)
        return {(start + timedelta(i)).isoformat(): base + (start + timedelta(i)).day for i in range((end - start).days + 1)}
    monkeypatch.setattr(collector, "fetch_cabin", grid)
    at = AppTest.from_file(os.path.join(here, "views/flights.py"), default_timeout=60).run()
    at.toggle(key="fl-gq-ATHFRA").set_value(False).run()  # AEGEAN only here: the Sky Express comparison has tests of its own
    at.button_group(key="fl-trip").set_value("Return").run()
    at.slider(key="fl-nights").set_value((7, 7)).run()  # a week away, as the old default
    first = _date.today() + timedelta(1)
    at.date_input(key="fl-when").set_value((first, first + timedelta(14))).run()  # 15 days, as two dates
    next(b for b in at.button if b.label == "Update prices").click().run()   # by label: the position moves when buttons are added
    deadline = time.time() + 20
    while any(v["running"] for v in jobs.JOBS.values()) and time.time() < deadline:
        time.sleep(0.2)
    at.run()
    first = _date.today() + timedelta(1)
    days = [first + timedelta(i) for i in range(15)]
    want = min((100 + d.day) + (50 + (d + timedelta(7)).day) for d in days)
    cards = " ".join(m.value for m in at.markdown)
    assert "CHEAPEST RETURN ECONOMY" in cards.upper() and f"€{want}" in cards and "7 nights" in cards, cards[:400]
    assert not at.exception
    st_page = AppTest.from_file(os.path.join(here, "views/stats.py"), default_timeout=60).run()
    text = " ".join(m.value for m in st_page.markdown)
    assert not st_page.exception and "Updates" in text and "Flights 1" in text
    jobs.JOBS.clear()


def test_daily_collector_retries_until_complete_and_app_loads_it(tmp_path):
    import collect_daily as cd
    calls = []

    def fake(start, end, o, t, skip, nonstop):  # pass 1 fails every Business day on ATH-FRA; pass 2 works
        calls.append((o, t, len(skip)))
        for i in range((end - start).days + 1):
            d = start + timedelta(i)
            for c in ("economy", "business"):
                if (d.isoformat(), c) in skip:
                    continue
                if (o, t, c) == ("ATH", "FRA", "business") and len(calls) == 1:
                    yield d, c, None, "", "error", "HTTPError: 429"
                else:
                    yield d, c, None if c == "business" and o == "ZRH" else 99.0, "calendar", "ok", None

    got, missing = cd.collect(fake, today=date.today(), pause=0)
    assert not missing and len(got) == 4 * 2 * 90
    assert calls[4] == ("ATH", "FRA", 90) and len(calls) == 5  # pass 2 only re-asks the failed leg, skipping the 90 good days
    out = tmp_path / "daily.csv"
    cd.save(got, out)
    t = db.connect(str(tmp_path / "t.db"))
    db.load_daily(t, str(out))
    db.load_daily(t, str(out))  # loading the same file twice adds nothing
    n, priced = t.execute("SELECT COUNT(*), COUNT(price) FROM prices").fetchone()
    assert n == 720 and priced == 720 - 90


def test_daily_hotel_retries_until_complete_and_app_loads_it(tmp_path, monkeypatch):
    import collect_daily as cd
    monkeypatch.setattr(par.time, "sleep", lambda s: None)
    tries = {}

    def google(start, end, q, f, nights, adults, skip):
        for i in range((end - start).days + 1):
            d = start + timedelta(i)
            if d.isoformat() not in skip:
                yield d, "Steigenberger Frankfurter Hof", 250.0, "ok", None

    def rate(key, d, nights, adults=2):  # Booking.com fails once for every day, then answers
        tries[d] = tries.get(d, 0) + 1
        if tries[d] == 1:
            raise RuntimeError("timeout")
        return 240.0

    heat = lambda key, s, e: {(s + timedelta(i)).isoformat(): "cheap" for i in range(3)}
    got, missing = cd.collect_hotel(google, rate, heat, pause=0)
    assert missing == 0 and sum(src == "booking" for src, _ in got) == 90 and sum(src == "heat" for src, _ in got) == 3
    out = tmp_path / "h.csv"
    cd.save_hotel(got, out)
    t = db.connect(str(tmp_path / "t.db"))
    db.load_daily_hotel(t, str(out))
    db.load_daily_hotel(t, str(out))  # loading the same file twice adds nothing
    assert [t.execute(f"SELECT COUNT(*) FROM {x}").fetchone()[0] for x in ("hotel", "booking", "heat")] == [90, 90, 3]
    assert db.saved_hotel_days(t, "Steigenberger Frankfurt", "Frankfurter Hof", 1, 2, date.today() + timedelta(1),
                               date.today() + timedelta(90)) == {(date.today() + timedelta(i)).isoformat() for i in range(1, 91)}


def test_load_daily_award(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("day,orig,dest,cabin,miles,nonstop,fetched\n"
                 "2026-11-11,ATH,FRA,economy,9032,1,2026-10-29T10:00:00\n"
                 "2026-11-11,ATH,FRA,business,,1,2026-10-29T10:00:00\n")  # empty miles: checked, no award seats
    t = db.connect(str(tmp_path / "t.db"))
    t.execute("DELETE FROM award_miles")  # connect() already loaded the repo's real data/daily_award.csv
    db.load_daily_award(t, str(p))
    db.load_daily_award(t, str(p))  # loading the same file twice adds nothing
    assert t.execute("SELECT COUNT(*), COUNT(miles), MAX(miles) FROM award_miles").fetchone() == (2, 1, 9032)


def test_award_miles_survive_old_fetch_stamp_but_not_past_days(tmp_path, monkeypatch):
    from datetime import date
    old = "2020-01-01T00:00:00"  # far older than RETENTION_DAYS: a skipped daily run must not erase the data
    future, past = (date.today() + timedelta(10)).isoformat(), (date.today() - timedelta(1)).isoformat()
    p = tmp_path / "a.csv"
    p.write_text("day,orig,dest,cabin,miles,nonstop,fetched\n"
                 f"{future},ATH,FRA,economy,9032.0,1.0,{old}\n"  # pandas/Excel style numbers
                 f"{past},ATH,FRA,economy,9032,1,{old}\n"
                 f"{future},ATH,FRA,business,,x,{old}\n")  # malformed row is skipped, not fatal
    monkeypatch.setattr(db, "DAILY_AWARD", str(p))
    t = db.connect(str(tmp_path / "t.db"))
    assert t.execute("SELECT day, miles, nonstop FROM award_miles").fetchall() == [(future, 9032, 1)]


def test_flights_page_keeps_value_metric_typed_only(tmp_path, monkeypatch):
    import os
    from datetime import datetime
    from streamlit.testing.v1 import AppTest
    import test_overview
    page = os.path.abspath("views/flights.py")
    t = test_overview.seed(tmp_path / "tracker.db")  # its own fares: the page must not depend on whatever data the checkout holds
    t.executemany("INSERT INTO award_miles(day, orig, dest, cabin, miles, nonstop, fetched) VALUES(?,?,?,?,?,?,?)",
                  [((date.today() + timedelta(10)).isoformat(), "ATH", "FRA", c, m, 1, datetime.now().isoformat())
                   for c, m in (("economy", 9032), ("business", 18064))])
    t.commit()
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception
    shown = " ".join(m.value for m in at.markdown)
    assert "Cheapest economy" in shown and "Lowest economy award" in shown  # award seats saved: shown beside the cash fares, whatever Pay with says
    at.button_group(key="fl-pay").set_value("miles").run()
    assert not at.exception
    shown = " ".join(m.value for m in at.markdown)
    assert "Best miles value" not in shown  # collected miles carry no taxes: they must not feed the value metric (only typed ones do)
    assert "Lowest economy award" in shown and "Lowest business award" in shown  # the miles sit in the headline, not in a hidden tab


def test_flights_page_shows_miles_only_when_asked_and_keeps_the_filters_under_options(tmp_path, monkeypatch):
    """The award column and chips are for Miles+Bonus members: Pay with (Cash € by default, like the Overview) shows them on request. The
    cheap-tolerance and weekday pills are under Options, 10% by default; the headline cards stay."""
    import os
    from streamlit.testing.v1 import AppTest
    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "views", "flights.py")
    _seed_fares(tmp_path, monkeypatch)
    at = AppTest.from_file(page, default_timeout=90).run()
    assert not at.exception and at.button_group(key="fl-pay").value == "cash"
    shown = " ".join(m.value for m in at.markdown)
    assert "Cheapest economy" in shown and "Economy is cheapest on" in shown and "Award miles with Miles+Bonus" not in shown
    assert at.button_group(key="fl-tol").value == 10 and len(at.button_group(key="fl-days").value) == 7  # under Options, the old defaults
    assert "within 10% of this price" in shown  # the headline card says in words how close to the cheapest still counts
    at.button_group(key="fl-pay").set_value("miles").run()
    assert not at.exception
    shown = " ".join(m.value for m in at.markdown)
    assert "Award miles with Miles+Bonus" in shown and "Not collected yet for this route" in shown and "Cheapest economy" in shown
    assert "collected on the owner's computer; taxes not included" in " ".join(c.value for c in at.caption)  # the award section explains itself


def test_flights_page_survives_missing_award_table(tmp_path, monkeypatch):
    """Streamlit Cloud once ran the new page against an old, already loaded db.py with no award_miles table and crashed."""
    import os
    from streamlit.testing.v1 import AppTest
    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "views", "flights.py")
    real = db.connect

    def stale(*a, **k):
        c = real(*a, **k)
        c.execute("DROP TABLE award_miles")
        return c
    monkeypatch.setattr(db, "connect", stale)
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception


def test_award_view_headline_and_table():
    import pandas as pd
    import award_view as av
    s = pd.Series({"2026-11-07": 9032.0, "2026-11-08": float("nan"), "2026-11-09": 9032.0})
    assert av.best_award(s, True) == ("9,032 miles", "the same on each of 2 days with seats, from Sat 07 Nov")
    mixed = pd.Series({"2026-11-07": 9032.0, "2026-11-08": 15000.0, "2026-11-09": 9032.0})
    assert av.best_award(mixed, True) == ("9,032 miles", "Sat 07 Nov \u00b7 2 days at this level \u00b7 3 days with seats")
    assert av.best_award(pd.Series({"2026-11-07": float("nan")}), True)[1] == "No award seats on these days"
    assert av.best_award(pd.Series(dtype=float), False)[1] == "Not collected yet for this route"  # no data is not the same as no seats
    t = av.miles_table(s, pd.Series({"2026-11-07": 16935.0, "2026-11-08": float("nan"), "2026-11-09": float("nan")}))
    assert t.to_dict("list") == {"Date": ["Sat 07 Nov", "Mon 09 Nov"], "Economy miles": ["9,032", "9,032"], "Business miles": ["16,935", "\u2014"]}


def test_booking_strategy_one_way_and_return():
    import pandas as pd
    import award_view as av
    view = pd.DataFrame({"economy": [120.0, 86.0, float("nan")], "econ_miles": [float("nan"), 9032.0, 9032.0]},
                        index=["2026-11-07", "2026-11-09", "2026-11-14"])
    ow = av.one_way_plan(view, "economy", "econ_miles")
    assert ow["cash"] == ("2026-11-09", 86.0) and ow["miles"] == ("2026-11-09", 9032.0, 86.0, None) and ow["best"][:4] == ("2026-11-09", 9032.0, 86.0, None)
    # return: miles pair is outbound 2026-11-07 + 7 nights = 2026-11-14 only when both days have miles
    out_m = pd.Series({"2026-11-07": 9032.0, "2026-11-09": 9032.0})
    back_m = pd.Series({"2026-11-16": 9032.0, "2026-11-21": 9032.0})
    assert av.best_return_miles(out_m, back_m, [7, 5]) == (18064.0, "2026-11-09", "2026-11-16", 7, None)
    assert av.best_return_miles(out_m, back_m, [3]) is None
    trips = pd.DataFrame([("economy", "2026-11-09", "2026-11-16", 7, 86.0, 90.0, 176.0)],
                         columns=["cabin", "depart", "back", "nights", "outbound", "return", "total"])
    rp = av.return_plan(trips, out_m, back_m, [7])
    assert rp["cash"] == ("2026-11-09", "2026-11-16", 7, 176.0) and rp["miles"] == ("2026-11-09", "2026-11-16", 7, 18064.0, 176.0, None)


def test_history_append_changes_only(tmp_path):
    import csv
    import history
    p = tmp_path / "h.csv"
    row = lambda day, price, fetched: dict(day=day, price=price, fetched=fetched, source="x")
    assert history.append_changes(p, [row("d1", 100, "t1"), row("d2", 50, "t1")], ("day",), ("price",)) == 2
    assert history.append_changes(p, [row("d1", 100, "t2"), row("d2", 55, "t2"), row("d3", "", "t2")], ("day",), ("price",)) == 2  # d2 changed, d3 is new
    assert history.append_changes(p, [row("d3", None, "t3")], ("day",), ("price",)) == 0  # None and "" are the same: no price
    rows = list(csv.DictReader(open(p)))
    assert [(r["day"], r["price"]) for r in rows] == [("d1", "100"), ("d2", "50"), ("d2", "55"), ("d3", "")]


def test_database_page_renders_and_keeps_only_changes():
    import os
    import pandas as pd
    from streamlit.testing.v1 import AppTest
    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "views", "database.py")
    at = AppTest.from_file(page, default_timeout=90).run()
    assert not at.exception and [t.label for t in at.tabs] == ["Cash fares", "Sky Express fares", "Award miles", "Hotels"]
    src = open(page, encoding="utf-8").read()
    ns = {}
    exec(src[src.index("def changes_only"):src.index("def overview")], {"pd": pd}, ns)  # the pure helper, without running the page
    df = pd.DataFrame({"day": ["d"] * 4, "fetched": ["t1", "t2", "t3", "t4"], "price": [100.0, 100.0, 90.0, 90.0]})
    assert list(ns["changes_only"](df, ["day"], "price").fetched) == ["t1", "t3"]
    ns["pd"] = pd
    exec(src[src.index("def with_before"):src.index("def overview")], ns)
    m = pd.DataFrame({"day": ["d1", "d2", "d1", "d1"], "fetched": ["t1", "t1", "t2", "t3"], "miles": [9032.0, float("nan"), float("nan"), 9032.0]})
    got = ns["with_before"](m, ["day"], "miles", "No seats")
    assert list(zip(got.miles, got.before)) == [("9,032", ""), ("No seats", ""), ("No seats", "9,032"), ("9,032", "No seats")]


def test_award_coverage_separates_updated_older_and_missing_days(tmp_path):
    import freshness
    (tmp_path / "daily_award.csv").write_text("day,orig,dest,nonstop,fetched\n"
                                              "2026-11-02,ATH,FRA,1,2026-11-01T07:00:00\n2026-11-03,ATH,FRA,1,2026-10-20T07:00:00\n"
                                              "2026-11-04,ATH,FRA,1,2026-11-01T07:00:00\n2026-11-02,ATH,ZRH,0,2026-11-01T07:00:00\n"
                                              "2026-11-09,ATH,FRA,1,2026-11-01T07:00:00\n")          # past the 5 days asked about
    assert freshness.award_coverage(str(tmp_path), date(2026, 11, 1), 5) == [("ATH \u2192 FRA", "2026-11-01", 2, 1, 2, "2026-11-02")]
    assert freshness.award_coverage(str(tmp_path / "none"), date(2026, 11, 1)) == []


def test_export_whole_database(tmp_path):
    import io
    import zipfile
    import pandas as pd
    import history
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("day,price,fetched\nd1,100,2026-10-29T07:00:00\nd1,90,2026-10-30T07:00:00\n")
    xl = pd.read_excel(io.BytesIO(history.export_excel({"Cash fares": str(a), "Hotels": str(b)})), sheet_name=None)  # b does not exist yet
    assert list(xl) == ["Cash fares", "Hotels"]
    assert list(xl["Cash fares"].price) == [90, 100]  # all rows, newest recording first
    assert list(xl["Hotels"].columns) == ["info"]
    z = zipfile.ZipFile(io.BytesIO(history.export_zip([str(a), str(b)])))
    assert z.namelist() == ["a.csv"] and b"d1,90" in z.read("a.csv")  # the missing file is skipped


def test_history_gains_a_column_without_losing_rows(tmp_path):
    import csv
    import history
    p = tmp_path / "h.csv"
    p.write_text("day,miles,fetched\nd1,9032,t1\n")  # a file made before the taxes column existed
    row = lambda day, miles, taxes, fetched: dict(day=day, miles=miles, taxes=taxes, fetched=fetched)
    assert history.append_changes(p, [row("d1", 9032, 30.44, "t2")], ("day",), ("miles", "taxes")) == 1  # same miles, but taxes are new
    assert history.append_changes(p, [row("d1", 9032, 30.44, "t3")], ("day",), ("miles", "taxes")) == 0
    rows = list(csv.DictReader(open(p)))
    assert rows[0] == {"day": "d1", "miles": "9032", "fetched": "t1", "taxes": ""} and rows[1]["taxes"] == "30.44"


def test_strategy_uses_award_taxes_when_known():
    import pandas as pd
    import award_view as av
    view = pd.DataFrame({"economy": [168.0, 86.0], "econ_miles": [9032.0, float("nan")], "econ_taxes": [30.44, float("nan")]},
                        index=["2026-11-07", "2026-11-18"])
    ow = av.one_way_plan(view, "economy", "econ_miles", "econ_taxes")
    assert ow["miles"] == ("2026-11-07", 9032.0, 168.0, 30.44)
    # return: taxes of both legs add up, and only when both are known
    out_m, back_m = pd.Series({"2026-11-09": 9032.0}), pd.Series({"2026-11-16": 9032.0})
    best = av.best_return_miles(out_m, back_m, [7], pd.Series({"2026-11-09": 30.44}), pd.Series({"2026-11-16": 25.0}))
    assert best == (18064.0, "2026-11-09", "2026-11-16", 7, 55.44)
    assert av.best_return_miles(out_m, back_m, [7], pd.Series({"2026-11-09": 30.44}), None)[4] is None


def test_load_award_survives_a_database_without_the_taxes_column(tmp_path):
    import sqlite3
    from datetime import date
    import award_view as av
    db = sqlite3.connect(str(tmp_path / "old.db"))  # a database from before taxes were stored
    db.execute("CREATE TABLE award_miles(day TEXT, orig TEXT, dest TEXT, cabin TEXT, miles INT, nonstop INT, fetched TEXT)")
    db.execute("INSERT INTO award_miles VALUES('2026-11-07','ATH','FRA','economy',9032,1,'t1')")
    db.commit()  # pandas rolls the connection back when the first query fails, which would discard an uncommitted row
    m, t, last = av.load_award(db, "ATH", "FRA", True, date(2026, 11, 1), date(2026, 11, 30))
    assert m.loc["2026-11-07", "economy"] == 9032 and t.isna().all().all() and last == "t1"
    empty = sqlite3.connect(":memory:")  # no award table at all
    assert av.load_award(empty, "ATH", "FRA", True, date(2026, 11, 1), date(2026, 11, 30))[2] is None


def test_freshness_levels_and_health_page(tmp_path):
    from datetime import date
    import freshness
    (tmp_path / "daily.csv").write_text("day,fetched\nx,2026-10-27T07:00:00\ny,2026-10-29T07:00:00\n")
    r = {x[0]: x for x in freshness.freshness(str(tmp_path), date(2026, 10, 30))}
    assert r["Cash fares"][2] == 1 and r["Cash fares"][3] == "ok"
    assert r["Award miles"][1] is None and r["Award miles"][3] == "stale"   # never collected: stale, with how it is collected
    assert freshness.freshness(str(tmp_path), date(2026, 11, 3))[0][3] == "late" and freshness.freshness(str(tmp_path), date(2026, 12, 1))[0][3] == "stale"
    assert freshness.SOURCES[2][2] == 35  # award miles are collected once a month
    import os
    from streamlit.testing.v1 import AppTest
    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "views", "health.py")
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception
    assert "Data freshness" in " ".join(m.value for m in at.markdown) and len(at.success) + len(at.warning) + len(at.error) == 3


def test_best_day_for_miles_is_where_cash_is_dearest_not_the_first_cheap_miles_day():
    import pandas as pd
    import award_view as av
    # the same 9,032 miles on three days; cash is 90, 300 and 150: the miles replace the most money on the 300 day
    view = pd.DataFrame({"economy": [90.0, 300.0, 150.0], "econ_miles": [9032.0, 9032.0, 9032.0]}, index=["2026-11-07", "2026-11-14", "2026-11-21"])
    ow = av.one_way_plan(view, "economy", "econ_miles")
    assert ow["miles"][0] == "2026-11-07"                      # the first of the equal cheapest-miles days (what 'lowest award' shows)
    assert ow["best"][:3] == ("2026-11-14", 9032.0, 300.0) and round(ow["best"][4], 1) == 33.2   # but the best day to spend them
    # taxes come off the saving: (300 - 30.44) / 9,032 * 1000
    view["econ_taxes"] = 30.44
    assert round(av.one_way_plan(view, "economy", "econ_miles", "econ_taxes")["best"][4], 1) == 29.8
    # return: two outbound days with the same miles; the pair with the dearer cash total is the better use of miles
    import pandas as pd
    out_m, back_m = pd.Series({"2026-11-07": 9032.0, "2026-11-14": 9032.0}), pd.Series({"2026-11-14": 9032.0, "2026-11-21": 9032.0})
    trips = pd.DataFrame([("economy", "2026-11-07", "2026-11-14", 7, 50.0, 60.0, 110.0), ("economy", "2026-11-14", "2026-11-21", 7, 200.0, 250.0, 450.0)],
                         columns=["cabin", "depart", "back", "nights", "outbound", "return", "total"])
    rp = av.return_plan(trips, out_m, back_m, [7])
    assert rp["cash"][3] == 110.0 and rp["best"][:2] == ("2026-11-14", "2026-11-21") and round(rp["best"][6], 1) == 24.9   # 450 / 18,064 * 1000


def test_trip_cost_adds_flights_and_every_hotel_night(tmp_path):
    import sqlite3
    from datetime import date
    import pandas as pd
    import db as appdb
    import trip_cost as tc
    t = appdb.connect(str(tmp_path / "t.db"))
    t.execute("DELETE FROM hotel")
    night = lambda day, price, fetched="t1", nights=1: t.execute(
        "INSERT INTO hotel(day, query, filt, nights, hotel, price, fetched, adults) VALUES(?, 'q', 'f', ?, 'H', ?, ?, 2)", (day, nights, price, fetched))
    for d, p in (("2026-11-07", 100.0), ("2026-11-08", 120.0), ("2026-11-09", 90.0)):
        night(d, p)
    night("2026-11-08", 110.0, "t2")            # a newer price for the same night wins
    night("2026-11-07", 999.0, nights=3)        # a 3-night price is not a per-night price: ignored
    t.commit()
    assert tc.hotel_options(t) == [dict(query="q", filt="f", adults=2, days=3)]
    np_ = tc.night_prices(t, "q", "f", 2, date(2026, 11, 1), date(2026, 11, 30))
    assert list(np_) == [100.0, 110.0, 90.0]
    trips = pd.DataFrame([("economy", "2026-11-07", "2026-11-10", 3, 80.0, 90.0, 170.0),   # nights 7, 8, 9: all priced
                          ("economy", "2026-11-08", "2026-11-11", 3, 50.0, 50.0, 100.0),   # night 10 has no price: skipped, not guessed
                          ("economy", "2026-11-07", "2026-11-09", 2, 60.0, 60.0, 120.0)],  # nights 7, 8
                         columns=["cabin", "depart", "back", "nights", "outbound", "return", "total"])
    r = tc.trip_costs(trips, np_)
    assert list(r.total) == [330.0, 470.0] and list(r.nights) == [2, 3]   # 120 + 210, then 170 + 300 (cheapest first)
    assert r.iloc[0].to_dict() == dict(depart="2026-11-07", back="2026-11-09", nights=2, flights=120.0, hotel=210.0, total=330.0)


def test_digest_lists_changes_and_ignores_first_sightings(tmp_path):
    import digest
    p = tmp_path / "history_prices.csv"
    p.write_text("day,orig,dest,cabin,price,source,fetched,nonstop\n"
                 "2026-11-18,ATH,FRA,economy,86,calendar,2026-10-29T07:00:00,1\n"
                 "2026-11-19,ATH,FRA,economy,100,calendar,2026-10-29T07:00:00,1\n"
                 "2026-11-18,ATH,FRA,economy,79,calendar,2026-10-30T07:00:00,1\n"     # dropped 86 -> 79
                 "2026-11-19,ATH,FRA,economy,120,calendar,2026-10-30T07:00:00,1\n"    # rose 100 -> 120
                 "2027-01-30,ATH,FRA,economy,90,calendar,2026-10-30T07:00:00,1\n")    # first seen: not a change
    a = tmp_path / "award_history.csv"
    a.write_text("day,orig,dest,cabin,miles,nonstop,fetched\n"
                 "2026-11-20,ATH,FRA,business,,1,2026-10-29T07:00:00\n"
                 "2026-11-20,ATH,FRA,business,16935,1,2026-10-30T07:00:00\n"            # a seat appeared
                 "2026-11-21,ATH,FRA,economy,9032,1,2026-10-29T07:00:00\n"
                 "2026-11-21,ATH,FRA,economy,,1,2026-10-30T07:00:00\n"                  # the seats went
                 "2026-11-22,ATH,FRA,economy,9032,1,2026-10-29T07:00:00\n"
                 "2026-11-22,ATH,FRA,economy,9032,1,2026-10-30T07:00:00\n")             # same miles, only the taxes changed: not news
    md = digest.build(str(tmp_path))
    assert "Sat 21 Nov, economy: was 9,032 miles" in md and "Sun 22 Nov" not in md
    assert md.startswith("# What changed on 30 Oct 2026")
    assert "Wed 18 Nov, economy: \u20ac86 \u2192 \u20ac79 (\u2212\u20ac7)" in md and "\u20ac100 \u2192 \u20ac120 (+\u20ac20)" in md
    assert "Fri 20 Nov, business: 16,935 miles (was none)" in md and "2027" not in md and "Jan" not in md
    assert digest.build(str(tmp_path / "nothing")) == ""   # no files: nothing to say, and no error


def test_aegean_pass_compared_with_cash():
    from datetime import date
    import pandas as pd
    import pass_plan as pp
    today = date(2026, 11, 1)
    assert pp.PASSES == (("7-day pass", 780.0, 7), ("30-day pass", 672.0, 30))   # the defaults: what the two passes cost
    assert pp.effective_cost(780.0, 12, 30.0) == 95.0 and pp.effective_cost(672.0, 12, 30.0) == 86.0   # price / flights + taxes
    # fares by day; the first two fall inside the 7-day booking window and must not count
    cash = pd.Series({"2026-11-03": 40.0, "2026-11-07": 45.0, "2026-11-10": 90.0, "2026-11-12": 120.0, "2026-11-20": 200.0, "2026-11-25": float("nan")})
    a = pp.assess(cash, 1200.0, 12, 30.0, 7, today)       # 100 + 30 = 130 per flight
    assert a["days"] == 3 and a["cheapest"] == 90.0 and a["typical"] == 120.0 and a["pass_cheaper"] == 1
    assert a["break_even"] is None   # it would need 14 flights, the pass has 12
    cheap = pp.assess(cash, 600.0, 12, 30.0, 7, today)    # 50 + 30 = 80, below the cheapest fare of 90
    assert cheap["break_even"] == 7
    far = pd.Series({"2026-11-20": 200.0, "2026-12-05": 150.0, "2026-12-10": 90.0})
    assert pp.assess(far, 672.0, 12, 30.0, 30, today)["days"] == 2   # the 30-day pass only counts fares at least 30 days ahead
    assert pp.assess(cash, 600.0, 12, 30.0, 90, today) is None


def test_availability_calendar_states_and_layout():
    from datetime import date
    import pandas as pd
    import award_calendar as ac
    nan = float("nan")
    cash = pd.Series({"2026-11-02": 90.0, "2026-11-03": 300.0, "2026-11-04": 120.0, "2026-11-05": 250.0, "2026-11-06": 60.0})
    miles = pd.Series({"2026-11-02": 9032.0, "2026-11-03": 9032.0, "2026-11-04": nan, "2026-11-05": 9032.0, "2026-11-06": nan})
    checked = {"2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"}      # the 7th and 8th were never checked
    cs = ac.cells(cash, miles, checked, date(2026, 11, 2), date(2026, 11, 8))
    # seat-day cash fares are 90, 300 and 250: the top quarter starts at 275, so only the 300 day is "best" and 250 is a plain seat day
    assert [c["state"] for c in cs] == ["seat", "best", "none", "seat", "none", "unk", "unk"]
    assert cs[1]["cash"] == 300.0 and cs[2]["miles"] is None and cs[5]["cash"] is None            # no fare saved for the unchecked days
    html = ac.calendar_html(cs)
    # a range that crosses a month gets one grid per month, and the first day of a month starts under its weekday
    cross = ac.cells(cash, miles, checked, date(2026, 10, 30), date(2026, 11, 3))
    h2 = ac.calendar_html(cross)
    assert "October 2026" in h2 and "November 2026" in h2 and h2.count('class="cal-g"') == 2
    assert ac.calendar_html([]) == ""


def test_advice_names_one_option_for_flexible_and_for_fixed_dates():
    import advice as ad
    cash = dict(min=86.0, typical=109.0, when="Wed 18 Nov")
    miles = dict(miles=9032.0, taxes=30.0, when="Sat 07 Nov")
    passes = [dict(name="7-day pass", eff=95.0, days=7, price=780.0, flights=12), dict(name="30-day pass", eff=86.0, days=30, price=672.0, flights=12)]
    opts = ad.options(cash, miles, passes, 10.0, 1)
    assert {o["name"]: round(o["flexible"]) for o in opts} == {"Pay cash": 86, "Use miles": 120, "7-day pass": 95, "30-day pass": 86}   # miles: 9.032 * 10 + 30
    assert ad.winner(opts, "flexible")["name"] == "Pay cash"      # the 30-day pass only ties with cash: no clear margin, so no commitment
    assert ad.winner(opts, "fixed")["name"] == "30-day pass"      # against a typical 109 fare it is clearly cheaper (86 < 98.1)
    html = ad.card_html("Economy", "#2563C9", opts, "flight")
    assert "If your dates are flexible" in html and "Pay cash \u00b7 \u20ac86" in html and "30-day pass \u00b7 \u20ac86" in html
    assert "about \u20ac23 less than a typical fare" in html and "Cost per flight" in html
    near = [dict(name="30-day pass", eff=80.0, days=30, price=672.0, flights=12)]       # cheaper than 86, but not by 10%
    html = ad.card_html("Economy", "#2563C9", ad.options(cash, None, near, 10.0, 1), "flight")
    assert "Pay cash \u00b7 \u20ac86" in html and "30-day pass is about \u20ac6 cheaper" in html and "paid upfront" in html
    assert ad.winner(ad.options(cash, miles, passes, 4.0, 1), "flexible")["name"] == "Use miles"          # 36 + 30 = 66 < 86
    assert round(ad.options(None, None, passes, 10.0, 2)[1]["flexible"]) == 172                           # a round trip is two pass flights
    business = dict(min=239.0, typical=320.0, when="Thu 12 Nov")
    assert "taxes not collected yet" in ad.card_html("Business", "#B8741A", ad.options(business, dict(miles=16935.0, taxes=None, when="x"), [], 10.0, 1), "flight")   # miles win: 169 < 239
    assert "No fares or award seats" in ad.card_html("Business", "#B8741A", [], "flight")


def _seed_fares(tmp_path, monkeypatch):
    """Cash fares for both directions of ATH-FRA, for the next 40 days, in the page's own database (the autouse fixture blocks the repo's CSVs)."""
    import csv
    from datetime import datetime
    daily = tmp_path / "daily.csv"
    now = datetime.now().isoformat(timespec="seconds")
    with open(daily, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "orig", "dest", "cabin", "price", "source", "fetched", "nonstop"])
        for i in range(1, 41):
            d = (date.today() + timedelta(i)).isoformat()
            for o, t in (("ATH", "FRA"), ("FRA", "ATH")):
                w.writerows([[d, o, t, "economy", 100 + (i % 5) * 40, "calendar", now, 1], [d, o, t, "business", 300, "calendar", now, 1]])
    monkeypatch.setattr(db, "DAILY", str(daily))
    monkeypatch.setattr(db, "DAILY_AWARD", str(tmp_path / "none.csv"))
    monkeypatch.chdir(tmp_path)


def test_what_should_i_book_shows_only_the_chosen_trip_type_and_leaves_out_impossible_passes(tmp_path, monkeypatch):
    import os
    from streamlit.testing.v1 import AppTest
    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "views", "flights.py")
    _seed_fares(tmp_path, monkeypatch)
    at = AppTest.from_file(page, default_timeout=90).run()
    assert not at.exception
    shown = " ".join(m.value for m in at.markdown)
    assert "What should I book?" in shown and "Economy</div>" in shown and "Business</div>" in shown
    assert "Cost per flight" in shown and "Cost per round trip" not in shown          # one way: one-way costs only, never a round trip beside it
    assert "7-day pass" in shown                                                       # 24 of the 30 dates shown can be booked 7+ days ahead
    assert "The 30-day pass is left out: only 1 of the 30 dates shown can be booked 30 or more days ahead" in shown   # not recommended for dates it cannot be booked for
    trip = next(p for p in at.get("button_group") if p.key == "fl-trip")
    trip.set_value("Return").run()
    assert not at.exception
    shown = " ".join(m.value for m in at.markdown)
    assert "Cost per round trip" in shown and "Cost per flight" not in shown          # return: round trips only, never a one-way block with other dates
    assert "Aegean Pass is an Economy Flex fare" in shown


def test_swap_button_exchanges_from_and_to(tmp_path, monkeypatch):
    import os
    from streamlit.testing.v1 import AppTest
    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "views", "flights.py")
    _seed_fares(tmp_path, monkeypatch)
    at = AppTest.from_file(page, default_timeout=90).run()
    assert at.selectbox(key="orig").value == "ATH" and at.selectbox(key="dest").value == "FRA"
    at.button(key="swap-airports").click().run()
    assert not at.exception
    assert at.selectbox(key="orig").value == "FRA" and at.selectbox(key="dest").value == "ATH"      # both boxes swapped in one click
    assert "FRA</span>" in " ".join(m.value for m in at.markdown)                                      # and the page is for the new direction
    at.button(key="swap-airports").click().run()
    assert at.selectbox(key="orig").value == "ATH" and at.selectbox(key="dest").value == "FRA"      # a second click swaps back


def test_insights_say_which_weekday_is_cheapest_and_whether_late_booking_costs_more(tmp_path):
    from datetime import date, timedelta
    import pandas as pd
    import db as appdb
    import insights as ins
    today = date(2026, 11, 1)                                     # a Sunday
    # six weeks of fares: Thursdays cheap (100), Sundays dear (200), everything else 150; the first two weeks 3x dearer
    cash = {}
    for i in range(1, 43):
        d = today + timedelta(i)
        base = {3: 100, 6: 200}.get(d.weekday(), 150)
        cash[d.isoformat()] = base * (3 if i <= 14 else 1)
    cash = pd.Series(cash)
    st = ins.weekday_stats(cash)
    assert list(st.index) == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] and st.loc["Thu", "days"] == 6
    assert "Thursday is the cheapest day to fly ATH \u2192 FRA" in ins.weekday_takeaway(st, "ATH \u2192 FRA") and "than Sunday" in ins.weekday_takeaway(st, "x")
    flat = pd.Series({(today + timedelta(i)).isoformat(): 120.0 + (i % 2) for i in range(1, 30)})
    assert "No strong weekday pattern" in ins.weekday_takeaway(ins.weekday_stats(flat), "x")
    ah = ins.ahead_stats(cash, today)
    assert list(ah.index)[:2] == [1, 2] and ah.loc[1, "days"] == 7
    sentence = ins.ahead_takeaway(ah, "ATH \u2192 FRA")
    assert "next 2 weeks" in sentence and "book earlier" in sentence and "3.0\u00d7" in sentence
    assert "No big last-minute premium" in ins.ahead_takeaway(ins.ahead_stats(flat, today), "x")
    assert ins.weekday_stats(pd.Series(dtype=float)).empty and "Not enough" in ins.weekday_takeaway(ins.weekday_stats(pd.Series(dtype=float)), "x")
    assert len(ins.weekday_chart(st, "#2563C9").data) == 1 and len(ins.ahead_chart(ah, "#2563C9").data) == 1
    # destinations from one airport, and the fare history of one day
    d = appdb.connect(str(tmp_path / "t.db"))
    d.execute("DELETE FROM prices")
    for dest, price in (("FRA", 100.0), ("FRA", 140.0), ("ZRH", 200.0), ("ZRH", 220.0)):
        d.execute("INSERT INTO prices(day, orig, dest, cabin, price, airlines, fetched, nonstop) VALUES(?, 'ATH', ?, 'economy', ?, 'x', 't1', 1)",
                  (f"2026-11-{10 + (int(price) % 7):02d}", dest, price))
    d.commit()
    g = ins.destinations(d, "ATH", "economy", True, date(2026, 11, 1), date(2026, 11, 30))
    assert list(g.dest) == ["FRA", "ZRH"] and ins.destination_takeaway(g, "ATH").startswith("From ATH, a typical fare to FRA is")
    assert ins.routes(d, True)[0][0] == "ATH" and len(ins.destination_chart(g).data) == 2
    hist = pd.DataFrame([dict(day="2026-11-18", orig="ATH", dest="FRA", cabin="economy", price=86.0, fetched="2026-10-29T07:00:00"),
                         dict(day="2026-11-18", orig="ATH", dest="FRA", cabin="economy", price=79.0, fetched="2026-10-30T07:00:00"),
                         dict(day="2026-11-19", orig="ATH", dest="FRA", cabin="economy", price=86.0, fetched="2026-10-29T07:00:00")])
    mv = ins.moves(hist, "ATH", "FRA", "economy")
    assert list(mv) == ["2026-11-18"]                                                  # only the day whose fare was recorded twice moved
    assert ins.history_sentence(mv["2026-11-18"], "Wed 18 Nov") == "The fare for Wed 18 Nov went down: \u20ac86 on 2026-10-29 \u2192 \u20ac79 on 2026-10-30 (\u2212\u20ac7)."
    assert ins.moves(pd.DataFrame(), "ATH", "FRA", "economy") == {} and len(ins.history_chart(mv["2026-11-18"], "#2563C9").data) == 1


def test_advice_names_a_shortlist_of_days_not_one_arbitrary_day():
    import pandas as pd
    import best_days as bd
    nan = float("nan")
    cash = pd.Series({"2026-11-03": 120.0, "2026-11-04": 86.0, "2026-11-05": 90.0, "2026-11-06": 86.0, "2026-11-07": 200.0, "2026-11-08": 88.0, "2026-11-09": 86.0})
    # three days tie at 86 and two more are within 10%: the shortlist names three and counts the rest, earliest first among equals
    assert bd.cheapest_days(cash, 0.10) == "Wed 04 Nov, Fri 06 Nov, Mon 09 Nov and 2 more within 10%"
    assert bd.cheapest_days(cash, 0.0) == "Wed 04 Nov, Fri 06 Nov, Mon 09 Nov"             # exact: only the ties, none left over
    assert bd.cheapest_days(cash, 0.0, n=2) == "Wed 04 Nov, Fri 06 Nov and 1 more at this price"
    assert bd.cheapest_days(pd.Series(dtype=float), 0.1) == ""
    # the same 9,032 miles on every seat day: the best days are where cash is dearest, never just the first day with a seat
    miles = pd.Series({d: (9032.0 if d != "2026-11-03" else nan) for d in cash.index})       # 3 Nov has no seat
    assert bd.best_miles_days(cash, miles) == "best days to use them: Sat 07 Nov (cash \u20ac200), Thu 05 Nov (cash \u20ac90), Sun 08 Nov (cash \u20ac88)"
    assert bd.best_miles_days(cash, pd.Series(dtype=float)) == ""
    # taxes come off the saving: a 150 euro tax on the 200 euro day sinks it below the cheaper days, and ties keep their date order
    tax = pd.Series({d: (150.0 if d == "2026-11-07" else 0.0) for d in cash.index})
    assert bd.best_miles_days(cash, miles, tax) == "best days to use them: Thu 05 Nov (cash \u20ac90), Sun 08 Nov (cash \u20ac88), Wed 04 Nov (cash \u20ac86)"
    # return trips: the shortlist of pairs, and the pairs where seats both ways replace the most cash
    trips = pd.DataFrame([("economy", "2026-11-07", "2026-11-14", 7, 50.0, 60.0, 110.0), ("economy", "2026-11-08", "2026-11-15", 7, 60.0, 55.0, 115.0),
                          ("economy", "2026-11-09", "2026-11-16", 7, 200.0, 250.0, 450.0)], columns=["cabin", "depart", "back", "nights", "outbound", "return", "total"])
    assert bd.cheapest_pairs(trips, 0.10) == "Sat 07 Nov \u2192 Sat 14 Nov, Sun 08 Nov \u2192 Sun 15 Nov"
    out_m = pd.Series({"2026-11-07": 9032.0, "2026-11-09": 9032.0})
    back_m = pd.Series({"2026-11-14": 9032.0, "2026-11-16": 9032.0})
    cash_of = {(r.depart, r.back): float(r.total) for r in trips.itertuples()}
    assert bd.best_miles_pairs(out_m, back_m, [7], cash_of).startswith("best trips to use them: Mon 09 Nov \u2192 Mon 16 Nov (cash \u20ac450), Sat 07 Nov \u2192 Sat 14 Nov (cash \u20ac110)")



def test_rate_limit_during_day_searches_stops_asking(monkeypatch):
    asked = []

    def limited(o, d, c, day, n):
        asked.append(day)
        raise RuntimeError("SearchHTTPError: HTTP 429")
    monkeypatch.setattr(collector, "fetch_cabin", lambda *a: {})  # the calendar answered, but empty: every day is worth a look
    monkeypatch.setattr(collector, "fetch_day", limited)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    d0 = date(2026, 11, 1)
    rows = list(collector.fetch_range(d0, d0 + timedelta(29), "ATH", "BIO", workers=1))
    assert len(rows) == 60 and all(r[4] == "error" and "429" in r[5] for r in rows)
    assert len(asked) <= 6  # stops after a few refusals instead of asking for all 60


def test_schedule_note_names_the_weekdays_and_the_last_flight_of_a_seasonal_route():
    import pandas as pd
    from datetime import date, timedelta
    from ui import schedule_note
    def seen(flies):
        days = [date(2026, 11, 1) + timedelta(i) for i in range(30)]
        return pd.DataFrame([dict(day=d.isoformat(), cabin=c, price=100.0 if flies(d) else float("nan"))
                             for d in days for c in ("economy", "business")])
    bio = seen(lambda d: d.weekday() in (1, 4) and d <= date(2026, 11, 20))  # ATH-BIO: Tuesdays and Fridays, last flight Fri 20 Nov
    assert schedule_note(bio, "ATH", "BIO") == ("AEGEAN flies ATH → BIO only on Tuesdays and Fridays, and not after Fri 20 Nov in the days "
                                               "checked (search later dates to see when it flies again).")
    assert schedule_note(seen(lambda d: True), "ATH", "FRA") is None  # every day: nothing to say
    assert schedule_note(seen(lambda d: d.weekday() == 0), "ATH", "XXX") == "AEGEAN flies ATH → XXX only on Mondays."  # weekly, not stopped


def test_schedule_note_says_no_service_and_names_aegean_airports_in_that_city():
    import pandas as pd
    from datetime import date, timedelta
    from ui import schedule_note
    def seen(n):
        return pd.DataFrame([dict(day=(date(2026, 10, 31) + timedelta(i)).isoformat(), cabin=c, price=float("nan"))
                             for i in range(n) for c in ("economy", "business")])
    assert schedule_note(seen(1), "ATH", "LCY") == ("AEGEAN has no nonstop flights ATH → LCY on the days checked (Sat 31 Oct). "
                                                    "Try London Heathrow (LHR) or London Gatwick (LGW, seasonal).")
    assert schedule_note(seen(30), "ATH", "LGW") == ("AEGEAN has no nonstop flights ATH → LGW on the days checked (Sat 31 Oct – Sun 29 Nov). "
                                                     "Try London Heathrow (LHR).")
    assert schedule_note(seen(3), "ATH", "FRA", nonstop=False) == "AEGEAN has no flights ATH → FRA on the days checked (Sat 31 Oct – Mon 02 Nov)."
    assert schedule_note(seen(0), "ATH", "LCY") is None  # nothing checked yet: nothing to say


def test_hotel_page_lists_the_hotels_collected_and_opens_one(tmp_path, monkeypatch):
    import os
    from datetime import datetime
    from streamlit.testing.v1 import AppTest
    page = os.path.abspath("views/hotel.py")
    t = db.connect(str(tmp_path / "tracker.db"))
    now = datetime.now().isoformat()
    for i in range(40, 50):  # a hotel a visitor searched, outside the default 30-day window
        t.execute("INSERT INTO hotel VALUES(?,?,?,?,?,?,?,?)", ((date.today() + timedelta(i)).isoformat(), "Kempinski Engelberg", "Kempinski", 2,
                                                                 "Kempinski Palace", 300.0 + i, now, 2))
    t.commit()
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception
    pick = at.selectbox(key="ho-saved")
    assert "Kempinski · 10 check-in days saved" in pick.format_func(0)
    assert not at.markdown or "Kempinski" not in " ".join(m.value for m in at.markdown)  # the default search is still Frankfurt
    pick.select(0).run()
    assert not at.exception
    assert at.text_input(key="ho-query").value == "Kempinski Engelberg" and at.number_input(key="ho-nights").value == 2
    assert at.date_input(key="ho-when").value == (date.today() + timedelta(40), date.today() + timedelta(49))
    assert "Kempinski" in " ".join(m.value for m in at.markdown)  # the page opens on that hotel's saved dates
    assert any(b.label.startswith("Download all") for b in at.get("download_button"))


def test_fetch_hotel_save_keeps_every_price_in_the_history(tmp_path, monkeypatch):
    import csv
    import fetch_hotel
    out = tmp_path / "hotel_searches.csv"
    day = (date.today() + timedelta(5)).isoformat()
    row = dict(source="google", day=day, hotel="H", price=120, tier="", fetched="2026-11-02T10:00:00", query="q", filt="f", hkey="", nights=1, adults=2)
    fetch_hotel.save([row], out)
    fetch_hotel.save([dict(row, price=110, fetched="2026-11-03T10:00:00")], out)
    assert [r["price"] for r in csv.DictReader(out.open())] == ["110"]  # the file: the newest price per day
    assert [r["price"] for r in csv.DictReader((tmp_path / "history_hotel.csv").open())] == ["120", "110"]  # the history: every change


def test_hotel_places_keep_every_hotel_a_search_returned(tmp_path):
    import csv
    import fetch_hotel
    seen = {}
    hotels.note(seen, [{"name": "Frankfurter Hof", "lat": 50.110, "lng": 8.676, "display_price": 287, "star_class": 5, "overall_rating": 4.5,
                        "review_count": 3401}, {"name": "Cheap Inn", "lat": 50.107, "lng": 8.665, "display_price": 90},
                       {"name": "No Coordinates", "display_price": 50}], date(2026, 11, 20))
    rows = hotels.place_rows(seen, "Steigenberger Frankfurt", 1, 2, "2026-11-02T10:00:00")
    assert {r["hotel"] for r in rows} == {"Frankfurter Hof", "Cheap Inn"}  # a hotel without a place cannot go on the map
    out = tmp_path / "hotel_places.csv"
    fetch_hotel.save_places(rows, out)
    fetch_hotel.save_places([dict(rows[1], low=80, fetched="2026-11-03T10:00:00")], out)  # a newer search replaces that hotel's row
    got = {r["hotel"]: r for r in csv.DictReader(out.open())}
    assert len(got) == 2 and got["Cheap Inn"]["low"] == "80" and got["Frankfurter Hof"]["stars"] == "5"
    assert 0.8 < hotels.km(50.110, 8.676, 50.107, 8.665) < 0.9


def test_hotel_landmarks_are_looked_up_once_per_search_and_never_break_a_fetch(tmp_path):
    import csv
    import fetch_hotel
    out = tmp_path / "hotel_landmarks.csv"
    places = {"Steigenberger Frankfurter Hof": dict(lat=50.110, lng=8.676), "Other": dict(lat=50.2, lng=8.7)}
    asked = []

    def find(lat, lng):
        asked.append((lat, lng))
        return [dict(kind="station", label="Main station", name="Frankfurt (Main) Hauptbahnhof", lat=50.107, lng=8.663)]
    s = dict(query="Steigenberger Frankfurt", filt="Frankfurter Hof")
    rows = fetch_hotel.landmark_rows(s, places, out, find)
    assert asked == [(50.110, 8.676)] and rows[0]["query"] == "Steigenberger Frankfurt"  # looked up around the hotel searched for
    fetch_hotel.save_landmarks(rows, out)
    assert fetch_hotel.landmark_rows(s, places, out, find) == [] and len(asked) == 1  # already saved: no second lookup
    assert [r["name"] for r in csv.DictReader(out.open())] == ["Frankfurt (Main) Hauptbahnhof"]

    def down(lat, lng):
        raise OSError("Nominatim is down")
    assert fetch_hotel.landmark_rows(dict(query="Elsewhere", filt="x"), places, out, down) == []  # the map just shows the hotels


def test_hotel_map_shows_for_a_name_typed_with_google_s_curly_apostrophe_even_before_prices(tmp_path, monkeypatch):
    import os
    from streamlit.testing.v1 import AppTest
    assert hotels.matches("Amrâth Grand Hotel de l'Empereur", "Amrâth Grand Hotel de l’Empereur")
    page = os.path.abspath("views/hotel.py")
    monkeypatch.chdir(tmp_path)  # an empty database: no prices for this search at all
    at = AppTest.from_file(page, default_timeout=60).run()
    name = "Amrâth Grand Hotel de l’Empereur"
    at.text_input(key="ho-query").set_value(name)
    at.text_input(key="ho-filt").set_value(name)
    at.number_input(key="ho-nights").set_value(3)
    at.run()
    assert not at.exception
    assert any("No prices saved yet" in i.value for i in at.info)
    assert any("The hotels around it: Amrâth Grand Hotel de" in m.value for m in at.markdown)  # the committed places file puts it on the map anyway


def test_hotel_page_ranks_the_hotels_around_by_the_total_for_the_stay(tmp_path, monkeypatch):
    import os
    from datetime import datetime
    from streamlit.testing.v1 import AppTest
    import fetch_hotel
    now, day = datetime.now().isoformat(timespec="seconds"), lambda i: (date.today() + timedelta(i)).isoformat()
    places = {"Grand Hotel Here": dict(lat=50.110, lng=8.676, stars=5, rating=4.6, reviews=900, prices={day(5): 300, day(6): 250}),
              "Cheap Inn": dict(lat=50.112, lng=8.678, stars=None, rating=3.9, reviews=40, prices={day(5): 70, day(6): 90}),
              "Good Value": dict(lat=50.108, lng=8.672, stars=None, rating=4.7, reviews=300, prices={day(6): 95}),
              "Far Away": dict(lat=50.300, lng=8.900, stars=None, rating=4.8, reviews=10, prices={day(5): 20}),
              "Bed In A Dorm": dict(lat=50.111, lng=8.677, stars=None, rating=3.1, reviews=500, prices={day(6): 30})}  # < half of €90
    monkeypatch.setattr(fetch_hotel, "PLACES", tmp_path / "hotel_places.csv")
    fetch_hotel.save_places(hotels.place_rows(places, "Test Query", 3, 2, now), tmp_path / "hotel_places.csv")
    fetch_hotel.save_nearby(hotels.day_rows(places, "Test Query", 3, 2, now), tmp_path / "hotel_nearby.csv")
    page = os.path.abspath("views/hotel.py")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch_hotel, "NEARBY", tmp_path / "hotel_nearby.csv")
    at = AppTest.from_file(page, default_timeout=60).run()
    for k, v in (("ho-query", "Test Query"), ("ho-filt", "Grand Hotel Here")):
        at.text_input(key=k).set_value(v)
    at.number_input(key="ho-nights").set_value(3)
    at.run()
    assert not at.exception
    said = [m.value for m in at.markdown if m.value.startswith("Cheapest:")][0]
    assert "**Cheap Inn**, €210 for the 3 nights (€70 a night for the room, 2 adults, before city tax)" in said  # far away hotel left out
    assert "Grand Hotel Here is €750" in said and "1 cheaper listing without a star class is left out" in said  # the dorm bed: listed only
    table = at.dataframe[-1].value
    assert table.set_index("Hotel").Note["Bed In A Dorm"] == "may be a shared or small room, check it"
    # the hotel's own fresher prices replace the neighbours' snapshot everywhere, and a suites-only night stays out of the statistics
    t = db.connect(str(tmp_path / "tracker.db"))
    for i, price in ((5, 240.0), (6, 900.0), (7, 250.0), (8, 260.0)):
        t.execute("INSERT INTO hotel VALUES(?,?,?,?,?,?,?,?)", (day(i), "Test Query", "Grand Hotel Here", 3, "Grand Hotel Here", price, now, 2))
    t.commit()
    at.run()
    assert not at.exception
    said = [m.value for m in at.markdown if m.value.startswith("Cheapest:")][0]
    assert "Grand Hotel Here is €720" in said  # €240 a night from its own prices, not €250 from the older snapshot
    cards = " ".join(m.value for m in at.markdown)
    assert "Only expensive rooms left" in cards and "€900" in cards and "Typical night" in cards and "Average night" not in cards
    assert "Dearest night" in cards and "€260" in cards
    assert any("Prices of the hotels around it checked" in c.value for c in at.caption)
    at.button_group(key="ho-stay-rating").set_value(4.5).run()
    assert "**Good Value**, €285 for the 3 nights" in [m.value for m in at.markdown if m.value.startswith("Cheapest:")][0]


def test_hotel_fetch_finds_the_tripadvisor_key_itself_when_none_is_given(tmp_path):
    import fetch_hotel
    d = date.today() + timedelta(5)
    s = fetch_hotel.search(dict(QUERY="Grand Resort Bad Ragaz", FILT="Grand Resort", NIGHTS="1", ADULTS="2", RANGES=f"{d},{d}", BOOKING="1"))
    assert s["hkey"] == ""

    def google(a, b, q, f, n, ad, skip=(), places=None):
        places["Grand Resort Bad Ragaz"] = dict(lat=47.0035, lng=9.5005, stars=5, rating=4.7, reviews=900, prices={d.isoformat(): 600})
        yield d, "Grand Resort Bad Ragaz", 600, "ok", None
    asked = []

    def find(name, lat, lng, towns, rapid):
        asked.append((name, lat, lng))
        return "g198797-d2070866"
    rows = fetch_hotel.fetch(s, google=google, heat=lambda k, a, b: {d.isoformat(): "cheap"}, rate=lambda k, day, n, adults: 550,
                             places={}, find=find)
    assert asked == [("Grand Resort Bad Ragaz", 47.0035, 9.5005)]  # looked up by Google's name and place for the hotel
    assert {r["source"] for r in rows} == {"google", "heat", "booking"} and {r["hkey"] for r in rows} == {"g198797-d2070866"}
    s["hkey"] = ""
    rows = fetch_hotel.fetch(s, google=google, places={}, find=lambda *a: None)  # not found: Google prices only, nothing breaks
    assert {r["source"] for r in rows} == {"google"} and rows[0]["hkey"] == ""


def test_flights_page_says_no_flight_only_after_every_day_came_back_empty(tmp_path, monkeypatch):
    import os
    from datetime import datetime
    from streamlit.testing.v1 import AppTest
    page = os.path.abspath("views/flights.py")
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(page, default_timeout=60).run()  # nothing saved yet (a search not started or still running)
    text = " ".join(m.value for m in at.markdown) + " ".join(i.value for i in at.info)
    assert not at.exception and "found none" not in text and "What should I book?" not in text
    t = db.connect(str(tmp_path / "tracker.db"))  # now every day of the default 30 came back with no AEGEAN flight, both ways
    now = datetime.now().isoformat()
    for i in range(1, 31):
        for o, d_ in (("ATH", "FRA"), ("FRA", "ATH")):
            for cabin in ("economy", "business"):
                t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", ((date.today() + timedelta(i)).isoformat(), o, d_, cabin, None, "calendar", now, 1))
    t.commit()
    at.run()
    assert not at.exception and any("Google Flights found none" in i.value for i in at.info)
    assert "What should I book?" not in " ".join(m.value for m in at.markdown)  # nothing to advise on


def test_fetch_route_stops_at_its_time_budget_and_keeps_what_it_fetched(monkeypatch):
    import fetch_route
    clock = iter(range(0, 10_000, 60))  # each call to the clock is a minute later
    monkeypatch.setattr(fetch_route.time, "monotonic", lambda: next(clock))
    monkeypatch.setenv("BUDGET_MIN", "3")
    d0 = date(2026, 12, 1)
    days = (lambda a, b, o, d, **k: ((d0 + timedelta(i), "economy", 100.0 + i, "calendar", "ok", None) for i in range(10)))
    monkeypatch.setattr(fetch_route, "fetch_range", days)
    rows, failed = fetch_route.fetch(f"ATH,FRA,{d0},{d0 + timedelta(9)}", True, passes=1, pause=0)
    assert 0 < len(rows) < 10 and failed  # stopped early, rows kept, and it says not everything was fetched


def test_footer_age():
    from datetime import datetime

    import ui
    now = datetime(2026, 11, 7, 12, 0, 0)
    assert ui.ago("2026-11-07T11:58:30", now) == "1 minute ago"
    assert ui.ago("2026-11-07T09:00:00", now) == "3 hours ago"
    assert ui.ago("2026-11-05T11:00:00", now) == "2 days ago"
    assert ui.ago("2026-11-07T12:00:10", now) == "just now"  # a stamp a little ahead of this clock


def test_daily_hotel_run_also_saves_the_hotels_around_it(tmp_path, monkeypatch):
    import collect_daily
    from datetime import date as _d
    def google(start, end, q, f, n, a, skip=(), places=None):
        for i in range((end - start).days + 1):
            d = start + timedelta(i)
            if places is not None:
                places.setdefault("Near Inn", dict(lat=50.1, lng=8.6, stars=3, rating=4.2, reviews=90, prices={}))["prices"][d.isoformat()] = 80
            yield d, "Steigenberger Frankfurter Hof", 150, "ok", None
    places = {}
    got, missing = collect_daily.collect_hotel(google=google, rate=lambda *a, **k: 160, heat=lambda *a: {}, today=_d(2026, 12, 1),
                                               pause=0, places=places)
    assert places["Near Inn"]["prices"] and not missing
