"""Price alerts, offline: temporary data files behind the database, a fake `gh`, and the Alerts page under AppTest."""
import json
import os
import random
from datetime import date, datetime, timedelta

import pytest

import alerts
import db
import gh

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "views", "alerts.py")
NOW = datetime.now().isoformat()


def day(i):
    return (date.today() + timedelta(i)).isoformat()


def write(folder, name, header, rows):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        f.write(header + "\n" + "\n".join(rows) + "\n")


@pytest.fixture
def data(tmp_path, monkeypatch):
    """The database alerts.py reads, fed from CSV files written into tmp_path: data(name, header, rows) writes one (daily.csv,
    daily_award.csv, routes.csv); nothing else is loaded, and GitHub is never asked for newer copies."""
    for name in ("DAILY", "DAILY_HOTEL", "DAILY_AWARD", "ROUTES", "SKYEXPRESS", "HOTEL_SEARCHES"):
        monkeypatch.setattr(db, name, str(tmp_path / f"{name.lower()}.csv"))
    monkeypatch.setattr(gh, "data_file", lambda path, tok=None: None)

    def feed(name, header, rows):
        write(tmp_path, {"daily.csv": "daily.csv", "daily_award.csv": "daily_award.csv", "routes.csv": "routes.csv"}[name], header, rows)
    return feed


FARES = "day,orig,dest,cabin,price,source,fetched,nonstop"
fare = lambda i, o, d, cabin, price, nonstop=1: f"{day(i)},{o},{d},{cabin},{price},calendar,{NOW},{nonstop}"


def test_alert_from_the_app_link_is_read_back():
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(alerts.new_issue_link("ATH", "ZRH", "business", "eur", 250, "2026-12-01", "2026-12-31")).query)
    assert q["labels"] == ["price-alert"] and q["title"] == ["Alert: ATH → ZRH business one way below €250"]
    assert alerts.parse(q["body"][0]) == dict(orig="ATH", dest="ZRH", cabin="business", unit="eur", limit=250.0, trip="oneway", nights=(2, 7),
                                              start="2026-12-01", end="2026-12-31", email="", ntfy="", code="")
    q = parse_qs(urlparse(alerts.new_issue_link("ATH", "FRA", "economy", "eur", 150, trip="return", nights=(3, 5))).query)
    assert q["title"] == ["Alert: ATH → FRA economy return below €150"]
    a = alerts.parse(q["body"][0])
    assert a["trip"] == "return" and a["nights"] == (3, 5) and a["start"] == "" and "trip: return\nnights: 3-5" in q["body"][0]
    assert alerts.parse("route: ath → fra\ncabin: Economy\nmiles below: 9,500")["unit"] == "miles"  # hand-written works too
    assert alerts.parse("route: ATH-FRA\ncabin: economy\nbelow: 100\ntrip: Return")["nights"] == (2, 7)  # a return without nights: 2 to 7
    assert alerts.parse("route: ATH-ZRH\ncabin: first\nbelow: 100") is None  # no such cabin
    assert alerts.describe(a, "in December 2026") == "Athens → Frankfurt economy drops below €150 return (3–5 nights), leaving in December 2026"
    assert alerts.describe(dict(a, trip="oneway", unit="miles", limit=16000)) == "Athens → Frankfurt economy drops below 16,000 miles one way"


