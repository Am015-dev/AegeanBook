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
                  wait=lambda m: __import__("time").sleep(1))
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


def test_append_history_keeps_snapshots_and_prunes(tmp_path):
    from datetime import date
    p = str(tmp_path / "h.csv")
    row = lambda fetched, miles: dict(day="2026-11-11", orig="ATH", dest="FRA", cabin="economy", miles=miles, nonstop=1, fetched=fetched)
    ac.append_history([row("2026-07-01T07:00:00", 1)], p, keep_days=90, today=date(2026, 7, 1))
    ac.append_history([row("2026-10-29T07:00:00", 2)], p, keep_days=90, today=date(2026, 10, 29))  # first snapshot is now > 90 days old
    ac.append_history([row("2026-10-30T07:00:00", 3)], p, keep_days=90, today=date(2026, 10, 30))
    assert [r["miles"] for r in __import__("csv").DictReader(open(p))] == ["2", "3"]


def test_access_denied_stops_the_run():
    import pytest
    page = type("P", (), {"inner_text": lambda self, sel: "Access Denied\nYou don't have permission to access ..."})()
    with pytest.raises(ac.Blocked):
        ac._check_blocked(page)
    ok = type("P", (), {"inner_text": lambda self, sel: "Available award miles 52,513"})()
    ac._check_blocked(ok)  # a normal page passes
