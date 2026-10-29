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


def test_calendar_gaps_are_checked_day_by_day(monkeypatch):
    d0 = date(2026, 11, 1)
    monkeypatch.setattr(collector, "fetch_cabin", lambda *a: {"2026-11-01": 100.0})
    monkeypatch.setattr(collector, "fetch_day", lambda o, d, c, day, n: 222.0 if day == d0 + timedelta(1) else None)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    got = sorted((g[0], g[2], g[3], g[4]) for g in collector.fetch_range(d0, d0 + timedelta(2), "ATH", "BIO", cabins=("economy",)))
    assert got == [(d0, 100.0, "calendar", "ok"), (d0 + timedelta(1), 222.0, "day search", "ok"), (d0 + timedelta(2), None, "", "none")]


def test_empty_answer_is_asked_twice(monkeypatch):
    calls = []
    monkeypatch.setattr(collector, "fetch_cabin", lambda *a: {})
    monkeypatch.setattr(collector, "fetch_day", lambda *a: calls.append(1) or (150.0 if len(calls) > 1 else None))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    d0 = date(2026, 11, 1)
    assert next(collector.fetch_range(d0, d0, "ATH", "FRA", cabins=("economy",)))[2:5] == (150.0, "day search", "ok")


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
    monkeypatch.setattr(xotelo, "booking_rate", lambda key, d, n=1: 100.0 + d.day)
    here = os.path.dirname(os.path.abspath(__file__))
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(os.path.join(here, "views/hotel.py"), default_timeout=60).run()
    first, days = _date.today() + timedelta(1), 30  # default window: the next 30 days from tomorrow
    at.button[0].click().run()
    deadline = time.time() + 20
    while any(v["running"] for v in jobs.JOBS.values()) and time.time() < deadline:
        time.sleep(0.2)
    at.run()
    cards = " ".join(m.value for m in at.markdown)
    cheapest = min(100 + (first + timedelta(i)).day for i in range(days))
    assert f"€{cheapest}" in cards and "Booking.com via Xotelo" in cards
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



def test_start_date_plus_window(tmp_path, monkeypatch):
    """Pick a start date (e.g. 29 Nov) and 15 days: the route header shows 29 Nov to 13 Dec."""
    import os
    from datetime import date as _date
    from streamlit.testing.v1 import AppTest
    here = os.path.dirname(os.path.abspath(__file__))
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(os.path.join(here, "views/flights.py"), default_timeout=60).run()
    first = _date.today() + timedelta(31)
    at.date_input(key="fl-from").set_value(first).run()
    at.button_group(key="fl-win").set_value("15 days").run()
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
    at.button_group(key="fl-trip").set_value("Return").run()
    at.button_group(key="fl-win").set_value("15 days").run()
    at.button[0].click().run()
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

    def rate(key, d, nights):  # Booking.com fails once for every day, then answers
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


def test_flights_page_keeps_value_metric_typed_only():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file("views/flights.py", default_timeout=60).run()
    assert not at.exception
    shown = " ".join(m.value for m in at.markdown)
    assert "Best miles value" not in shown  # collected miles carry no taxes: they must not feed the value metric (only typed ones do)
    assert "Lowest economy award" in shown and "Lowest business award" in shown  # the miles sit in the headline, not in a hidden tab


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
    assert av.best_award(s, True) == ("9,032 miles", "Sat 07 Nov \u00b7 same on all 2 days with seats")
    mixed = pd.Series({"2026-11-07": 9032.0, "2026-11-08": 15000.0, "2026-11-09": 9032.0})
    assert av.best_award(mixed, True) == ("9,032 miles", "Sat 07 Nov \u00b7 2 days at this level \u00b7 3 days with seats")
    assert av.best_award(pd.Series({"2026-11-07": float("nan")}), True)[1] == "No award seats on these days"
    assert av.best_award(pd.Series(dtype=float), False)[1] == "Not collected yet for this route"  # no data is not the same as no seats
    t = av.miles_table(s, pd.Series({"2026-11-07": 16935.0, "2026-11-08": float("nan"), "2026-11-09": float("nan")}))
    assert t.to_dict("list") == {"Date": ["Sat 07 Nov", "Mon 09 Nov"], "Economy miles": ["9,032", "9,032"], "Business miles": ["16,935", "\u2014"]}
    assert len(av.miles_chart(s, s).data) == 2