def test_one_way_alert_matches_once_then_only_when_cheaper(data):
    data("daily.csv", FARES, [fare(3, "ATH", "ZRH", "business", 240), fare(4, "ATH", "ZRH", "business", 260),
                              fare(5, "ATH", "ZRH", "business", 230, nonstop=0),  # not nonstop: ignored
                              fare(-1, "ATH", "ZRH", "business", 100),  # in the past: ignored
                              fare(6, "ATH", "ZRH", "economy", 90)])
    a = alerts.parse("route: ATH-ZRH\ncabin: business\nbelow: 250")
    hits = alerts.matches(a)
    assert hits == [dict(depart=date.today() + timedelta(3), back=None, nights=None, price=240.0)]
    first = alerts.message(a, hits, None)
    assert "**Athens → Zurich** is now €240 one way in business, below your €250: leave " in first and "<!-- alert best: 240.0 -->" in first
    assert "Book: https://en.aegeanair.com/" in first and "TravelType=O" in first
    assert alerts.message(a, hits, 240.0) is None  # the same news is not told twice
    assert "cheaper than the €245 I told you about" in alerts.message(a, hits, 245.0)
    assert alerts.matches(dict(a, start=day(4))) == []  # date range respected
    assert alerts.matches(dict(a, limit=240)) == []  # "below €240" means strictly below: the pass's own price as the limit does not fire


def test_return_alert_watches_the_return_total_for_the_nights_asked(data):
    import pandas as pd
    import deals
    out = {3: 100, 4: 60, 5: 100, 10: 90}  # ATH -> FRA by day ahead
    back = {6: 80, 7: 70, 11: 65, 20: 20, 30: 60}  # FRA -> ATH
    data("daily.csv", FARES, [fare(i, "ATH", "FRA", "economy", p) for i, p in out.items()] + [fare(i, "FRA", "ATH", "economy", p) for i, p in back.items()])
    a = alerts.parse("route: ATH-FRA\ncabin: economy\nbelow: 150\ntrip: return\nnights: 2-7")
    hits = alerts.matches(a)
    t = date.today()
    # out day 4 (€60) with back day 11 (€65, 7 nights) = €125, day 7 (€70, 3 nights) = €130, day 6 (€80, 2 nights) = €140; day 10 + day 11 is one night
    assert [(h["depart"] - t).days for h in hits] == [4, 4, 4] and [h["nights"] for h in hits] == [7, 3, 2] and [h["price"] for h in hits] == [125.0, 130.0, 140.0]
    assert hits[0] == dict(depart=t + timedelta(4), back=t + timedelta(11), nights=7, price=125.0)
    # exactly what the Overview's boarding pass shows for the same fares, nights and window
    s = lambda d: pd.Series({day(i): float(p) for i, p in d.items()})
    assert deals.best_trip(s(out), s(back), t + timedelta(1), t + timedelta(90), range(2, 8)) == hits[0]
    assert alerts.matches(dict(a, nights=(1, 1))) == []  # one night: day 10 + day 11 is €155, not below 150
    assert alerts.matches(dict(a, nights=(1, 1), limit=160)) == [dict(depart=t + timedelta(10), back=t + timedelta(11), nights=1, price=155.0)]
    assert alerts.matches(dict(a, limit=61)) == []  # a €60 one-way is not what a return alert asked for: it never fires on a one-way
    assert alerts.matches(dict(a, trip="oneway", limit=61))[0]["price"] == 60.0  # the one-way alert does
    assert alerts.matches(dict(a, start=day(5), end=day(9))) == []  # the window is for the day out: days 4 and 10 are outside it
    assert alerts.matches(dict(a, start=day(8), end=day(12), nights=(10, 10), limit=200))[0]["back"] == t + timedelta(20)  # the way back may be later
    assert alerts.matches(dict(a, start=day(8), end=day(12), nights=(20, 20), limit=200))[0]["back"] == t + timedelta(30)


