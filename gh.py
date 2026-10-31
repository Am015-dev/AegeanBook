"""The app's line to GitHub, with the token saved in the Streamlit secrets (any name with "github" in it):
- fetch routes on GitHub's servers (.github/workflows/fetch-route.yml), because Google rate-limits Streamlit Cloud's shared server;
- read the data files (daily fares, fetched routes) straight from the repository, so new data shows without waiting for Streamlit
  to redeploy, and a fetched route survives the app restarting.
The token needs, on this repository: Issues read and write (alerts), Actions read and write (fetching) and Contents read (data).
A new module on purpose: Streamlit Cloud can run a page against older, already loaded modules."""
import json
import os
import secrets
import tempfile
import time
import urllib.error
import urllib.request

REPO = "Am015-dev/AegeanBook"
WORKFLOW = "fetch-route.yml"
FRESH = 300  # seconds a downloaded data file is reused before asking GitHub again
_files = {}  # path -> (time, local copy or None)


class GitHubError(Exception):
    """A plain message for the page."""


def token():
    """The GitHub token from the Streamlit secrets, under any name with "github" in it (any case), at the top level or inside a
    [section]; or from the environment. None when there is none (tests, a computer without secrets)."""
    try:
        import streamlit as st
        items = list(st.secrets.items())
    except Exception:  # no secrets file at all
        items = []
    for name, value in sorted(items, key=lambda kv: "token" not in kv[0].lower()):
        if "github" in name.lower():
            values = value.values() if hasattr(value, "values") else [value]
            t = next((v.strip() for v in values if isinstance(v, str) and v.strip()), None)
            if t:
                return t
    return os.environ.get("GITHUB_TOKEN") or None


def _api(tok, method, path, body=None, raw=False, timeout=20):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {tok}", "X-GitHub-Api-Version": "2022-11-28",
                                          "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return data.decode() if raw else (json.loads(data) if data else None)
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise GitHubError("The app's GitHub token is missing a permission: it needs Actions (read and write) and Contents (read) "
                              "on this repository, besides Issues for the alerts.") from None
        if err.code == 404 and raw:
            return None
        raise GitHubError(f"GitHub answered HTTP {err.code}.") from None


def data_file(path, tok=None):
    """A local copy of the repository's current `path` (e.g. data/daily.csv), refreshed at most every FRESH seconds; None without a
    token, when the file is not there, or when GitHub cannot be reached (the app then uses the copy it was deployed with)."""
    tok = tok or token()
    if not tok:
        return None
    at, local = _files.get(path, (0, None))
    if time.time() - at < FRESH:
        return local
    try:
        text = _api(tok, "GET", f"/contents/{path}", raw=True)
    except Exception:  # never let a network hiccup break a page
        text = None
    if text is not None:
        fd, local = tempfile.mkstemp(suffix=os.path.basename(path))
        with os.fdopen(fd, "w") as f:
            f.write(text)
    _files[path] = (time.time(), local)
    return local


def forget(path):
    """Read `path` from GitHub again on the next data_file call (a fetch just finished)."""
    _files.pop(path, None)


def fetch(tok, legs, nonstop):
    """Start fetching on GitHub. legs: [(orig, dest, start, end)]. Returns the request id that names the run."""
    request = secrets.token_hex(4)
    _api(tok, "POST", f"/actions/workflows/{WORKFLOW}/dispatches", {"ref": "main", "inputs": {
        "legs": ";".join(f"{o},{d},{a.isoformat()},{b.isoformat()}" for o, d, a, b in legs),
        "nonstop": "1" if nonstop else "0", "request": request}})
    return request


def fetch_hotel(tok, query, filt, hkey, nights, adults, ranges, booking):
    """Start fetching a hotel search on GitHub (.github/workflows/fetch-hotel.yml). ranges: [(first, last)] check-in days still
    missing. Returns the request id that names the run."""
    request = secrets.token_hex(4)
    _api(tok, "POST", "/actions/workflows/fetch-hotel.yml/dispatches", {"ref": "main", "inputs": {
        "query": query, "filt": filt, "hkey": hkey or "", "nights": str(int(nights)), "adults": str(int(adults)),
        "ranges": ";".join(f"{a.isoformat()},{b.isoformat()}" for a, b in ranges), "booking": "1" if booking else "0",
        "request": request}})
    return request


def fetch_details(tok, legs, cabin):
    """Start fetching the flights of some days on GitHub (.github/workflows/fetch-details.yml). legs: [(orig, dest, day)]. Returns
    the request id that names the run."""
    request = secrets.token_hex(4)
    _api(tok, "POST", "/actions/workflows/fetch-details.yml/dispatches", {"ref": "main", "inputs": {
        "legs": ";".join(f"{o},{d},{a.isoformat()}" for o, d, a in legs), "cabin": cabin, "request": request}})
    return request


def run(tok, request, workflow=WORKFLOW):
    """The fetch's run as dict(status, conclusion, url), or None while GitHub has not listed it yet. status: queued, in_progress or
    completed; conclusion (when completed): success, failure, cancelled..."""
    runs = _api(tok, "GET", f"/actions/workflows/{workflow}/runs?event=workflow_dispatch&per_page=20") or {}
    r = next((r for r in runs.get("workflow_runs", []) if r.get("display_title") == f"fetch {request}"), None)
    return r and dict(status=r["status"], conclusion=r.get("conclusion"), url=r["html_url"])
