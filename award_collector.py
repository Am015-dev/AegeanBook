"""Miles+Bonus award helper, runs on YOUR machine with YOUR account (may breach AEGEAN's terms; keep it slow).

  python award_collector.py login    # log in once in the opened browser; profile is kept in .aegean_profile/
  python award_collector.py record   # do ONE manual award search; JSON responses are saved to award_recording.json

No password is stored by this script. award_recording.json may contain personal data (name, miles balance):
review it before sharing. ponytail: `fetch` (automatic daily search) comes after we see a real recording.
"""
import argparse
import csv
import json
import os
import re
from datetime import date, datetime, timedelta

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

PROFILE, OUT = ".aegean_profile", "award_recording.json"


def run(mode, host="aegeanair.com", url="https://en.aegeanair.com/", headless=False, wait=input):
    seen = []

    def on_response(r):
        if mode == "record" and host in r.url and "json" in r.headers.get("content-type", ""):
            try:
                seen.append({"method": r.request.method, "url": r.url, "status": r.status,
                             "post": r.request.post_data, "body": r.json()})
            except Exception:
                pass  # body gone (redirect/stream); skip

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=headless, executable_path=os.environ.get("CHROME_PATH"))
        ctx.on("response", on_response)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url)
        wait("Log in in the browser, then press Enter here..." if mode == "login"
             else "Run one Business award search for one date, wait for results, then press Enter here...")
        ctx.close()
    if mode == "record":
        json.dump(seen, open(OUT, "w"), indent=1, ensure_ascii=False)
    return seen


SEARCH_URL = "https://en.aegeanair.com/milesandbonus/my-account/spend-miles/award-ticket/"
OUT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "daily_award.csv")
FIELDS = ["day", "orig", "dest", "cabin", "miles", "nonstop", "fetched"]
DAYS = 90
HISTORY_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "award_history.csv")
HISTORY_DAYS = 90  # daily snapshots kept in the history file
DAY_TAB = "a[role=button]:has(time.date)"


def say(msg):
    print(msg, flush=True)  # flushed so the app's progress bar moves while the collector runs


def _miles(cell):
    """'from' / '9,032 Miles' -> 9032; 'No seats available' -> None."""
    m = re.search(r"([\d,]+) Miles", cell)
    return int(m.group(1).replace(",", "")) if m else None


def parse_flights(text):
    """Best award miles per (cabin, nonstop) from one day's rendered flight list, AEGEAN-operated flights only
    (every flight number starts with A3). nonstop=1 looks at nonstop flights, nonstop=0 at any AEGEAN flight."""
    best = {(c, n): None for c in ("economy", "business") for n in (0, 1)}
    for blk in text.split("\nFlight\n")[1:]:
        L = [x.strip() for x in blk.split("\n") if x.strip()]
        try:
            num = L[L.index("Flight details") + 1]
            cells = {c: L[L.index(c) + 1:L.index(c) + 3] for c in ("Economy", "Business")}
        except (ValueError, IndexError):
            continue
        if not all(t.startswith("A3") for t in num.split()):
            continue
        for cabin, cell in cells.items():
            m = _miles(cell[1] if cell[0] == "from" and len(cell) > 1 else cell[0])
            for n in ((0, 1) if "nonstop" in L else (0,)):
                k = (cabin.lower(), n)
                if m and (best[k] is None or m < best[k]):
                    best[k] = m
    return best


def _open(p, headless):
    if os.environ.get("AEGEAN_CDP"):  # attach to an already open Chrome (development)
        return p.chromium.connect_over_cdp(os.environ["AEGEAN_CDP"]).contexts[0]
    return p.chromium.launch_persistent_context(PROFILE, headless=headless, channel=None if os.environ.get("CHROME_PATH") else "chrome",
                                                executable_path=os.environ.get("CHROME_PATH"))


class Blocked(RuntimeError):
    """AEGEAN's bot protection refused the page: stop at once, retrying only makes it worse."""


def _check_blocked(page):
    if "Access Denied" in page.inner_text("body")[:300]:
        raise Blocked("AEGEAN answered 'Access Denied' (bot protection). Stopped; wait some hours before the next run.")