def test_booking_strategy_one_way_and_return():
    import pandas as pd
    import award_view as av
    view = pd.DataFrame({"economy": [120.0, 86.0, float("nan")], "econ_miles": [float("nan"), 9032.0, 9032.0]},
                        index=["2026-11-07", "2026-11-09", "2026-11-14"])
    ow = av.one_way_plan(view, "economy", "econ_miles")
    assert ow["cash"] == ("2026-11-09", 86.0) and ow["miles"] == ("2026-11-09", 9032.0, 86.0, None) and ow["best"][:4] == ("2026-11-09", 9032.0, 86.0, None)
    assert round(av.per_thousand(86.0, 9032.0), 1) == 9.5
    html = av.plan_html("Economy", "#2563C9", ow, None, 10.0)
    assert "On Mon 09 Nov: pay cash" in html and "below your" in html  # 9.5 per 1,000 is below 10 (cash and miles on the same day: one line)
    assert "On Mon 09 Nov: use miles" in av.plan_html("Economy", "#2563C9", ow, None, 8.0)  # ...and above 8
    assert "No award seats" in av.plan_html("Business", "#B8741A", av.one_way_plan(view.assign(m=float("nan")), "economy", "m"), None, 10.0)
    # return: miles pair is outbound 2026-11-07 + 7 nights = 2026-11-14 only when both days have miles
    out_m = pd.Series({"2026-11-07": 9032.0, "2026-11-09": 9032.0})
    back_m = pd.Series({"2026-11-16": 9032.0, "2026-11-21": 9032.0})
    assert av.best_return_miles(out_m, back_m, [7, 5]) == (18064.0, "2026-11-09", "2026-11-16", 7, None)
    assert av.best_return_miles(out_m, back_m, [3]) is None
    trips = pd.DataFrame([("economy", "2026-11-09", "2026-11-16", 7, 86.0, 90.0, 176.0)],
                         columns=["cabin", "depart", "back", "nights", "outbound", "return", "total"])
    rp = av.return_plan(trips, out_m, back_m, [7])
    assert rp["cash"] == ("2026-11-09", "2026-11-16", 7, 176.0) and rp["miles"] == ("2026-11-09", "2026-11-16", 7, 18064.0, 176.0, None)
    both = av.plan_html("Economy", "#2563C9", ow, rp, 10.0)
    assert "Return" in both and "18,064 miles" in both and "7 nights" in both


def test_strategy_separates_flexible_dates_from_being_tied_to_the_miles_day():
    import pandas as pd
    import award_view as av
    view = pd.DataFrame({"economy": [168.0, 86.0, 86.0], "econ_miles": [9032.0, float("nan"), float("nan")]},
                        index=["2026-11-07", "2026-11-18", "2026-11-19"])
    html = av.plan_html("Economy", "#2563C9", av.one_way_plan(view, "economy", "econ_miles"), None, 10.0)
    assert "Flexible dates: pay cash" in html   # 86 / 9,032 = 9.5 per 1,000, below 10
    assert "Best day for miles, Sat 07 Nov: use miles" in html   # 168 / 9,032 = 18.6 per 1,000, above 10: miles beat cash on a day when cash is dear


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
    assert not at.exception and [t.label for t in at.tabs] == ["Cash fares", "Award miles", "Hotels"]
    src = open(page, encoding="utf-8").read()
    ns = {}
    exec(src[src.index("def changes_only"):src.index("def overview")], {"pd": pd}, ns)  # the pure helper, without running the page
    df = pd.DataFrame({"day": ["d"] * 4, "fetched": ["t1", "t2", "t3", "t4"], "price": [100.0, 100.0, 90.0, 90.0]})
    assert list(ns["changes_only"](df, ["day"], "price").fetched) == ["t1", "t3"]


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
    assert round(av.per_thousand(168.0, 9032.0, 30.44), 1) == 15.2    # (168 - 30.44) / 9,032 * 1000: the taxes come off the saving
    html = av.plan_html("Economy", "#2563C9", ow, None, 10.0)
    assert "9,032 miles</b> + \u20ac30 taxes" in html and "9,032 miles + \u20ac30 taxes instead of \u20ac168 is \u20ac15.2 per 1,000 miles" in html
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
    html = av.plan_html("Economy", "#2563C9", ow, None, 10.0)
    assert "Best day for miles, Sat 14 Nov: use miles" in html and "€33.2 per 1,000" in html
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


