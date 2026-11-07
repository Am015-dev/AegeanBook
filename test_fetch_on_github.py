"""Fetching on GitHub's servers: the script the workflow runs, the app's GitHub calls, and the Flights page's Update prices."""
import io
import json
import os
from datetime import date, datetime, timedelta

import pytest

import collector
import db
import fetch_route
import gh
import trip_plan as tp

HERE = os.path.dirname(os.path.abspath(__file__))


def test_fetch_route_parses_fetches_and_keeps_only_fresh_future_rows(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        fetch_route.parse("ATH,BIO;rm -rf,x,y,z")  # only airport pairs and dates get through
    d1 = date.today() + timedelta(1)
    legs = fetch_route.parse(f"ath,bio,{d1},{d1};BIO,ATH,{d1},{d1}")
    assert legs == [("ATH", "BIO", d1, d1), ("BIO", "ATH", d1, d1)]
    out = tmp_path / "routes.csv"
    old = (datetime.now() - timedelta(days=30)).isoformat()
    out.write_text("day,orig,dest,cabin,price,source,fetched,nonstop\n"
                   f"{d1},ATH,FRA,economy,100,calendar,{datetime.now().isoformat()},1\n"  # kept
                   f"{d1},ATH,FRA,business,300,calendar,{old},1\n"  # fetched too long ago: dropped
                   f"2020-01-01,ATH,FRA,economy,100,calendar,{datetime.now().isoformat()},1\n")  # past day: dropped
    monkeypatch.setattr(fetch_route, "OUT", out)

    asked = []

    def fake(a, b, o, d, skip=(), nonstop=True):
        asked.append((o, sorted(skip)))
        if (a.isoformat(), "economy") not in skip:
            yield a, "economy", 97.0 if o == "ATH" else None, "calendar", "ok" if o == "ATH" else "none", None
        yield a, "business", None, "", "error", "HTTP 429"  # failed: not saved, retried in the next pass and the next fetch
    monkeypatch.setattr(fetch_route, "fetch_range", fake)
    monkeypatch.setattr(fetch_route.time, "sleep", lambda s: None)
    assert fetch_route.main(f"ATH,BIO,{d1},{d1};BIO,ATH,{d1},{d1}", True) == 0
    assert [o for o, _ in asked] == ["ATH"] * 3 + ["BIO"] * 3 and asked[1][1] == [(d1.isoformat(), "economy")]  # retries skip saved days
    rows = out.read_text().splitlines()
    assert len(rows) == 1 + 3 and any(",ATH,BIO,economy,97.0," in r for r in rows) and any(",BIO,ATH,economy,," in r for r in rows)
    assert not any("business" in r and "BIO" in r for r in rows) and not any("2020-01-01" in r for r in rows)
    today = date.today().isoformat()  # the searched routes are recorded, with the days asked for, so the daily run can refresh them
    assert (tmp_path / "tracked_routes.csv").read_text().splitlines() == ["orig,dest,nonstop,last_requested,from,to,airline",
                                                                           f"ATH,BIO,1,{today},{d1},{d1},A3", f"BIO,ATH,1,{today},{d1},{d1},A3"]


def test_tracked_routes_are_upserted_by_the_merge_step_with_the_newest_request_date(tmp_path, monkeypatch):
    out, d1 = tmp_path / "routes.csv", (date.today() + timedelta(5)).isoformat()
    monkeypatch.setattr(fetch_route, "OUT", out)
    (tmp_path / "tracked_routes.csv").write_text("orig,dest,nonstop,last_requested\nATH,BIO,1,2026-01-01\nSKG,LCA,1,2026-02-02\n")
    row = dict(day=d1, orig="ATH", dest="BIO", cabin="economy", price=97.0, source="calendar", fetched=datetime.now().isoformat(), nonstop=1)
    fetch_route.write_new([row], tmp_path / "a.csv")
    fetch_route.save(fetch_route.read(tmp_path / "a.csv"), track=True)  # the --merge path
    assert (tmp_path / "tracked_routes.csv").read_text().splitlines()[1:] == [f"ATH,BIO,1,{date.today()},{d1},{d1},A3", "SKG,LCA,1,2026-02-02,,,A3"]
    fetch_route.save([dict(row, price=98.0)])  # a --refresh save does not count as a request
    assert f"ATH,BIO,1,{date.today()},{d1},{d1},A3" in (tmp_path / "tracked_routes.csv").read_text()
    may, far = (date.today() + timedelta(200)).isoformat(), (date.today() + timedelta(400)).isoformat()  # a summer month planned later
    fetch_route.save([dict(row, day=may), dict(row, day=far)], track=True)
    assert f"ATH,BIO,1,{date.today()},{d1},{date.today() + timedelta(330)},A3" in (tmp_path / "tracked_routes.csv").read_text()  # the union, capped
    fetch_route.save([dict(row, day=(date.today() + timedelta(2)).isoformat())], track=True)  # a nearer search widens the start, keeps the end
    assert f"ATH,BIO,1,{date.today()},{date.today() + timedelta(2)},{date.today() + timedelta(330)},A3" in (tmp_path / "tracked_routes.csv").read_text()
    wf = open(os.path.join(HERE, ".github", "workflows", "fetch-route.yml")).read()
    assert "git add data/routes.csv data/tracked_routes.csv" in wf


def test_refresh_fetches_recent_tracked_routes_not_the_daily_legs_capped_and_writes_history(tmp_path, monkeypatch):
    import collect_daily
    out, today = tmp_path / "routes.csv", date.today()
    monkeypatch.setattr(fetch_route, "OUT", out)
    monkeypatch.setattr(fetch_route, "TRACK_MAX", 3)
    lines = ["orig,dest,nonstop,last_requested", f"ATH,FRA,1,{today}",  # a daily leg: collect_daily fetches it already
             f"OLD,ONE,1,{today - timedelta(61)}",  # not searched for over 60 days: dropped
             f"ATH,BIO,1,{today - timedelta(60)}", f"BIO,ATH,0,{today - timedelta(10)}", f"SKG,LCA,1,{today - timedelta(1)}",
             f"LCA,SKG,1,{today}"]  # four recent routes, cap 3: the oldest (ATH-BIO) is skipped
    (tmp_path / "tracked_routes.csv").write_text("\n".join(lines) + "\n")
    assert ("ATH", "FRA") in collect_daily.LEGS
    asked = []

    def fake(a, b, o, d, skip=(), nonstop=True):
        asked.append((o, d, nonstop, a, b))
        yield a, "economy", 120.0, "calendar", "ok", None
    monkeypatch.setattr(fetch_route, "fetch_range", fake)
    monkeypatch.setattr(fetch_route.time, "sleep", lambda s: pytest.fail("nothing failed: no pause"))
    assert fetch_route.refresh() == 0
    assert sorted((o, d, n) for o, d, n, _, _ in asked) == [("BIO", "ATH", False), ("LCA", "SKG", True), ("SKG", "LCA", True)]
    assert all((a, b) == (today + timedelta(1), today + timedelta(90)) for *_, a, b in asked)
    rows = fetch_route.read(out)
    assert {(r["orig"], r["dest"], r["nonstop"]) for r in rows} == {("BIO", "ATH", "0"), ("LCA", "SKG", "1"), ("SKG", "LCA", "1")}
    hist = (tmp_path / "history_prices.csv").read_text().splitlines()
    assert len(hist) == 4 and hist[0].startswith("day,orig,dest,cabin,price")
    assert fetch_route.refresh() == 0 and len((tmp_path / "history_prices.csv").read_text().splitlines()) == 4  # unchanged prices: no new rows
    wf = open(os.path.join(HERE, ".github", "workflows", "daily.yml")).read()
    assert "python fetch_route.py --refresh" in wf and "data/routes.csv data/tracked_routes.csv" in wf


def test_refresh_reaches_the_requested_end_and_only_beyond_the_daily_run_for_its_legs(tmp_path, monkeypatch):
    import collect_daily
    out, today = tmp_path / "routes.csv", date.today()
    monkeypatch.setattr(fetch_route, "OUT", out)
    (tmp_path / "tracked_routes.csv").write_text("orig,dest,nonstop,last_requested,from,to\n"
                                                 f"ATH,FRA,1,{today},{today + timedelta(200)},{today + timedelta(230)}\n"  # a daily leg, summer
                                                 f"FRA,ATH,1,{today},{today + timedelta(5)},{today + timedelta(60)}\n"  # a daily leg, inside its 90 days
                                                 f"ATH,BIO,1,{today},{today + timedelta(200)},{today + timedelta(400)}\n"  # planned past the horizon
                                                 f"BIO,ATH,1,{today},{today + timedelta(1)},{today + timedelta(20)}\n")  # a short search: 90 days anyway
    assert ("ATH", "FRA") in collect_daily.LEGS and collect_daily.DAYS == 90
    asked = []

    def fake(a, b, o, d, skip=(), nonstop=True):
        asked.append((o, d, a, b))
        yield a, "economy", 120.0, "calendar", "ok", None
    monkeypatch.setattr(fetch_route, "fetch_range", fake)
    assert fetch_route.refresh() == 0
    assert asked == [("ATH", "FRA", today + timedelta(91), today + timedelta(230)),  # only the part the daily run does not cover
                     ("ATH", "BIO", today + timedelta(1), today + timedelta(330)),  # to the requested end, at most the horizon
                     ("BIO", "ATH", today + timedelta(1), today + timedelta(90))]  # FRA-ATH: the daily run has it all


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_github_calls(monkeypatch):
    sent = []

    def urlopen(req, timeout):
        sent.append(req)
        if req.get_method() == "POST":
            return Answer(b"")
        if "/runs" in req.full_url:
            return Answer(json.dumps({"workflow_runs": [{"display_title": "fetch other", "status": "queued", "html_url": "x"},
                                                        {"display_title": f"fetch {request}", "status": "completed",
                                                         "conclusion": "success", "html_url": "https://github.com/run/1"}]}).encode())
        return Answer(b"day,orig\n")
    monkeypatch.setattr(gh.urllib.request, "urlopen", urlopen)
    d1 = date(2026, 11, 3)
    request = gh.fetch("tok", [("ATH", "BIO", d1, d1 + timedelta(29))], True)
    body = json.loads(sent[0].data)
    assert sent[0].full_url.endswith("/actions/workflows/fetch-route.yml/dispatches") and body["ref"] == "main"
    assert body["inputs"] == {"legs": "ATH,BIO,2026-11-03,2026-12-02", "nonstop": "1", "request": request, "airline": "A3"}
    assert sent[0].headers["Authorization"] == "Bearer tok"
    assert sent[0].headers["Content-type"] == "application/json"  # without it urllib sends a form type and GitHub answers HTTP 415
    assert gh.run("tok", request) == dict(status="completed", conclusion="success", url="https://github.com/run/1")


def test_data_file_is_cached_and_a_failure_falls_back_to_the_deployed_copy(monkeypatch):
    import importlib
    real = importlib.reload(gh).data_file  # the conftest swaps data_file out; test the real one
    calls = []
    monkeypatch.setattr(gh, "_api", lambda tok, m, path, raw=False, **k: calls.append(path) or "day,orig\n2026-11-03,ATH\n")
    p = real("data/routes.csv", "tok")
    assert open(p).read().startswith("day,orig") and real("data/routes.csv", "tok") == p and len(calls) == 1  # reused
    gh.forget("data/routes.csv")
    monkeypatch.setattr(gh, "_api", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert real("data/routes.csv", "tok") is None  # the page then uses the copy it was deployed with
    assert real("data/routes.csv", None) is None  # no token (the tests remove GITHUB_TOKEN), no GitHub


def test_gaps_are_the_days_still_missing():
    d = date(2027, 5, 1)
    saved = {((d + timedelta(i)).isoformat(), c) for i in range(92) for c in ("economy", "business")}  # May to July
    assert collector.gaps(d, date(2027, 8, 31), saved) == [(date(2027, 8, 1), date(2027, 8, 31))]  # only August is fetched
    saved.discard(("2027-06-10", "business"))  # one cabin missing on one day: that day again
    assert collector.gaps(d, date(2027, 7, 31), saved) == [(date(2027, 6, 10), date(2027, 6, 10))]
    assert collector.gaps(d, date(2027, 7, 31), saved | {("2027-06-10", "business")}) == []


def test_routes_file_keeps_one_row_per_day_the_newest(tmp_path, monkeypatch):
    out = tmp_path / "routes.csv"
    d1 = (date.today() + timedelta(3)).isoformat()
    row = lambda price, at: dict(day=d1, orig="BIO", dest="ATH", cabin="economy", price=price, source="calendar", fetched=at, nonstop=1)
    fetch_route.save([row(91.0, (datetime.now() - timedelta(days=2)).isoformat())], out)
    fetch_route.save([row(99.0, datetime.now().isoformat())], out)
    lines = out.read_text().splitlines()
    assert len(lines) == 2 and ",99.0," in lines[1]


def test_update_prices_fetches_on_github_and_shows_the_results(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.chdir(tmp_path)
    t = db.connect(str(tmp_path / "tracker.db"))
    for i in range(1, 11):  # the first 10 days of ATH -> FRA are saved already (one with no flight): they are not fetched again
        for c in ("economy", "business"):
            t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", ((date.today() + timedelta(i)).isoformat(), "ATH", "FRA", c,
                                                                     None if i == 5 else 150.0, "calendar", datetime.now().isoformat(), 1))
    t.commit()
    started, state = [], {"run": None}
    monkeypatch.setattr(gh, "token", lambda: "tok")
    monkeypatch.setattr(gh, "fetch", lambda tok, legs, nonstop, airline="A3": (started.append(legs) if airline == "A3" else None) or "abcd1234")
    monkeypatch.setattr(gh, "run", lambda tok, request, workflow="fetch-route.yml": state["run"])
    monkeypatch.setattr(collector, "fetch_range", lambda *a, **k: pytest.fail("must not search from the app's own server"))
    at = AppTest.from_file(os.path.join(HERE, "views", "flights.py"), default_timeout=60).run()
    at.button_group(key="fl-trip").set_value("One-way").run()  # the page now starts as Return, like the Overview
    at.button[1].click().run()  # Update prices (the first button swaps the airports)
    assert not at.exception and len(started) == 1
    d = lambda i: date.today() + timedelta(i)
    assert started[0] == [("ATH", "FRA", d(11), d(30)), ("FRA", "ATH", d(1), d(30))]  # only the missing days, and the way back too
    assert "Searching on GitHub" in " ".join(i.value for i in at.info)
    assert "already saved" not in " ".join(i.value for i in at.info)  # it said so next to a search that had just started
    # GitHub finished: the fetched fares are in the database and the page shows them
    t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", (d(12).isoformat(), "ATH", "FRA", "economy", 77.0, "calendar",
                                                             datetime.now().isoformat(), 1))
    t.commit()
    state["run"] = dict(status="completed", conclusion="success", url="u")
    at.run()
    assert not at.exception and "Searching on GitHub" not in " ".join(i.value for i in at.info)
    assert "€77" in " ".join(m.value for m in at.markdown)