def _search(page, orig, dest, start):
    """Fill the one-way award search and land on the flight list of day `start`."""
    page.goto(SEARCH_URL)
    _check_blocked(page)
    page.wait_for_selector("#AirportFromSelect", timeout=30000)
    if "Available award miles" not in page.inner_text("body"):
        raise RuntimeError("Not logged in to AEGEAN: run `python award_collector.py login` first")
    page.get_by_text("One way", exact=True).first.click()
    for sel, code in (("#AirportFromSelect", orig), ("#AirportToSelect", dest)):
        page.click(sel)
        page.keyboard.type(code, delay=100)
        page.locator("li.ui-menu-item:visible", has_text=f"({code})").first.click(timeout=15000)
    page.get_by_text("Departure", exact=True).locator("visible=true").first.click(timeout=15000)  # opens the date picker
    page.locator(f'.date-picker-wrapper .day.valid[date="{start.day} {start.month - 1} {start.year}"]').first.click(timeout=15000)
    page.get_by_role("button", name="Search").last.click()
    page.wait_for_selector(DAY_TAB, timeout=60000)


TABS_JS = ("()=>[...document.querySelectorAll('a[role=button]')].filter(a=>a.querySelector('time.date')).map(a=>{const t=a.querySelector('time.date');"
           "return [t.getAttribute('datetime')||'',t.textContent.trim(),!/Not available/.test(a.textContent)]})")
SELECTED_JS = ("l=>{const a=[...document.querySelectorAll('a[role=button]')].find(a=>{const t=a.querySelector('time.date');"
               "return t&&t.textContent.trim()===l});return a&&/(?<!un)selected/.test(a.textContent)}")
NONE = {(c, n): None for c in ("economy", "business") for n in (0, 1)}


def _tabs(page, ref):
    """[(iso day, label, has award seats)] for the day tabs on the page; the flight list's labels ('Sat, 7 Nov') carry no year."""
    out = []
    for iso, label, ok in page.evaluate(TABS_JS):
        if not iso[:4].isdigit():
            d = label.split(", ")[-1]
            iso = min((x for x in (datetime.strptime(f"{d} {y}", "%d %b %Y").date() for y in (ref.year, ref.year + 1)) if x >= ref - timedelta(30)),
                      default=ref).isoformat()
        out.append((iso[:10], label, ok))
    return out


def _open_day(page, label):
    page.locator(DAY_TAB, has_text=label).first.click()
    page.wait_for_function(SELECTED_JS, arg=label, timeout=10000)


def collect_leg(page, orig, dest, start, end, log=say):
    """Yield (iso day, {(cabin, nonstop): miles|None}) for each day of start..end that AEGEAN's award calendar covers; best is None when
    a day failed to load. Days the calendar marks 'Not available' have no award seats on any cabin, so only the others are opened."""
    _search(page, orig, dest, start)
    days = {}
    for _ in range(8):  # the calendar shows about two months at a time
        tabs = _tabs(page, start)
        days.update({iso: (label, ok) for iso, label, ok in tabs})
        if max(days) >= end.isoformat():
            break
        page.get_by_text("Next", exact=True).locator("visible=true").first.click()
        try:  # wait until the window really moved on, a fixed pause sometimes read the old one again
            page.wait_for_function("f=>{const t=document.querySelector('a[role=button] time.date');return t&&t.getAttribute('datetime')!==f}",
                                   arg=tabs[0][0], timeout=10000)
        except PWTimeout:
            break
    want = sorted(d for d in days if start.isoformat() <= d <= end.isoformat())
    for iso in (d for d in want if not days[d][1]):
        yield iso, dict(NONE)
    todo = [d for d in want if days[d][1]]
    if not todo:
        return
    shown = [(i, lab) for i, lab, ok in _tabs(page, start) if ok]  # the calendar window moved on: open any day it still shows
    _open_day(page, next((lab for i, lab in shown if i in todo), shown[0][1]))
    page.get_by_role("button", name="Continue").last.click()
    page.wait_for_function("()=>document.body.innerText.includes('flight page is loaded')", timeout=60000)
    for iso in todo:
        try:
            for _ in range(25):  # the flight list shows one week; step weeks until the day is in view
                shown = {i: lab for i, lab, _ in _tabs(page, start)}
                if iso in shown:
                    break
                page.get_by_role("button", name=re.compile("goNextWeek" if iso > max(shown) else "goPrevWeek")).click()
                page.wait_for_timeout(2500)
            try:  # each day click asks the server for that day's flights: wait for the answer, not the stale list
                with page.expect_response(lambda r: r.request.method == "POST" and "aegeanair" in r.url, timeout=12000):
                    _open_day(page, shown[iso])
            except PWTimeout:  # no request: the day was already selected
                pass
            page.wait_for_function("()=>document.body.innerText.includes('Flight details')||/no (flights|seats)/i.test(document.body.innerText)",
                                   timeout=15000)
            page.mouse.wheel(0, 6000)
            page.wait_for_timeout(1200)
            yield iso, parse_flights(page.inner_text("body"))
        except Exception as e:  # day did not load: report it, the next run retries
            _check_blocked(page)  # unless AEGEAN is refusing us, then the whole run stops
            log(f"{iso} {type(e).__name__}")
            yield iso, None
        page.wait_for_timeout(1500)  # keep it slow