def test_aegean_pass_compared_with_cash():
    from datetime import date
    import pandas as pd
    import pass_value as pv
    today = date(2026, 11, 1)
    # fares by day; the first two are inside the 7-day booking window and must not count
    cash = pd.Series({"2026-11-03": 40.0, "2026-11-07": 45.0, "2026-11-10": 90.0, "2026-11-12": 120.0, "2026-11-20": 200.0, "2026-11-25": float("nan")})
    assert pv.effective_cost(1200.0, 12, 12, 30.0) == 130.0          # 1,200 / 12 flights + 30 taxes
    assert pv.effective_cost(1200.0, 12, 6, 30.0) == 230.0           # use only 6 of the 12 and each flight costs more
    assert pv.effective_cost(1200.0, 12, 99, 30.0) == 130.0          # you cannot use more flights than the pass has
    a = pv.assess(cash, 1200.0, 12, 12, 30.0, 7, today)
    assert a["days"] == 3 and a["cheapest"] == 90.0 and a["typical"] == 120.0   # only 10, 12 and 20 Nov are bookable 7+ days ahead
    assert a["pass_cheaper"] == 1 and round(a["share"], 2) == 0.33              # cash is above the pass's 130 only on the 200 day
    assert a["break_even_uses"] is None   # it would need 1,200 / (120 - 30) = 14 flights to beat the typical fare, but the pass only has 12
    assert pv.verdict(a)[1].startswith("<b>Only on the dear days.")
    cheap = pv.assess(cash, 600.0, 12, 12, 30.0, 7, today)                       # 80 per flight: below the cheapest fare of 90
    assert pv.verdict(cheap)[0] == "miles" and cheap["break_even_uses"] == 7     # 600 / (120 - 30) = 6.7 -> 7 flights
    assert pv.assess(cash, 600.0, 12, 12, 30.0, 60, today) is None               # nothing bookable that far ahead
    assert len(pv.chart(a).data) == 1


def test_pass_card_shows_the_numbers_and_the_unpriced_benefits():
    from datetime import date
    import pandas as pd
    import pass_value as pv
    cash = pd.Series({"2026-11-10": 90.0, "2026-11-12": 120.0, "2026-11-20": 200.0})
    a = pv.assess(cash, 600.0, 12, 12, 30.0, 7, date(2026, 11, 1))
    html = pv.card_html(a, 600.0, 12, 12, 30.0, 7, ret=(2, 5))
    assert "booked 7 days ahead" in html and "\u20ac80</b>" in html and "3 of 3 days" in html and "2 of 5" in html and "7 flights</b> used" in html
    assert "Free changes".lower() in html.lower() and "Worth it on price" in html


def test_aegean_pass_section_appears_once_a_price_is_entered():
    import os
    from streamlit.testing.v1 import AppTest
    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "views", "flights.py")
    at = AppTest.from_file(page, default_timeout=90).run()
    assert not at.exception and "Is the Aegean Pass worth it?" in " ".join(m.value for m in at.markdown)
    assert "Aegean Pass, booked" not in " ".join(m.value for m in at.markdown)       # no price yet: only the prompt
    at.number_input(key="pass-price").set_value(900.0).run()
    assert not at.exception
    shown = " ".join(m.value for m in at.markdown)
    assert "Aegean Pass, booked 7 days ahead" in shown and "Effective cost per flight" in shown   # 7 days is the default window


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
                 "2026-11-20,ATH,FRA,business,16935,1,2026-10-30T07:00:00\n")           # a seat appeared
    md = digest.build(str(tmp_path))
    assert md.startswith("# What changed on 30 Oct 2026")
    assert "Wed 18 Nov, economy: \u20ac86 \u2192 \u20ac79 (\u2212\u20ac7)" in md and "\u20ac100 \u2192 \u20ac120 (+\u20ac20)" in md
    assert "Fri 20 Nov, business: 16,935 miles (was none)" in md and "2027" not in md and "Jan" not in md
    assert digest.build(str(tmp_path / "nothing")) == ""   # no files: nothing to say, and no error
