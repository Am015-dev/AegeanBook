"""Flight times under the boarding pass: the script the workflow runs, the app's GitHub call, and the Overview's lines and button."""
import json
import os
from datetime import date, datetime, timedelta

import pytest

import fetch_details
import flight_times
import gh

HERE = os.path.dirname(os.path.abspath(__file__))
D1 = date.today() + timedelta(40)
ROW = "day,orig,dest,cabin,flight,depart,arrive,minutes,price,fetched\n"


def test_parse_validates_and_fetch_retries_a_day_once(tmp_path, monkeypatch):
    assert fetch_details.parse(f"ath,ist,{D1};IST,ATH,{D1}", "economy") == [("ATH", "IST", D1), ("IST", "ATH", D1)]
    for legs, cabin in ((f"ATH,IST;rm -rf,{D1}", "economy"), (f"ATH,IST,{D1}", "first"), (f"ATH,IST,{D1}x", "economy")):
        with pytest.raises(ValueError):
            fetch_details.parse(legs, cabin)
    calls = []

    def fetch_day(o, d, cabin, day):
        calls.append(o)
        if len(calls) == 1:
            raise RuntimeError("429")
        return [dict(day=day.isoformat(), orig=o, dest=d, cabin=cabin, flight="A3 992", depart="09:25", arrive="10:55", minutes=90, price=53)]
    monkeypatch.setattr(fetch_details, "fetch_day", fetch_day)
    monkeypatch.setattr(fetch_details.time, "sleep", lambda s: None)
    new = tmp_path / "new.csv"
    assert fetch_details.main(f"ATH,IST,{D1}", "economy", str(new)) == 0 and calls == ["ATH", "ATH"]
    rows = fetch_details.read(new)
    assert len(rows) == 1 and rows[0]["flight"] == "A3 992" and rows[0]["fetched"]


def test_merge_upserts_by_flight_and_drops_old_and_past_rows(tmp_path):
    out = tmp_path / "details.csv"
    now, old = datetime.now().isoformat(), (datetime.now() - timedelta(days=31)).isoformat()
    out.write_text(ROW + f"{D1},ATH,IST,economy,A3 992,09:25,10:55,90,53,{now}\n"  # replaced by the new price
                   f"{D1},ATH,IST,economy,A3 994,18:00,19:30,90,80,{now}\n"  # kept
                   f"{D1},ATH,IST,business,A3 992,09:25,10:55,90,200,{old}\n"  # too old: dropped
                   f"2020-01-01,ATH,IST,economy,A3 992,09:25,10:55,90,53,{now}\n")  # past: dropped
    fetch_details.save([dict(day=D1.isoformat(), orig="ATH", dest="IST", cabin="economy", flight="A3 992", depart="09:30", arrive="11:00",
                             minutes=90, price=60, fetched=now)], out)
    rows = fetch_details.read(out)
    assert [(r["flight"], r["price"], r["depart"]) for r in rows] == [("A3 992", "60", "09:30"), ("A3 994", "80", "18:00")]


def test_github_dispatch_body(monkeypatch):
    sent = []

    class Answer:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return self.data
    monkeypatch.setattr(gh.urllib.request, "urlopen", lambda req, timeout: sent.append(req) or Answer(b""))
    request = gh.fetch_details("tok", [("ATH", "IST", D1), ("IST", "ATH", D1 + timedelta(6))], "business")
    body = json.loads(sent[0].data)
    assert sent[0].full_url.endswith("/actions/workflows/fetch-details.yml/dispatches") and body["ref"] == "main"
    assert body["inputs"] == {"legs": f"ATH,IST,{D1};IST,ATH,{D1 + timedelta(6)}", "cabin": "business", "request": request}


def test_render_shows_the_lines_or_a_button_that_asks_github(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    back = D1 + timedelta(6)
    csv_path = tmp_path / "details.csv"
    csv_path.write_text(ROW + f"{D1},ATH,IST,economy,A3 994,18:00,19:30,90,80,x\n{D1},ATH,IST,economy,A3 992,09:25,10:55,90,53,x\n"
                        f"{back},IST,ATH,economy,A3 993,12:00,13:25,85,61,x\n")
    monkeypatch.setattr(flight_times, "LOCAL", str(csv_path))
    page = tmp_path / "page.py"
    page.write_text(f"import flight_times\nfrom datetime import date\n"
                    f"flight_times.render('ATH', 'IST', 'economy', date.fromisoformat('{D1}'), date.fromisoformat('{back}'), 'details-test')\n")
    monkeypatch.setattr(gh, "token", lambda: None)
    at = AppTest.from_file(str(page)).run()
    assert not at.exception and len(at.caption) == 1 and not at.button
    text = at.caption[0].value
    assert text.index(f"Out · {D1:%a} {D1.day} {D1:%b}: A3 992 09:25 → 10:55, 1 h 30 · €53") < text.index("A3 994 18:00")  # cheapest first
    assert f"Back · {back:%a} {back.day} {back:%b}: A3 993 12:00 → 13:25, 1 h 25 · €61" in text and "Google's lowest fare" in text

    # business is not known: with a token, a button that asks GitHub for both days; without one, nothing
    page.write_text(page.read_text().replace("'economy'", "'business'"))
    at = AppTest.from_file(str(page)).run()
    assert not at.exception and not at.caption and not at.button
    asked = []
    monkeypatch.setattr(gh, "token", lambda: "tok")
    monkeypatch.setattr(gh, "fetch_details", lambda tok, legs, cabin: asked.append((legs, cabin)) or "abcd1234")
    monkeypatch.setattr(gh, "run", lambda tok, request, workflow: None)
    at = AppTest.from_file(str(page)).run()
    assert at.button[0].label == "Show flight times"
    at = at.button[0].click().run()
    assert asked == [([("ATH", "IST", D1), ("IST", "ATH", back)], "business")] and not at.exception
    assert at.session_state["gh-details-test"][0] == "abcd1234" and "flight times" in at.info[0].value
    assert not at.run().button  # while GitHub works, the button is gone
