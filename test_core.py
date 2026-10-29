"""Offline tests: no network. Run with `pytest`. Live checks are in the app's Health page."""
import time
from datetime import date, timedelta

import collector
import db
import hotels
import jobs
import par


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


def test_old_rows_are_deleted(tmp_path):
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
        calls.append(1)
        raise RuntimeError("SearchHTTPError: HTTP 429")
    monkeypatch.setattr(collector, "fetch_cabin", limited)
    monkeypatch.setattr(collector, "fetch_day", limited)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    d0 = date(2026, 11, 1)
    rows = list(collector.fetch_range(d0, d0 + timedelta(29), "ATH", "FRA"))
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
    for page in ("views/flights.py", "views/hotel.py", "views/health.py"):
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