def test_save_step_merges_into_the_newest_file_so_parallel_fetches_never_conflict(tmp_path, monkeypatch):
    """31 Oct: two fetches started from the same commit; the second's rebase conflicted on routes.csv and its 311 rows were lost.
    Now each job keeps its own rows and merges them into whatever routes.csv is newest when it saves."""
    out, d1, now = tmp_path / "routes.csv", (date.today() + timedelta(5)).isoformat(), datetime.now().isoformat()
    monkeypatch.setattr(fetch_route, "OUT", out)
    row = lambda o, d, p: dict(day=d1, orig=o, dest=d, cabin="economy", price=p, source="calendar", fetched=now, nonstop=1)
    fetch_route.write_new([row("ATH", "BIO", 97.0)], tmp_path / "a.csv")
    fetch_route.write_new([row("BIO", "ATH", 91.0)], tmp_path / "b.csv")
    fetch_route.save(fetch_route.read(tmp_path / "a.csv"))  # the first job saves
    fetch_route.save(fetch_route.read(tmp_path / "b.csv"))  # the second merges into the file with the first one's rows in it
    rows = fetch_route.read(out)
    assert {(r["orig"], r["dest"], r["price"]) for r in rows} == {("ATH", "BIO", "97.0"), ("BIO", "ATH", "91.0")}
    wf = open(os.path.join(HERE, ".github", "workflows", "fetch-route.yml")).read()
    assert "--merge" in wf and "pull --rebase" not in wf and "fromJSON(needs.plan.outputs.legs)" in wf  # one job per leg, merged saves