def test_trips_price_like_deals_best_trip():
    """The daily run has no pandas, so alerts.trips redoes deals.best_trip's arithmetic: the same answer on random fares with gaps."""
    import pandas as pd
    import deals
    start, end = date(2027, 3, 1), date(2027, 3, 31)
    rnd = random.Random(7)
    for _ in range(25):
        out = {(start + timedelta(i)).isoformat(): float(rnd.choice([80, 90, 120, 150, 200])) for i in range(31) if rnd.random() < 0.6}
        back = {(start + timedelta(i)).isoformat(): float(rnd.choice([70, 90, 110, 160])) for i in range(45) if rnd.random() < 0.6}
        lo = rnd.randint(1, 5)
        hi = lo + rnd.randint(0, 6)
        for b in (back, None):
            mine = alerts.trips(out, b, start, end, (lo, hi))
            theirs = deals.best_trip(pd.Series(out, dtype=float), None if b is None else pd.Series(b, dtype=float), start, end, range(lo, hi + 1))
            if b is not None and not any(t["back"] for t in mine):
                continue  # no return possible: best_trip then names a one-way, which a return alert must not
            assert (mine[0] if mine else None) == theirs, (out, b, lo, hi)


def test_message_is_a_sentence_a_person_understands():
    a = alerts.parse("route: ATH-FRA\ncabin: economy\nbelow: 150\ntrip: return\nnights: 2-7\ncode: s3cret")
    hits = [dict(depart=date(2026, 11, 19), back=date(2026, 11, 21), nights=2, price=139.0),
            dict(depart=date(2026, 11, 20), back=date(2026, 11, 23), nights=3, price=145.0)]
    msg = alerts.message(a, hits, None, alerts.link("stop", 5, "s3cret"))
    assert msg.startswith("**Athens → Frankfurt** is now €139 return in economy, below your €150: leave Thu 19 Nov, back Sat 21 Nov (2 nights). "
                          "Book: https://en.aegeanair.com/")
    assert "DateDeparture=19/11/2026" in msg and "DateReturn=21/11/2026" in msg and "TravelType=R" in msg
    assert "Other dates under €150:\n- leave Fri 20 Nov, back Mon 23 Nov (3 nights): €145" in msg
    assert "Stop this alert: https://aegeanbook-fdb67dfpcw4qxu8t2ornzt.streamlit.app/alerts?stop=5&code=s3cret" in msg
    plain = alerts._plain(msg)
    assert "**" not in plain and "<!--" not in plain and plain.startswith("Athens → Frankfurt is now €139 return")
    title = alerts._ascii(a, hits[0])
    assert title == "ATH-FRA: EUR 139 return, below your EUR 150" and title.isascii()
    m = alerts.message(dict(a, unit="miles", limit=20000, cabin="business"), [dict(hits[0], price=16935.0)], None)
    assert "is now 16,935 miles return in business, below your 20,000 miles" in m and "Close this issue to stop the alert." in m


def test_run_comments_on_the_alerts_that_are_met(data, monkeypatch):
    data("daily.csv", FARES, [fare(3, "ATH", "FRA", "economy", 61)])
    data("daily_award.csv", "day,orig,dest,cabin,miles,nonstop,fetched", [f"{day(3)},ATH,FRA,business,16935,1,{NOW}"])
    issues = [dict(number=1, title="a", body="route: ATH-FRA\ncabin: economy\nbelow: 70", comments=[]),
              dict(number=2, title="b", body="route: ATH-FRA\ncabin: economy\nbelow: 70",
                   comments=[{"body": "old news <!-- alert best: 61.0 -->"}]),
              dict(number=3, title="c", body="route: ATH-FRA\ncabin: business\nmiles below: 20000", comments=[]),
              dict(number=4, title="d", body="just a note", comments=[]),
              dict(number=5, title="e", body="route: ATH-FRA\ncabin: economy\nbelow: 70\ntrip: return", comments=[])]  # no fares back
    calls = []

    def fake(*args):
        calls.append(args)
        return json.dumps(issues) if args[:2] == ("issue", "list") else ""
    monkeypatch.setattr(alerts, "gh", fake)
    alerts.run()
    commented = [a[2] for a in calls if a[:2] == ("issue", "comment")]
    assert commented == ["1", "3"]  # #2 was already told €61, #4 is not an alert, #5 wants a return and only the way out is saved
    assert "16,935 miles" in [a for a in calls if a[:3] == ("issue", "comment", "3")][0][4]


