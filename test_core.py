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
