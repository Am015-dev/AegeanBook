"""Price alerts, offline: a temporary data folder and a fake `gh`."""
import json
import os
from datetime import date, timedelta

import alerts


def day(i):
    return (date.today() + timedelta(i)).isoformat()


def write(folder, name, header, rows):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        f.write(header + "\n" + "\n".join(rows) + "\n")


def test_alert_from_the_app_link_is_read_back(monkeypatch):
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(alerts.new_issue_link("ATH", "ZRH", "business", "eur", 250, "2026-12-01", "2026-12-31")).query)
    assert q["labels"] == ["price-alert"] and q["title"] == ["Alert: ATH → ZRH business below €250"]
    assert alerts.parse(q["body"][0]) == dict(orig="ATH", dest="ZRH", cabin="business", unit="eur", limit=250.0,
                                              start="2026-12-01", end="2026-12-31", email="", ntfy="", code="")
    assert alerts.parse("route: ath → fra\ncabin: Economy\nmiles below: 9,500")["unit"] == "miles"  # hand-written works too
    assert alerts.parse("route: ATH-ZRH\ncabin: first\nbelow: 100") is None  # no such cabin


def test_matches_comment_once_then_only_when_cheaper(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "DATA", str(tmp_path))
    write(tmp_path, "daily.csv", "day,orig,dest,cabin,price,source,fetched,nonstop",
          [f"{day(3)},ATH,ZRH,business,240,calendar,x,1", f"{day(4)},ATH,ZRH,business,260,calendar,x,1",
           f"{day(5)},ATH,ZRH,business,230,calendar,x,0",  # not nonstop: ignored
           f"{day(-1)},ATH,ZRH,business,100,calendar,x,1",  # in the past: ignored
           f"{day(6)},ATH,ZRH,economy,90,calendar,x,1"])
    a = alerts.parse("route: ATH-ZRH\ncabin: business\nbelow: 250")
    hits = alerts.matches(a)
    assert hits == [(day(3), 240.0)]
    first = alerts.message(a, hits, None)
    assert "is now €240" in first and "below your limit of €250" in first and "<!-- alert best: 240.0 -->" in first
    assert alerts.message(a, hits, 240.0) is None  # the same news is not told twice
    assert "cheaper than the €245" in alerts.message(a, hits, 245.0)
    assert alerts.matches(dict(a, start=day(4))) == []  # date range respected


def test_run_comments_on_the_alerts_that_are_met(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "DATA", str(tmp_path))
    write(tmp_path, "daily.csv", "day,orig,dest,cabin,price,source,fetched,nonstop", [f"{day(3)},ATH,FRA,economy,61,calendar,x,1"])
    write(tmp_path, "daily_award.csv", "day,orig,dest,cabin,miles,nonstop,fetched", [f"{day(3)},ATH,FRA,business,16935,1,x"])
    issues = [dict(number=1, title="a", body="route: ATH-FRA\ncabin: economy\nbelow: 70", comments=[]),
              dict(number=2, title="b", body="route: ATH-FRA\ncabin: economy\nbelow: 70",
                   comments=[{"body": "old news <!-- alert best: 61.0 -->"}]),
              dict(number=3, title="c", body="route: ATH-FRA\ncabin: business\nmiles below: 20000", comments=[]),
              dict(number=4, title="d", body="just a note", comments=[])]
    calls = []

    def fake(*args):
        calls.append(args)
        return json.dumps(issues) if args[:2] == ("issue", "list") else ""
    monkeypatch.setattr(alerts, "gh", fake)
    alerts.run()
    commented = [a[2] for a in calls if a[:2] == ("issue", "comment")]
    assert commented == ["1", "3"]  # #2 was already told €61, #4 is not an alert
    assert "16,935 miles" in [a for a in calls if a[:3] == ("issue", "comment", "3")][0][4]