def test_fetch_hotel_checks_the_search_and_fetches_calendar_booking_and_google(monkeypatch, tmp_path):
    import fetch_hotel
    with pytest.raises(ValueError):
        fetch_hotel.search({"QUERY": "x", "FILT": "y", "NIGHTS": "99", "RANGES": "2026-11-01,2026-11-02"})
    d1 = date.today() + timedelta(1)
    s = fetch_hotel.search({"QUERY": "Hotel Zurich", "FILT": "Baur au Lac", "HKEY": "https://www.tripadvisor.com/x-g188113-d228996-y",
                            "NIGHTS": "1", "ADULTS": "2", "RANGES": f"{d1},{d1 + timedelta(1)}", "BOOKING": "1"})
    assert s["hkey"] == "g188113-d228996" and s["ranges"] == [(d1, d1 + timedelta(1))]
    asked = []

    def google(a, b, q, f, n, ad, skip=()):
        asked.append(sorted(skip))
        for i in range(2):
            d = a + timedelta(i)
            if d.isoformat() in skip:
                continue
            ok = i == 0 or len(asked) > 1  # day 2 fails the first time (a rate limit), then works
            yield d, "Baur au Lac", 600.0 + i if ok else None, "ok" if ok else "error", None if ok else "HTTP 429"
    heat = lambda k, a, b: {d1.isoformat(): "cheap", (d1 + timedelta(1)).isoformat(): "high"}
    rate = lambda k, d, n, adults: 550.0 if adults == 2 else None  # the search's adults are sent: a room for two
    monkeypatch.setattr(fetch_hotel.time, "sleep", lambda s: None)
    rows = fetch_hotel.fetch(s, google=google, rate=rate, heat=heat)
    got = {(r["source"], r["day"]): (r["price"], r["tier"]) for r in rows}
    assert got[("heat", d1.isoformat())] == ("", "cheap") and got[("booking", d1.isoformat())] == (550.0, "")
    assert ("booking", (d1 + timedelta(1)).isoformat()) not in got  # Booking.com only on cheap days
    assert got[("google", (d1 + timedelta(1)).isoformat())] == (601.0, "") and asked[1] == [d1.isoformat()]  # retried, day 1 skipped
    out = tmp_path / "hotel_searches.csv"
    fetch_hotel.save(rows, out)
    fetch_hotel.save([dict(rows[0], query="Other hotel")], out)  # a second search merges in, the first stays
    assert len(fetch_hotel.read(out)) == len(rows) + 1


