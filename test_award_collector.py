"""Smoke test: recorder keeps JSON from the watched host and ignores the rest. Needs playwright + chromium."""
import http.server, threading, json
import award_collector as ac


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api":
            body, ct = json.dumps({"miles": 25000}).encode(), "application/json"
        else:
            body, ct = b"<script>fetch('/api')</script>", "text/html"
        self.send_response(200); self.send_header("content-type", ct); self.end_headers(); self.wfile.write(body)
    log_message = lambda *a: None


def test_record(tmp_path, monkeypatch):
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.chdir(tmp_path)
    seen = ac.run("record", host="127.0.0.1", url=f"http://127.0.0.1:{srv.server_port}/", headless=True,
                  wait=lambda m: __import__("time").sleep(3))  # the page's background request needs a moment; 1 s was flaky
    assert [s["body"] for s in seen] == [{"miles": 25000}]
    assert json.load(open(ac.OUT))[0]["url"].endswith("/api")


SAMPLE = """x
Flight
08:10
nonstop
Flight details
A3830
Operated by: Aegean Airlines
Economy
from
9,032 Miles
Business
No seats available
Flight
06:45
nonstop
Flight details
LH1285
Economy
15,000 Miles
Business
25,000 Miles
Flight
08:25
1 stop
Flight details
A3682  LH283
Economy
12,000 Miles
Business
25,000 Miles
Flight
09:00
1 stop
Flight details
A3100  A3200
Economy
No seats available
Business
40,000 Miles"""


def test_parse_flights_aegean_only():
    b = ac.parse_flights(SAMPLE)
    assert b[("economy", 1)] == 9032 and b[("economy", 0)] == 9032  # codeshare with LH ignored, LH nonstop ignored
    assert b[("business", 1)] is None and b[("business", 0)] == 40000  # only the A3-only connection has Business


def test_save_csv_merges_and_drops_past(tmp_path):
    from datetime import date
    p = str(tmp_path / "a.csv")
    row = lambda day, miles: dict(day=day, orig="ATH", dest="FRA", cabin="economy", miles=miles, nonstop=1, fetched="t")
    ac.save_csv([row("2026-11-01", 100), row("2026-10-01", 5)], p, today=date(2026, 10, 29))
    ac.save_csv([row("2026-11-01", 90)], p, today=date(2026, 10, 29))
    rows = list(__import__("csv").DictReader(open(p)))
    assert [(r["day"], r["miles"]) for r in rows] == [("2026-11-01", "90")]


def test_append_history_keeps_only_changes_and_never_trims(tmp_path):
    p = str(tmp_path / "h.csv")
    row = lambda fetched, miles: dict(day="2026-11-11", orig="ATH", dest="FRA", cabin="economy", miles=miles, nonstop=1, fetched=fetched)
    assert ac.append_history([row("2026-01-01T07:00:00", 1)], p) == 1
    assert ac.append_history([row("2026-01-02T07:00:00", 1)], p) == 0   # same miles: nothing new to remember
    assert ac.append_history([row("2026-10-30T07:00:00", 3)], p) == 1   # much later: the first row is still there, history is never trimmed
    assert [r["miles"] for r in __import__("csv").DictReader(open(p))] == ["1", "3"]


def test_access_denied_stops_the_run():
    import pytest
    page = type("P", (), {"inner_text": lambda self, sel: "Access Denied\nYou don't have permission to access ..."})()
    with pytest.raises(ac.Blocked):
        ac._check_blocked(page)
    ok = type("P", (), {"inner_text": lambda self, sel: "Available award miles 52,513"})()
    ac._check_blocked(ok)  # a normal page passes


class FakePage:
    def __init__(self, text):
        self.text = text
    def inner_text(self, sel):
        return self.text
    def wait_for_timeout(self, ms):
        pass


def test_challenge_and_hang_stop_the_run():
    import pytest
    with pytest.raises(ac.Blocked):  # human check not solved within patience
        ac._check_blocked(FakePage("Please complete the CAPTCHA to continue"), log=lambda m: None, patience=0)
    with pytest.raises(ac.Blocked):  # the page that hangs after a request is a throttle
        ac._check_blocked(FakePage("You submitted a new request from calendar step, the page is now loading, please wait"), after_timeout=True)
    ac._check_blocked(FakePage("You submitted a new request, the page is now loading, please wait"))  # a normal wait is fine


def test_collected_today_skips_repeat_runs(tmp_path):
    from datetime import date
    p = str(tmp_path / "a.csv")
    ac.save_csv([dict(day="2026-11-11", orig="ATH", dest="FRA", cabin="economy", miles=1, nonstop=1, fetched="2026-10-29T07:00:00")], p,
                today=date(2026, 10, 28))
    assert ac.collected_today("ATH", "FRA", p, today=date(2026, 10, 29))
    assert not ac.collected_today("FRA", "ATH", p, today=date(2026, 10, 29))
    assert not ac.collected_today("ATH", "FRA", p, today=date(2026, 10, 30))


def test_imperva_challenge_page_is_recognised():
    import pytest
    page = FakePage("Pardon our interruption... As you were browsing www.aegeanair.com something about your browser made us think you were a bot.")
    with pytest.raises(ac.Blocked):
        ac._check_blocked(page, log=lambda m: None, patience=0)


def test_open_search_does_not_mistake_the_homepage_form_for_the_award_search():
    class Loc:
        def count(self):
            return 1
        first = property(lambda self: self)
        def is_visible(self):
            return True

    class Page:
        url = "https://en.aegeanair.com/"  # the homepage has its own #AirportFromSelect
        def __init__(self):
            self.went = []
        def locator(self, sel):
            return Loc()
        def get_by_text(self, t):
            return type("L", (), {"count": lambda s: 0})()
        def goto(self, u, referer=None):
            self.went.append(u)
        def inner_text(self, sel):
            return "Hello"
        def wait_for_timeout(self, ms):
            pass
    p = Page()
    ac._open_search(p)
    assert p.went == [ac.SEARCH_URL]  # from the homepage straight on to the award page, not "already there"