def test_visitor_alerts_phone_and_email_with_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "DATA", str(tmp_path))
    write(tmp_path, "daily.csv", "day,orig,dest,cabin,price,source,fetched,nonstop", [f"{day(3)},ATH,ZRH,business,240,calendar,x,1"])
    pushed, mailed = [], []
    monkeypatch.setattr(alerts, "push", lambda topic, title, text, click: pushed.append((topic, title, text)))
    monkeypatch.setattr(alerts, "mail", lambda to, subject, text: mailed.append((to, subject, text)))
    base = "route: ATH-ZRH\ncabin: business\nbelow: 250\ncode: s3cret\n"
    phone = dict(number=7, body=base + "ntfy: aegean-abc", labels=[{"name": "price-alert"}, {"name": "confirmed"}], comments=[])
    body = alerts.handle(phone, alerts.parse(phone["body"]))
    assert pushed[0][0] == "aegean-abc" and pushed[0][1].isascii() and "€240" in pushed[0][2] and "stop=7&code=s3cret" in pushed[0][2]
    assert "<!-- alert best: 240.0 -->" in body and "<!--" not in pushed[0][2]  # remembered in the body, not shown to the visitor
    assert alerts.handle(dict(phone, body=body), alerts.parse(body)) is None  # nothing new: no second notification
    email = dict(number=8, body=base + "email: a@b.co", labels=[{"name": "price-alert"}], comments=[])
    body = alerts.handle(email, alerts.parse(email["body"]))
    assert mailed[-1][0] == "a@b.co" and "Confirm" in mailed[-1][1] and "confirm=8&code=s3cret" in mailed[-1][2] and "€240" not in mailed[-1][2]
    assert alerts.handle(dict(email, body=body), alerts.parse(body)) is None and len(mailed) == 1  # one confirmation, never again
    confirmed = dict(email, body=body, labels=email["labels"] + [{"name": "confirmed"}])
    alerts.handle(confirmed, alerts.parse(body))
    assert len(mailed) == 2 and "€240" in mailed[-1][2] and "stop=8&code=s3cret" in mailed[-1][2]


def test_alert_store_create_confirm_and_stop(monkeypatch):
    import alert_store as st_
    issues, calls = {}, []

    def api(token, method, path, body=None):
        calls.append((method, path, body))
        if method == "GET" and path.startswith("/issues?"):
            return list(issues.values())
        if method == "POST" and path == "/issues":
            n = len(issues) + 1
            issues[n] = dict(number=n, state="open", title=body["title"], body=body["body"], labels=[{"name": l} for l in body["labels"]])
            return issues[n]
        if method == "GET":
            return issues[int(path.split("/")[-1])]
        return {}
    monkeypatch.setattr(st_, "_api", api)
    n, code = st_.create("t", "ATH", "ZRH", "business", "eur", 250, email="A@B.co")
    assert "email: a@b.co" in issues[n]["body"] and f"code: {code}" in issues[n]["body"] and "a@b.co" not in issues[n]["title"]
    assert [l["name"] for l in issues[n]["labels"]] == ["price-alert"]  # email waits for confirmation
    m, _ = st_.create("t", "ATH", "FRA", "economy", "eur", 70, ntfy=st_.topic())
    assert "confirmed" in [l["name"] for l in issues[m]["labels"]]  # a phone topic is the visitor's own: nothing to confirm
    for bad, err in ((dict(email="nope"), "does not look right"), (dict(), "Choose where")):
        try:
            st_.create("t", "ATH", "ZRH", "business", "eur", 250, **bad)
            raise AssertionError("should refuse")
        except st_.AlertError as e:
            assert err in str(e)
    try:
        st_.confirm("t", n, "wrong")
        raise AssertionError("should refuse")
    except st_.AlertError as e:
        assert "not valid" in str(e)
    st_.confirm("t", n, code)
    st_.stop("t", n, code)
    assert ("POST", f"/issues/{n}/labels", {"labels": ["confirmed"]}) in calls and calls[-1][0] == "PATCH"
    for _ in range(5):  # five alerts per address are fine, the sixth is refused
        st_.create("t", "ATH", "ZRH", "business", "eur", 250, email="x@y.co")
    try:
        st_.create("t", "ATH", "ZRH", "business", "eur", 250, email="x@y.co")
        raise AssertionError("should refuse")
    except st_.AlertError as e:
        assert "already have 5" in str(e)


