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
    assert body["inputs"] == {"legs": "ATH,BIO,2026-11-03,2026-12-02", "nonstop": "1", "request": request}
    assert sent[0].headers["Authorization"] == "Bearer tok"
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


def test_update_prices_fetches_on_github_and_shows_the_results(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.chdir(tmp_path)
    started, state = [], {"run": None}
    monkeypatch.setattr(gh, "token", lambda: "tok")
    monkeypatch.setattr(gh, "fetch", lambda tok, legs, nonstop: started.append(legs) or "abcd1234")
    monkeypatch.setattr(gh, "run", lambda tok, request: state["run"])
    monkeypatch.setattr(collector, "fetch_range", lambda *a, **k: pytest.fail("must not search from the app's own server"))
    at = AppTest.from_file(os.path.join(HERE, "views", "flights.py"), default_timeout=60).run()
    at.button[1].click().run()  # Update prices (the first button swaps the airports)
    assert not at.exception and started and started[0][0][:2] == ("ATH", "FRA")
    assert "Searching on GitHub" in " ".join(i.value for i in at.info)
    # GitHub finished: the fetched fares are in the database and the page shows them
    t = db.connect(str(tmp_path / "tracker.db"))
    d1 = (date.today() + timedelta(1)).isoformat()
    t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", (d1, "ATH", "FRA", "economy", 77.0, "calendar", datetime.now().isoformat(), 1))
    t.commit()
    state["run"] = dict(status="completed", conclusion="success", url="u")
    at.run()
    assert not at.exception and "Searching on GitHub" not in " ".join(i.value for i in at.info)
    assert "€77" in " ".join(m.value for m in at.markdown)