def test_visitor_alerts_phone_and_email_with_confirmation(data, monkeypatch):
    data("daily.csv", FARES, [fare(3, "ATH", "ZRH", "business", 240), fare(6, "ZRH", "ATH", "business", 200)])
    pushed, mailed = [], []
    monkeypatch.setattr(alerts, "push", lambda topic, title, text, click: pushed.append((topic, title, text, click)))
    monkeypatch.setattr(alerts, "mail", lambda to, subject, text: mailed.append((to, subject, text)))
    base = "route: ATH-ZRH\ncabin: business\nbelow: 450\ntrip: return\nnights: 2-4\ncode: s3cret\n"
    phone = dict(number=7, body=base + "ntfy: aegean-abc", labels=[{"name": "price-alert"}, {"name": "confirmed"}], comments=[])
    body = alerts.handle(phone, alerts.parse(phone["body"]))
    assert pushed[0][0] == "aegean-abc" and pushed[0][1] == "ATH-ZRH: EUR 440 return, below your EUR 450"
    assert "Athens → Zurich is now €440 return in business" in pushed[0][2] and "stop=7&code=s3cret" in pushed[0][2]
    assert pushed[0][3].startswith("https://en.aegeanair.com/") and "TravelType=R" in pushed[0][3]  # one tap books it
    assert "<!-- alert best: 440.0 -->" in body and "<!--" not in pushed[0][2]  # remembered in the body, not shown to the visitor
    assert alerts.handle(dict(phone, body=body), alerts.parse(body)) is None  # nothing new: no second notification
    email = dict(number=8, body=base + "email: a@b.co", labels=[{"name": "price-alert"}], comments=[])
    body = alerts.handle(email, alerts.parse(email["body"]))
    assert mailed[-1][0] == "a@b.co" and mailed[-1][1] == "Confirm your price alert: Athens → Zurich"
    assert "when Athens → Zurich business drops below €450 return (2–4 nights)" in mailed[-1][2]
    assert "confirm=8&code=s3cret" in mailed[-1][2] and "€440" not in mailed[-1][2]
    assert alerts.handle(dict(email, body=body), alerts.parse(body)) is None and len(mailed) == 1  # one confirmation, never again
    confirmed = dict(email, body=body, labels=email["labels"] + [{"name": "confirmed"}])
    alerts.handle(confirmed, alerts.parse(body))
    assert len(mailed) == 2 and "€440" in mailed[-1][2] and "stop=8&code=s3cret" in mailed[-1][2]