def test_alerts_page_without_and_with_a_token(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import alert_store as store
    import db
    import test_overview
    for name in ("DAILY", "DAILY_HOTEL", "DAILY_AWARD"):
        monkeypatch.setattr(db, name, "/nonexistent.csv")
    monkeypatch.chdir(tmp_path)
    test_overview.seed(tmp_path / "tracker.db")
    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "views", "alerts.py")
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception and "not switched on yet" in at.info[0].value  # no token: the owner's GitHub link instead
    made = []
    monkeypatch.setattr(store, "create", lambda token, *a, **k: (made.append((a, k)), (12, "c0de"))[1])
    monkeypatch.setattr(alerts, "mail", lambda *a: (_ for _ in ()).throw(RuntimeError("no SMTP in tests")))
    at = AppTest.from_file(page, default_timeout=60)
    at.secrets["github"] = {"token": "t"}  # any name with "github" in it, even inside a [section]
    at.run()
    at.text_input(key="al-email").input("me@example.com")
    at.button[0].click().run()
    assert not at.exception and made[-1][1]["email"] == "me@example.com" and made[-1][0][:3] == ("ATH", "FRA", "economy")
    assert "confirmation email goes to me@example.com" in at.success[0].value and "stop=12&code=c0de" in at.caption[-1].value
    monkeypatch.setattr(store, "confirm", lambda token, n, code: "ATH → FRA economy below €60 (email)" if code == "ok" else
                        (_ for _ in ()).throw(store.AlertError("This link is not valid any more.")))
    at = AppTest.from_file(page, default_timeout=60)
    at.secrets["GITHUB_TOKEN"] = "t"
    at.query_params["route"], at.query_params["cabin"], at.query_params["unit"] = "ZRH-ATH", "business", "miles"  # from an "Alert me" link
    at.run()
    assert at.selectbox(key="al-leg").value == ("ZRH", "ATH") and at.button_group(key="al-cabin").value == "business"
    assert at.button_group(key="al-unit").value == "miles"
    at.query_params["confirm"], at.query_params["code"] = "12", "ok"
    at.run()
    assert "is on" in at.success[0].value
    at = AppTest.from_file(page, default_timeout=60)
    at.secrets["GITHUB_TOKEN"] = "t"
    at.query_params["confirm"], at.query_params["code"] = "12", "bad"
    at.run()
    assert "not valid" in at.error[0].value


def test_alerts_page_phone_sends_a_first_notification(tmp_path, monkeypatch):
    import urllib.request
    from streamlit.testing.v1 import AppTest
    import alert_store as store
    import db
    import test_overview
    for name in ("DAILY", "DAILY_HOTEL", "DAILY_AWARD"):
        monkeypatch.setattr(db, name, "/nonexistent.csv")
    monkeypatch.chdir(tmp_path)
    test_overview.seed(tmp_path / "tracker.db")
    made, sent = [], []
    monkeypatch.setattr(store, "create", lambda token, *a, **k: (made.append(k), (13, "c0de"))[1])
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: (sent.append(req), __import__("io").BytesIO(b"{}"))[1])
    at = AppTest.from_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "views", "alerts.py"), default_timeout=60)
    at.secrets["GITHUB_TOKEN"] = "t"
    at.run()
    at.button_group(key="al-how").set_value("phone").run()
    topic = at.code[0].value
    at.button[0].click().run()
    assert not at.exception and made[-1]["ntfy"] == topic and topic.startswith("aegean-")
    assert sent[-1].full_url == f"https://ntfy.sh/{topic}" and b"stop=13" in sent[-1].data and "first notification" in at.success[0].value
