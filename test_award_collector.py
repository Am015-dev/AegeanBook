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