def test_alert_store_create_confirm_stop_and_list_mine(monkeypatch):
    import alert_store as st_
    issues, calls = {}, []

    def api(token, method, path, body=None):
        calls.append((method, path, body))
        if method == "GET" and path.startswith("/issues?"):
            return [i for i in issues.values() if i["state"] == "open"]  # the real API's state=open
        if method == "POST" and path == "/issues":
            n = len(issues) + 1
            issues[n] = dict(number=n, state="open", title=body["title"], body=body["body"], labels=[{"name": l} for l in body["labels"]])
            return issues[n]
        if method == "GET":
            return issues[int(path.split("/")[-1])]
        if method == "PATCH" and body and body.get("state") == "closed":
            issues[int(path.split("/")[-1])]["state"] = "closed"
        return {}
    monkeypatch.setattr(st_, "_api", api)
    n, code = st_.create("t", "ATH", "ZRH", "business", "eur", 250, email="A@B.co", trip="return", nights=(2, 5))
    assert "email: a@b.co" in issues[n]["body"] and f"code: {code}" in issues[n]["body"] and "a@b.co" not in issues[n]["title"]
    assert "trip: return\nnights: 2-5" in issues[n]["body"] and issues[n]["title"] == "Alert: ATH → ZRH business return below €250 (email)"
    assert [l["name"] for l in issues[n]["labels"]] == ["price-alert"]  # email waits for confirmation
    m, _ = st_.create("t", "ATH", "FRA", "economy", "eur", 70, ntfy=st_.topic())
    assert "confirmed" in [l["name"] for l in issues[m]["labels"]] and "trip:" not in issues[m]["body"]  # a phone topic is the visitor's own
    for bad, err in ((dict(email="nope"), "does not look right"), (dict(), "Choose where")):
        with pytest.raises(st_.AlertError, match=err):
            st_.create("t", "ATH", "ZRH", "business", "eur", 250, **bad)
    with pytest.raises(st_.AlertError, match="not valid"):
        st_.confirm("t", n, "wrong")
    assert st_.owner("t", n, "wrong") is None and st_.owner("t", n, code) == ("email", "a@b.co")
    mine = st_.mine("t", "email", "a@b.co")
    assert [a["number"] for a in mine] == [n] and mine[0]["code"] == code and not mine[0]["confirmed"]
    st_.confirm("t", n, code)
    issues[n]["labels"].append({"name": "confirmed"})
    assert st_.mine("t", "email", "a@b.co")[0]["confirmed"] and st_.mine("t", "email", "x@y.co") == []
    st_.stop("t", n, code)
    assert ("POST", f"/issues/{n}/labels", {"labels": ["confirmed"]}) in calls and calls[-1][0] == "PATCH"
    assert st_.mine("t", "email", "a@b.co") == []  # a stopped alert is closed, so it is no longer listed
    for _ in range(5):  # five alerts per address are fine, the sixth is refused
        st_.create("t", "ATH", "ZRH", "business", "eur", 250, email="x@y.co")
    with pytest.raises(st_.AlertError, match="already have 5"):
        st_.create("t", "ATH", "ZRH", "business", "eur", 250, email="x@y.co")


@pytest.fixture
def page(tmp_path, monkeypatch):
    """The Alerts page under AppTest on the seeded database (test_overview.seed: ATH-FRA economy €60 on day 10, €150-210 otherwise)."""
    from streamlit.testing.v1 import AppTest
    import test_overview
    for name in ("DAILY", "DAILY_HOTEL", "DAILY_AWARD"):
        monkeypatch.setattr(db, name, "/nonexistent.csv")
    monkeypatch.chdir(tmp_path)
    test_overview.seed(tmp_path / "tracker.db")

    def open_page(token="t", **params):
        at = AppTest.from_file(PAGE, default_timeout=60)
        if token:
            at.secrets["GITHUB_TOKEN"] = token
        for k, v in params.items():
            at.query_params[k] = v
        return at
    return open_page