def test_hotels_page_fetches_only_missing_check_ins_on_github(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import hotels
    monkeypatch.chdir(tmp_path)
    t = db.connect(str(tmp_path / "tracker.db"))
    for i in range(1, 6):  # the first 5 check-ins of the default search are saved already
        t.execute("INSERT INTO hotel VALUES(?,?,?,?,?,?,?,?)", ((date.today() + timedelta(i)).isoformat(), "Steigenberger Frankfurt",
                                                               "Frankfurter Hof", 1, "Steigenberger", 200.0, datetime.now().isoformat(), 2))
    t.commit()
    st_nights = 1  # the page opens on 2 nights; these rows are one-night stays
    started = []
    monkeypatch.setattr(gh, "token", lambda: "tok")
    monkeypatch.setattr(gh, "fetch_hotel", lambda tok, *a: started.append(a) or "h1")
    monkeypatch.setattr(gh, "run", lambda tok, request, workflow="fetch-route.yml": None)
    monkeypatch.setattr(hotels, "fetch_range", lambda *a, **k: pytest.fail("must not search from the app's own server"))
    at = AppTest.from_file(os.path.join(HERE, "views", "hotel.py"), default_timeout=60).run()
    at.text_input(key="ho-link").set_value("g187337-d202275")  # the field is empty by default now: this hotel's key, pasted
    at.number_input(key="ho-nights").set_value(st_nights).run()
    at.button[0].click().run()  # Update prices
    assert not at.exception and len(started) == 1
    query, filt, hkey, nights, adults, ranges, booking = started[0]
    d = lambda i: date.today() + timedelta(i)
    assert (query, filt, hkey, nights, adults) == ("Steigenberger Frankfurt", "Frankfurter Hof", "g187337-d202275", 1, 2)
    assert ranges == [(d(6), d(30))]  # only the check-ins not saved yet
    info = " ".join(i.value for i in at.info)
    assert "Searching on GitHub" in info and "already saved" not in info


def far_month(today):
    """(Period, its index in trip_plan.months) of a month about seven months ahead: nothing is saved there in the seeded databases."""
    import trip_plan as tp
    periods = tp.months(today, today + timedelta(30))
    i = next(i for i, p in enumerate(periods) if p.start > today + timedelta(200))
    return periods[i], i


def test_overview_fetches_a_month_with_no_fares_on_github(tmp_path, monkeypatch):
    from test_overview import pages, seed
    open_page = pages(tmp_path, monkeypatch, None)
    t = seed(tmp_path / "tracker.db")  # the daily legs, 30 days
    today = date.today()
    p, i = far_month(today)
    p = tp.period(p.start, p.end, today, today)  # picked as two dates in "Leaving between": labelled "1–30 Jun"
    started, state = [], {"run": None}
    monkeypatch.setattr(gh, "token", lambda: "tok")
    monkeypatch.setattr(gh, "fetch", lambda tok, legs, nonstop: started.append((legs, nonstop)) or "abcd1234")
    monkeypatch.setattr(gh, "run", lambda tok, request, workflow="fetch-route.yml": state["run"])
    monkeypatch.setattr(collector, "fetch_range", lambda *a, **k: pytest.fail("must not search from the app's own server"))
    at = open_page("overview.py").run()
    assert not at.exception
    assert at.date_input(key="ov-when").value == (today + timedelta(1), today + timedelta(30))  # the next 30 days by default
    assert not [b for b in at.button if b.key.startswith("fetch-ahead")]  # the next 30 days are saved: nothing to fetch
    at.date_input(key="ov-when").set_value((p.start, p.end)).run()  # a month nobody has fetched yet
    assert not at.exception and f"No economy fares saved from Athens to Frankfurt for {p.label} yet" in at.info[0].value
    assert not [m for m in at.markdown if 'class="pass"' in m.value]  # no answer to give for that month
    key = f"ahead|ATH|FRA|{p.start}"
    assert at.button(key=f"fetch-{key}").label == f"Search {p.label} on GitHub"
    at.button(key=f"fetch-{key}").click().run()
    assert not at.exception and started == [([("ATH", "FRA", p.start, p.end), ("FRA", "ATH", p.start + timedelta(2), p.end + timedelta(7))], True)]
    assert "Searching on GitHub" in " ".join(x.value for x in at.info) and not [b for b in at.button if b.key.startswith("fetch-ahead")]
    # GitHub finished: the fares are in the database, the grid shows them and the button is gone
    now = datetime.now().isoformat()
    for o, d in (("ATH", "FRA"), ("FRA", "ATH")):
        for j in range((p.end - p.start).days + 8):
            t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", ((p.start + timedelta(j)).isoformat(), o, d, "economy", 99.0, "calendar", now, 1))
    t.commit()
    state["run"] = dict(status="completed", conclusion="success", url="u")
    at.run()
    assert not at.exception and "Searching on GitHub" not in " ".join(x.value for x in at.info)
    from test_overview import grid
    at.button(key="ov-grid-more-ATHFRA").click().run()  # the grid shows a week first; the button opens the whole month
    assert len(grid(at)) >= 28 and not [b for b in at.button if b.key.startswith("fetch-ahead")]
    assert "€198" in " ".join(m.value for m in at.markdown if 'class="pass"' in m.value)  # and the pass answers for that month
    # without a token nothing can be fetched from here: say so, and where to search
    monkeypatch.setattr(gh, "token", lambda: None)
    at = open_page("overview.py").run()
    at.selectbox(key="ov-grid-dest-ATH").set_value("ZRH").run()  # Zurich has nothing that far either
    at.date_input(key="ov-when").set_value((p.start, p.end)).run()
    assert not at.exception and not [b for b in at.button if b.key.startswith("fetch-ahead")]
    assert f"{p.name.capitalize()} from Athens to Zurich is not fetched yet. Search those dates on the Flights page" in " ".join(c.value for c in at.caption)


def test_expert_and_anywhere_fetch_a_month_with_no_fares_on_github(tmp_path, monkeypatch):
    import expert_chat as ex
    from test_overview import pages, seed
    open_page = pages(tmp_path, monkeypatch, "test")
    t = seed(tmp_path / "tracker.db")
    today = date.today()
    p, i = far_month(today)
    p = tp.period(p.start, p.end, today, today)  # picked as two dates in "Leaving between"
    started = []
    monkeypatch.setattr(gh, "token", lambda: "tok")
    monkeypatch.setattr(gh, "fetch", lambda tok, legs, nonstop, airline="A3": (started.append(legs) if airline == "A3" else None) or "abcd1234")
    monkeypatch.setattr(gh, "run", lambda tok, request, workflow="fetch-route.yml": None)
    monkeypatch.setattr(collector, "fetch_range", lambda *a, **k: pytest.fail("must not search from the app's own server"))
    monkeypatch.setattr(ex, "briefing", lambda f, cfg, day: ("- ok", None))
    monkeypatch.setattr(ex, "chat", lambda history, facts, cfg: pytest.fail("nothing to plan with"))
    at = open_page("expert.py").run()
    assert not at.exception and at.date_input(key="plan-when").value == (today + timedelta(1), today + timedelta(30))
    assert not [b for b in at.button if b.label.startswith("Search")]  # the window on screen is saved: nothing to fetch
    at.date_input(key="plan-when").set_value((p.start, p.end)).run()
    assert not at.exception
    at.button(key=f"fetch-ahead|ATH|FRA|{p.start}").click().run()  # 2 to 7 nights by default, as on the Overview: the way back 2 to 7 days later
    assert not at.exception and started == [[("ATH", "FRA", p.start, p.end), ("FRA", "ATH", p.start + timedelta(2), p.end + timedelta(7))]]
    assert "Searching on GitHub" in " ".join(x.value for x in at.info)
    started.clear()
    at = open_page("overview.py").run()  # Anywhere: one button fetches the month for every route from here
    at.selectbox(key="ov-grid-dest-ATH").set_value("ANY").run()
    at.date_input(key="ov-when").set_value((p.start, p.end)).run()
    assert not at.exception and f"No economy fares saved from Athens for {p.label}" in at.info[0].value
    assert at.button(key=f"fetch-ahead|ATH|all|{p.start}").label == "Search all routes from Athens on GitHub"
    at.button(key=f"fetch-ahead|ATH|all|{p.start}").click().run()  # one request: every route from Athens, both ways
    assert not at.exception and started == [[("ATH", "FRA", p.start, p.end), ("ATH", "ZRH", p.start, p.end),
                                             ("FRA", "ATH", p.start, p.end), ("ZRH", "ATH", p.start, p.end)]]
    assert "Searching on GitHub" in " ".join(x.value for x in at.info)
    # the fetch is in: Anywhere lists Frankfurt for that month, and the expert plans from it (the Overview grid is covered above)
    now = datetime.now().isoformat()
    for o, d in (("ATH", "FRA"), ("FRA", "ATH")):
        for j in range((p.end - p.start).days + 4):
            t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", ((p.start + timedelta(j)).isoformat(), o, d, "economy", 88.0, "calendar", now, 1))
    t.commit()
    at = open_page("overview.py")
    at.session_state["ov-grid-dest-ATH"], at.session_state["ov-when"] = "ANY", (p.start, p.end)
    at.run()
    assert not at.exception
    cards = next(m.value for m in at.markdown if 'class="fares"' in m.value).split('<div class="fare">')[1:]
    assert len(cards) == 1 and "Frankfurt" in cards[0] and "€176" in cards[0] and not [b for b in at.button if b.key.startswith("fetch-ahead")]
    seen = []
    monkeypatch.setattr(ex, "chat", lambda history, facts, cfg: (seen.append((history, facts)), iter(["Answer"]))[1])
    at = open_page("expert.py")
    at.session_state["plan-when"] = (p.start, p.end)
    at.run()
    assert not at.exception and not [b for b in at.button if b.label.startswith("Search")]
    at.chat_input[0].set_value("Why then?").run()  # the chat reads that month's facts: the recommended trip is €88 out and €88 back
    assert not at.exception and seen[-1][0][-1]["content"] == "Why then?"
    assert f"departures {p.start:%a %d %b %Y}" in seen[-1][1] and "Recommended trip (the one the Overview's boarding pass shows): leave" in seen[-1][1]
    assert "€176." in seen[-1][1]


def test_plan_splits_the_refresh_into_matrix_jobs_by_airline_a_few_routes_each(tmp_path, monkeypatch):
    """The daily run refreshes the tracked routes in matrix jobs (daily.yml), each on its own server with its own share of Google's rate
    limit, so the number of routes is no longer bounded by what one server finishes before the timeout."""
    import collect_daily
    out, today = tmp_path / "routes.csv", date.today()
    monkeypatch.setattr(fetch_route, "OUT", out)
    monkeypatch.setattr(fetch_route, "TRACK_MAX", 5)
    lines = ["orig,dest,nonstop,last_requested,from,to,airline", f"ATH,FRA,1,{today},,,A3",  # a daily leg: only beyond the daily run's 90 days
             f"OLD,ONE,1,{today - timedelta(61)},,,A3",  # not searched for over 60 days: dropped
             f"ATH,BIO,1,{today - timedelta(60)},,,A3",  # the oldest of 6 recent routes, cap 5: skipped
             f"ATH,HER,1,{today},,,GQ", f"ATH,HER,1,{today},,,A3", f"BIO,ATH,0,{today},,,A3", f"SKG,LCA,1,{today},,,A3",
             f"LCA,SKG,1,{today},{today + timedelta(100)},{today + timedelta(130)},A3"]  # planned past 90 days: up to the day asked
    (tmp_path / "tracked_routes.csv").write_text("\n".join(lines) + "\n")
    assert ("ATH", "FRA") in collect_daily.LEGS
    d = lambda i: (today + timedelta(i)).isoformat()
    assert fetch_route.plan(per_job=2) == [
        dict(airline="A3", nonstop="0", legs=f"BIO,ATH,{d(1)},{d(90)}"),
        dict(airline="A3", nonstop="1", legs=f"ATH,HER,{d(1)},{d(90)};LCA,SKG,{d(1)},{d(130)}"),
        dict(airline="A3", nonstop="1", legs=f"SKG,LCA,{d(1)},{d(90)}"),
        dict(airline="GQ", nonstop="1", legs=f"ATH,HER,{d(1)},{d(90)}")]  # one airline and nonstop flag per job, at most per_job routes
    assert not out.exists() and not (tmp_path / "history_prices.csv").exists()  # planning fetches nothing
    assert len(fetch_route.plan()) == 3  # 8 per job by default: A3 nonstop, A3 one-stop, GQ
    (tmp_path / "tracked_routes.csv").write_text(lines[0] + "\n")
    assert fetch_route.plan() == [] and json.dumps(fetch_route.plan()) == "[]"  # nothing tracked: no refresh jobs (daily.yml skips them)
    wf = open(os.path.join(HERE, ".github", "workflows", "daily.yml")).read()
    jobs = wf.index("\n  collect:"), wf.index("\n  refresh:"), wf.index("\n  finish:")
    assert jobs[0] < jobs[1] < jobs[2]
    collect, refresh, finish = wf[jobs[0]:jobs[1]], wf[jobs[1]:jobs[2]], wf[jobs[2]:]
    assert "python collect_daily.py" in collect and "--daily" in collect and "fetch_route.py --plan" in collect and "git push" in collect
    assert "include: ${{ fromJSON(needs.collect.outputs.refresh) }}" in refresh and "needs.collect.outputs.refresh != '[]'" in refresh
    assert 'python fetch_route.py --refresh "$LEGS" "$NONSTOP"' in refresh and "--merge-refresh" in refresh and "pull --rebase" not in refresh
    assert "LEGS: ${{ matrix.legs }}" in refresh and "AIRLINE: ${{ matrix.airline }}" in refresh and "fail-fast: false" in refresh
    assert "git fetch -q origin main && git reset -q --hard origin/main" in refresh  # merge into the newest main, like fetch-route.yml
    assert "needs: [collect, refresh]" in finish and "python digest.py" in finish and "python alerts.py" in finish
    assert finish.index("python digest.py") < finish.index("python alerts.py") < finish.index("gh issue create")
    assert "python digest.py" not in collect and "alerts.py" not in collect  # they read every job's fares, so they come after the refresh
    assert "needs.collect.outputs.missing == 'failure'" in finish  # the run is still marked red when daily days are missing


def test_a_refresh_job_only_writes_its_rows_and_merge_refresh_saves_them_without_tracking(tmp_path, monkeypatch):
    out, d1, today = tmp_path / "routes.csv", (date.today() + timedelta(3)).isoformat(), date.today().isoformat()
    monkeypatch.setattr(fetch_route, "OUT", out)
    (tmp_path / "tracked_routes.csv").write_text(f"orig,dest,nonstop,last_requested,from,to,airline\nATH,BIO,1,2026-01-01,,,A3\n")
    asked = []

    def fake(a, b, o, d, skip=(), nonstop=True, **kw):
        asked.append((o, d, nonstop, kw.get("airline")))
        yield a, "economy", 120.0, "calendar", "ok", None
    monkeypatch.setattr(fetch_route, "fetch_range", fake)
    new = tmp_path / "new_rows.csv"
    assert fetch_route.refresh(f"ATH,BIO,{d1},{d1};BIO,ATH,{d1},{d1}", True, new, "A3") == 0
    assert [(o, d, n) for o, d, n, _ in asked] == [("ATH", "BIO", True), ("BIO", "ATH", True)]
    rows = fetch_route.read(new)
    assert [(r["orig"], r["dest"], r["price"]) for r in rows] == [("ATH", "BIO", "120.0"), ("BIO", "ATH", "120.0")]
    assert not out.exists() and not (tmp_path / "history_prices.csv").exists()  # the save step does that, into the newest main
    fetch_route.save(rows)  # --merge-refresh: saved and in the history, but not a request: ATH-BIO keeps its old last_requested
    assert fetch_route.log_history(rows) == 2
    assert {(r["orig"], r["dest"]) for r in fetch_route.read(out)} == {("ATH", "BIO"), ("BIO", "ATH")}
    assert (tmp_path / "tracked_routes.csv").read_text().splitlines()[1:] == ["ATH,BIO,1,2026-01-01,,,A3"]
    asked.clear()
    assert fetch_route.refresh(f"ATH,HER,{d1},{d1}", False, new, "GQ") == 0 and asked[0][2] is False and asked[0][3].name == "GQ"
    src = open(os.path.join(HERE, "fetch_route.py")).read()
    block = src[src.index('elif sys.argv[1] == "--merge-refresh"'):src.index('elif sys.argv[1] == "--plan"')]
    assert "save(rows, airline=airline)" in block and "log_history(rows, airline)" in block and "track=True" not in block