def save_csv(rows, path=OUT_CSV, today=None):
    """Merge new rows into the CSV: one row per (day, orig, dest, cabin, nonstop), newest wins, past days dropped."""
    old = {}
    if os.path.exists(path):
        with open(path, newline="") as f:
            old = {(r["day"], r["orig"], r["dest"], r["cabin"], r["nonstop"]): r for r in csv.DictReader(f)}
    old.update({(r["day"], r["orig"], r["dest"], r["cabin"], str(r["nonstop"])): {**r, "nonstop": str(r["nonstop"])} for r in rows})
    today = (today or date.today()).isoformat()
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(r for k, r in sorted(old.items()) if k[0] > today)


def append_history(rows, path=HISTORY_CSV, keep_days=HISTORY_DAYS, today=None):
    """Append this run's rows to the history file (one snapshot per run), dropping snapshots older than keep_days."""
    old = []
    if os.path.exists(path):
        with open(path, newline="") as f:
            old = list(csv.DictReader(f))
    cut = ((today or date.today()) - timedelta(keep_days)).isoformat()
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(r for r in old + [{**r, "nonstop": str(r["nonstop"])} for r in rows] if r["fetched"][:10] >= cut)

def fetch(legs, days=DAYS, headless=False, log=say, today=None):
    """Collect AEGEAN award miles for the next `days` days on every (orig, dest) leg and save them. Prints `DAY <iso> ok|error` per day."""
    today = today or date.today()
    start, end = today + timedelta(1), today + timedelta(days)
    rows, now = [], datetime.now().isoformat(timespec="seconds")
    with sync_playwright() as p:
        ctx = _open(p, headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            for orig, dest in legs:
                for iso, best in collect_leg(page, orig, dest, start, end, log):
                    if best is None:
                        log(f"DAY {iso} error")
                        continue
                    rows += [dict(day=iso, orig=orig, dest=dest, cabin=c, miles=m or "", nonstop=n, fetched=now) for (c, n), m in best.items()]
                    log(f"DAY {iso} ok")
        finally:  # save what we have even if the run broke half way
            save_csv(rows, today=today)
            if rows:
                append_history(rows, today=today)
            if not os.environ.get("AEGEAN_CDP"):
                ctx.close()
    return rows


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("mode", choices=["login", "record", "fetch"])
    a.add_argument("legs", nargs="*", help="fetch only: ORIG-DEST pairs, e.g. ATH-FRA FRA-ATH")
    a.add_argument("--days", type=int, default=DAYS)
    a = a.parse_args()
    if a.mode == "fetch":
        try:
            fetch([tuple(x.split("-")) for x in a.legs or ["ATH-FRA", "FRA-ATH", "ATH-ZRH", "ZRH-ATH"]], a.days)
        except Blocked as e:  # partial results are already saved by fetch()
            raise SystemExit(f"BLOCKED: {e}")
    else:
        run(a.mode)