def test_alerts_page_without_and_with_a_token(page, monkeypatch):
    import alert_store as store
    at = page(token=None).run()
    assert not at.exception and "not switched on yet" in at.info[0].value  # no token: the owner's GitHub link instead
    made = []
    monkeypatch.setattr(store, "create", lambda token, *a, **k: (made.append((a, k)), (12, "c0de"))[1])
    monkeypatch.setattr(store, "mine", lambda token, kind, contact: [] if not made else [
        dict(number=12, title="x", code="c0de", confirmed=False, body="route: ATH-FRA\ncabin: economy\nbelow: 210\ntrip: return\nnights: 2-7\n"
                                                                      "email: me@example.com\ncode: c0de")])
    monkeypatch.setattr(alerts, "mail", lambda *a: (_ for _ in ()).throw(RuntimeError("no SMTP in tests")))
    at = page()
    at.secrets.clear()
    at.secrets["github"] = {"token": "t"}  # any name with "github" in it, even inside a [section]
    at.run()
    assert not at.exception and "Made alerts before?" in at.caption[-1].value  # nothing proven yet: no list
    sentence = [m.value for m in at.markdown if "Tell me when" in m.value][0]
    assert "Tell me when Athens → Frankfurt economy drops below €210 return (2–7 nights), leaving in the next 90 days." in sentence
    assert "Cheapest now: €210 return" in " ".join(c.value for c in at.caption)  # €60 out on day 10, €150 back 4 nights later
    at.text_input(key="al-email").input("me@example.com")
    at.button[0].click().run()
    assert not at.exception and made[-1][1]["email"] == "me@example.com" and made[-1][0][:3] == ("ATH", "FRA", "economy")
    assert made[-1][1]["trip"] == "return" and made[-1][1]["nights"] == (2, 7) and made[-1][0][4] == 210
    assert "confirmation email goes to me@example.com" in at.success[0].value and "stop=12&code=c0de" in at.caption[-1].value
    # the alert made here is listed with a Stop button; stopping it uses its code
    assert "Your alerts" in " ".join(m.value for m in at.markdown)
    listed = [m.value for m in at.markdown if "drops below €210" in m.value and "Tell me" not in m.value][0]
    assert "once you open the link in the confirmation email" in listed
    stopped = []
    monkeypatch.setattr(store, "stop", lambda token, n, code: stopped.append((n, code)))
    at.button(key="al-stop-12").click().run()
    assert stopped == [(12, "c0de")] and not at.exception
    # a confirm link: the code proves the address is the visitor's, so the page lists every alert of that address
    monkeypatch.setattr(store, "confirm", lambda token, n, code: "ATH → FRA economy return below €60 (email)" if code == "ok" else
                        (_ for _ in ()).throw(store.AlertError("This link is not valid any more.")))
    monkeypatch.setattr(store, "owner", lambda token, n, code: ("email", "a@b.co") if code == "ok" else None)
    monkeypatch.setattr(store, "mine", lambda token, kind, contact: [dict(number=3, title="x", code="k", confirmed=True,
                                                                           body="route: FRA-ATH\ncabin: business\nbelow: 300\nemail: a@b.co\ncode: k")])
    at = page(confirm="12", code="ok").run()
    assert "is on" in at.success[0].value and "Frankfurt → Athens business drops below €300 one way" in " ".join(m.value for m in at.markdown)
    assert at.button(key="al-stop-3")
    at = page(confirm="12", code="bad").run()
    assert "not valid" in at.error[0].value and "Your alerts" not in " ".join(m.value for m in at.markdown)


def test_alerts_page_opens_prefilled_from_the_boarding_pass(page, monkeypatch):
    import alert_store as store
    made = []
    monkeypatch.setattr(store, "create", lambda token, *a, **k: (made.append((a, k)), (12, "c0de"))[1])
    monkeypatch.setattr(store, "mine", lambda token, kind, contact: [])
    monkeypatch.setattr(alerts, "mail", lambda *a: (_ for _ in ()).throw(RuntimeError("no SMTP in tests")))
    t = date.today()
    # everything the pass knows: the route, cabin, cash, its price, its dates, the nights slider and the days it searched
    at = page(route="ATH-FRA", cabin="economy", unit="eur", price="210", depart=day(10), back=day(14), nights="3-6", trip="return",
              when=f"{day(1)},{day(20)}").run()
    assert not at.exception
    assert at.selectbox(key="al-leg").value == ("ATH", "FRA") and at.button_group(key="al-cabin").value == "economy"
    assert at.button_group(key="al-unit").value == "eur" and at.button_group(key="al-trip").value == "return"
    assert at.slider(key="al-nights").value == (3, 6) and at.number_input(key="al-limit-ATHFRA-economy-eur-return").value == 210
    sentence = [m.value for m in at.markdown if "Tell me when" in m.value][0]
    assert f"drops below €210 return (3–6 nights), leaving between {t + timedelta(1):%-d %b} and {t + timedelta(20):%-d %b}." in sentence
    at.text_input(key="al-email").input("me@example.com")
    at.button[0].click().run()
    assert made[-1][0] == ("ATH", "FRA", "economy", "eur", 210, day(1), day(20)) and made[-1][1]["nights"] == (3, 6)
    # the first version of the link: route, cabin and unit only. The pass's defaults then: a return of 2 to 7 nights, any date, the limit
    # just under the cheapest trip now
    at = page(route="ZRH-ATH", cabin="business", unit="miles").run()
    assert not at.exception and at.selectbox(key="al-leg").value == ("ZRH", "ATH") and at.button_group(key="al-cabin").value == "business"
    assert at.button_group(key="al-unit").value == "miles" and at.button_group(key="al-trip").value == "return"
    assert at.slider(key="al-nights").value == (2, 7) and at.selectbox(key="al-when").value == 0
    assert "No return trips saved" in " ".join(c.value for c in at.caption)  # no award miles are seeded
    # the pass's dates without the nights slider: a stay as long as the pass's, in the month it leaves in; the price rounds to a euro
    at = page(route="ATH-FRA", cabin="economy", unit="eur", price="209.6", depart=day(10), back=day(14)).run()
    assert not at.exception and at.slider(key="al-nights").value == (4, 4)
    assert at.number_input(key="al-limit-ATHFRA-economy-eur-return").value == 210
    i = at.selectbox(key="al-when").value
    assert at.selectbox(key="al-when").format_func(i) == f"{t + timedelta(10):%B %Y}"
    # a one-way pass
    at = page(route="ATH-FRA", cabin="economy", unit="eur", price="60", depart=day(10)).run()
    assert not at.exception and at.button_group(key="al-trip").value == "oneway" and at.number_input(key="al-limit-ATHFRA-economy-eur-oneway").value == 60
    assert "drops below €60 one way, leaving " in [m.value for m in at.markdown if "Tell me when" in m.value][0]  # "in <month>" or "between" the month's remaining days
    # a route with nothing saved: the form opens on its defaults
    at = page(route="ATH-XXX", cabin="economy", unit="eur", price="1").run()
    assert not at.exception and at.selectbox(key="al-leg").value == ("ATH", "FRA")


def test_alerts_page_phone_sends_a_first_notification(page, monkeypatch):
    import urllib.request
    import alert_store as store
    made, sent = [], []
    monkeypatch.setattr(store, "create", lambda token, *a, **k: (made.append(k), (13, "c0de"))[1])
    monkeypatch.setattr(store, "mine", lambda token, kind, contact: [])
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: (sent.append(req), __import__("io").BytesIO(b"{}"))[1])
    at = page().run()
    at.button_group(key="al-how").set_value("phone").run()
    topic = at.code[0].value
    at.button[0].click().run()
    assert not at.exception and made[-1]["ntfy"] == topic and topic.startswith("aegean-")
    assert sent[-1].full_url == f"https://ntfy.sh/{topic}" and b"stop=13" in sent[-1].data and "first notification" in at.success[0].value
    assert f"No alerts are on for your phone (topic {topic})" in " ".join(c.value for c in at.caption)  # listed under Your alerts (fake store: none)


def test_daily_run_needs_no_pandas(monkeypatch):
    """daily.yml installs nothing for the alerts step: alerts.py must read the database and name cities with the standard library alone."""
    import importlib
    import sys
    for name in ("pandas", "plotly", "streamlit", "trip_plan"):
        monkeypatch.setitem(sys.modules, name, None)
    mod = importlib.reload(alerts)
    try:
        assert mod.city("ATH") == "Athens" and mod.city("ZRH") == "Zurich" and mod.city("XXX") == "XXX"
        assert mod.describe(mod.parse("route: ATH-ZRH\ncabin: business\nbelow: 250")) == "Athens → Zurich business drops below €250 one way"
    finally:
        for name in ("pandas", "plotly", "streamlit", "trip_plan"):
            monkeypatch.delitem(sys.modules, name, raising=False)
        importlib.reload(alerts)
