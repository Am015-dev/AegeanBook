"""Miles+Bonus award helper, runs on YOUR machine with YOUR account (may breach AEGEAN's terms; keep it slow).

  python award_collector.py login    # log in once in the opened browser; profile is kept in .aegean_profile/
  python award_collector.py record   # do ONE manual award search; JSON responses are saved to award_recording.json

No password is stored by this script. award_recording.json may contain personal data (name, miles balance):
review it before sharing. ponytail: `fetch` (automatic daily search) comes after we see a real recording.
"""
import argparse
import csv
import time
import json
import os
import random
import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlencode

import history
from award_api import (PACE, Blocked, best_with_taxes, calendar_fares, days_to_check, pair_days, parse_flights as parse_api_flights, post_day,
                       trusted_best)

try:
    from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright
except ImportError:  # the app and the tests need no browser; only running the collector does
    PWTimeout, sync_playwright = TimeoutError, None

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

    _need_browser()
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
FIELDS = ["day", "orig", "dest", "cabin", "miles", "taxes", "nonstop", "fetched", "calendar"]  # taxes: cash EUR on top of the miles, from the request mode;
# calendar: the day's cheapest fare the last time it was looked at, "-" for no seats (see collect_leg_api)
DAYS = 365
FILL = 55  # day calls per leg and way back, nearest days first: 11 s each (the pause plus AEGEAN's 1.5 s) for 2 days, sized so a full run fits in MAX_MINUTES
MAX_MINUTES = 30  # a run stops here whatever is left: the calendar takes about 10, the day calls the rest
CYCLE_DAYS = 7  # the full year takes about 6 such runs, one a day: days answered this recently are not asked again
HISTORY_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "award_history.csv")
RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "award_runs")  # one file per collection day, kept to compare runs
DAY_TAB = "a[role=button]:has(time.date)"


def _need_browser():
    if sync_playwright is None:
        raise SystemExit("The browser tools are not installed: pip install -r requirements-award.txt")


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


HOME_URL = "https://en.aegeanair.com/"
CHALLENGE = ("captcha", "verify you are human", "are you a robot", "unusual traffic", "press & hold", "security check", "checking your browser",
             "pardon our interruption", "think you were a bot")  # the last two are Imperva's wording on aegeanair.com


def pause(page, lo=3.0, hi=8.0):
    """A random pause, so the requests do not arrive on a fixed beat."""
    page.wait_for_timeout(int(random.uniform(lo, hi) * 1000))


def _check_blocked(page, log=None, after_timeout=False, patience=300):
    """Stop the whole run when AEGEAN refuses us or stops answering. A human check (captcha) is given `patience` seconds to be
    solved by you in the open browser window; anything else is never retried, retrying only makes a block last longer."""
    log = log or say
    text = page.inner_text("body")
    if "Access Denied" in text[:300]:
        raise Blocked("AEGEAN answered 'Access Denied' (bot protection). Stopped; wait some hours before the next run.")
    if after_timeout and "is now loading, please wait" in text:
        raise Blocked("AEGEAN stopped answering (the page hangs): it is probably throttling us. Stopped; wait some hours.")
    waited = 0
    while any(k in text[:600].lower() for k in CHALLENGE):
        if waited >= patience:
            raise Blocked("AEGEAN is asking for a human check that was not solved. Solve it in the browser window and run again.")
        if not waited:
            log("CHALLENGE: AEGEAN wants a human check. Solve it in the browser window; waiting up to 5 minutes...")
        page.wait_for_timeout(5000)
        waited += 5
        text = page.inner_text("body")


def _open_search(page):
    """Reach the award search the way a visitor does: the page already open, else 'Back to search', else the homepage first and
    the award page with the homepage as referrer, never a cold jump to the deep link."""
    form = "#AirportFromSelect"  # the homepage's normal booking form uses the same id, so the URL decides which form this is
    if "award-ticket" in page.url and page.locator(form).count() and page.locator(form).first.is_visible():
        return
    back = page.get_by_text("Back to search")
    if back.count() and back.first.is_visible():
        back.first.click()
        try:
            page.wait_for_selector(form, timeout=15000)
            pause(page, 2, 4)
            return
        except PWTimeout:
            pass
    if page.url.rstrip("/") != HOME_URL.rstrip("/"):  # already on the homepage: go on from there
        page.goto(HOME_URL)
        _check_blocked(page)
        pause(page, 4, 9)
    page.goto(SEARCH_URL, referer=HOME_URL)


def _deny_cookies(page):
    """The cookie banner takes the keyboard focus, so the airport codes would be typed nowhere. The answer stays in the profile, so it
    only shows after the profile lost its cookies."""
    try:
        page.locator("#onetrust-reject-all-handler").click(timeout=5000)
        pause(page, 1, 2)
    except PWTimeout:
        pass  # no banner


def _wait_for_form(page, log=say, patience=600):
    """The award form, after a login in the open window if one is needed: the login lasts under a day, so a monthly run starts logged out."""
    try:
        page.wait_for_selector("#AirportFromSelect", timeout=30000)
    except PWTimeout:
        if "/member/login" not in page.url:
            raise
        log(f"LOGIN: log in to AEGEAN in the browser window; waiting up to {patience // 60} minutes...")
        page.wait_for_url(lambda u: "/member/login" not in u, timeout=patience * 1000)
        page.goto(HOME_URL)  # a fresh visit: the page the login lands on keeps its overlay over the form
        pause(page, 4, 9)
        page.goto(SEARCH_URL, referer=HOME_URL)
        page.wait_for_selector("#AirportFromSelect", timeout=30000)


def _search(page, orig, dest, start):
    """Fill the one-way award search and land on the flight list of day `start`."""
    _open_search(page)
    _check_blocked(page)
    _wait_for_form(page)
    try:  # the member box renders a moment after the form
        page.wait_for_function("()=>document.body.innerText.includes('Available award miles')", timeout=15000)
    except PWTimeout:
        _check_blocked(page)
        raise RuntimeError("Not logged in to AEGEAN: run `python award_collector.py login` first")
    _deny_cookies(page)
    page.get_by_text("One way", exact=True).first.click()
    for sel, code in (("#AirportFromSelect", orig), ("#AirportToSelect", dest)):
        page.click(sel)
        page.keyboard.type(code, delay=random.randint(80, 170))
        page.locator("li.ui-menu-item:visible", has_text=f"({code})").first.click(timeout=15000)
        pause(page, 1, 2.5)
    page.get_by_text("Departure", exact=True).locator("visible=true").first.click(timeout=15000)  # opens the date picker
    pause(page, 1, 2.5)
    page.locator(f'.date-picker-wrapper .day.valid[date="{start.day} {start.month - 1} {start.year}"]').first.click(timeout=15000)
    pause(page, 1, 2.5)
    page.get_by_role("button", name="Search").last.click()
    try:
        page.wait_for_selector(DAY_TAB, timeout=60000)
    except PWTimeout:
        _check_blocked(page, after_timeout=True)  # returns once a human check is solved in the window: then the search goes on
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


def _read_calendar(page, start, end):
    """{iso day: (label, has award seats)} from the calendar page, pressing Next until `end` is covered (about two months per window)."""
    days = {}
    for _ in range(8):
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
        pause(page, 4, 9)
    return days


def open_session(page, leg, start):
    """One real search, up to the page's own request for a day's flights: that request is the template for every other day of every leg."""
    _search(page, *leg, start)
    shown = [lab for _, lab, ok in _tabs(page, start) if ok]
    if not shown:
        raise RuntimeError("The calendar shows no day with seats to start from")
    _open_day(page, shown[0])
    pause(page, 2, 4)
    try:
        with page.expect_response(lambda r: r.request.method == "POST" and "fromCalendarPage.json" in r.url, timeout=60000) as got:
            page.get_by_role("button", name="Continue").last.click()
    except PWTimeout:
        _check_blocked(page, after_timeout=True)
        raise
    if "json" not in got.value.headers.get("content-type", ""):
        raise Blocked("AEGEAN answered the flight request with a page instead of data (bot check). Stopped.")
    req = got.value.request
    return dict(url=req.url, template=req.post_data, ticket=int(json.loads(parse_qs(req.post_data, keep_blank_values=True)["data"][0]).get("PAGE_TICKET", 0)))


def _direct_only(route):
    """The page's own search, direct flights on AEGEAN's fare family only: no connections and no partners' dearer fares in the calendar. On
    2026-11-02 its days matched that day's real answers on 524 of 525 leg-days, across the four legs."""
    form = {k: v[0] for k, v in parse_qs(route.request.post_data, keep_blank_values=True).items()}
    data = json.loads(form["data"])
    data["DIRECT_NON_STOP"] = "TRUE"
    data["COMMERCIAL_FARE_FAMILY_1"] = "CFFE2EIRU"
    form["data"] = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    route.continue_(post_data=urlencode(form))


def read_year(page, leg, start, end):
    """{iso day: calendar fare} of one leg up to `end`, direct flights only: a search, then Next, 61 days per request."""
    got = []

    def answer(r):
        if r.request.method == "POST" and "fromEntryPoint" in r.url and "json" in r.headers.get("content-type", ""):
            got.append(r.json())

    page.goto("about:blank")  # _search then comes in through the homepage: a form left behind by another search can miss its fields
    page.on("response", answer)
    page.route("**/fromEntryPoint*", _direct_only)
    try:
        _search(page, *leg, start)
        fares = {}
        for _ in range(9):
            for _ in range(100):
                if got:
                    break
                page.wait_for_timeout(300)
            else:
                _check_blocked(page, after_timeout=True)
                raise RuntimeError("The calendar did not answer")
            fares.update(calendar_fares(got.pop(0)))
            if max(fares) >= end.isoformat():
                break
            pause(page, *PACE)
            page.get_by_text("Next", exact=True).locator("visible=true").first.click()
        return fares
    finally:
        page.remove_listener("response", answer)
        page.unroute("**/fromEntryPoint*")


def _extend_session(page, log=say):
    """The flight page counts down while nobody clicks and asks to extend the session, every few minutes of a run: answer it, so nobody has to
    watch the window."""
    button = page.get_by_text("Extend my session", exact=True)
    if button.count() and button.first.is_visible():
        button.first.click(timeout=5000)
        log("SESSION extended")


def collect_pair_api(page, session, a, b, start, end, log=say, stamps=None, calendars=None, finished=None):
    """Same output as collect_leg, from requests alone, for a leg and its way back at once: a round-trip call has one day of each in both
    cabins plus the calendar of the 7 days around each. So a call every 7 days maps both legs. Then a day the calendar shows empty costs
    nothing, and a day with seats is asked about when its calendar fare changed or it is one of the REFRESH days checked longest ago, two days
    per call, the FILL nearest of them per run; the others keep their stored row, or stay without one, and wait for a later run: a day is
    only stored from a real answer, never guessed from its calendar fare. A day the calendar did not cover is never assumed empty, and an answer trusted_best rejects is asked again one-way.
    With `calendars` ({'ATH-FRA': read_year's answer}) this is a full run instead: no map and no stored row counts, every day with direct seats
    up to FILL calls gets a real answer, nearest first, except the `finished` ones ({leg: days}) of an interrupted run. A day with no direct seats gets nonstop rows only: the
    direct calendar says nothing about connections. A refused request raises Blocked and ends the run. Yields (orig, dest, iso day, best).
    `stamps` collects {(day, orig, dest): calendar fare} for save_csv; a day that should have been asked about but was not is left out, so
    the next run still sees it as changed."""
    stamps = {} if stamps is None else stamps
    days = [(start + timedelta(i)).isoformat() for i in range((end - start).days + 1)]
    there, back = legs = (a, b), (b, a)
    fares, asked, lowest = {l: {} for l in legs}, {l: set() for l in legs}, {l: lowest_miles(*l) for l in legs}

    def wait():  # outside the try of its caller: a closed browser ends the run, it is not one more failed day
        pause(page, *PACE)
        _extend_session(page, log)

    def post(iso, orig, dest, ret=None):
        session["ticket"] += 1
        return post_day(page, session["url"], session["template"], date.fromisoformat(iso), session["ticket"], orig, dest,
                        date.fromisoformat(ret) if ret else None)

    def done(leg, iso, best):
        asked[leg].add(iso)
        if best is not None:
            stamps[(iso, *leg)] = fares[leg].get(iso, "")
        return (*leg, iso, best)

    def one_way(leg, iso):
        wait()
        try:
            resp = post(iso, *leg)
            if not calendars:
                fares[leg].update(calendar_fares(resp))
            return done(leg, iso, best_with_taxes(parse_api_flights(resp)))
        except Blocked:
            raise
        except Exception as e:  # one day failed to parse or load: report it, the next run retries
            log(f"{iso} {type(e).__name__} {e}")
            return done(leg, iso, None)

    def both(out, ret):
        if ret is None:
            yield one_way(there, out)
            return
        resp = None
        wait()
        try:
            resp = post(out, a, b, ret)
            if not calendars:  # a full run keeps read_year's direct calendar
                fares[there].update(calendar_fares(resp))
                fares[back].update(calendar_fares(resp, 1))
        except Blocked:
            raise
        except Exception as e:
            log(f"{out} {ret} {type(e).__name__} {e}")
        for i, leg, iso in ((0, there, out), (1, back, ret)):
            try:
                best = trusted_best(resp, i, lowest[leg]) if resp else None  # the day's own answer: a calendar inside a round trip is priced as one
            except Exception as e:
                log(f"{iso} {type(e).__name__} {e}")
                best = None
            yield done(leg, iso, best) if best else one_way(leg, iso)

    if calendars:
        todo = {}
        for leg in legs:
            fares[leg] = dict(calendars[f"{leg[0]}-{leg[1]}"])
            left = [d for d in days if d not in (finished or {}).get(leg, ())]
            for iso in (d for d in left if fares[leg].get(d) == "-"):
                stamps[(iso, *leg)] = "-"
                yield (*leg, iso, {k: None for k in NONE if k[1] == 1})
            todo[leg] = [d for d in left if fares[leg].get(d) != "-"]
            log(f"{leg[0]}-{leg[1]}: {len(days) - len(todo[leg])} days done or without direct seats, asking about {len(todo[leg])}")
        pairs, lone = pair_days(todo[there], todo[back])
        for out, ret in pairs[:FILL]:
            yield from both(out, ret)
        for iso in lone[:max(0, FILL - len(pairs))]:
            yield one_way(back, iso)
        return
    for iso in days[3::7]:
        yield from both(iso, iso)
    todo = {}
    for leg in legs:
        left = [d for d in days if d not in asked[leg]]
        for iso in (d for d in left if fares[leg].get(d) == "-"):
            stamps[(iso, *leg)] = "-"
            yield (*leg, iso, dict(NONE))
        rest = [d for d in left if fares[leg].get(d) != "-"]
        todo[leg] = sorted(set(days_to_check([d for d in rest if d in fares[leg]], last_checked(*leg), fares[leg])) | {d for d in rest if d not in fares[leg]})
        log(f"{leg[0]}-{leg[1]}: {len(fares[leg])} days mapped, {len(rest)} more with seats, asking about {len(todo[leg])}")
        for iso in set(rest) - set(todo[leg]):
            stamps[(iso, *leg)] = fares[leg][iso]
    pairs, lone = pair_days(todo[there], todo[back])
    for out, ret in pairs[:FILL]:
        yield from both(out, ret)
    for iso in lone[:max(0, FILL - len(pairs))]:
        yield one_way(back, iso)


def collect_leg(page, orig, dest, start, end, log=say):
    """Yield (iso day, {(cabin, nonstop): miles|None}) for each day of start..end that AEGEAN's award calendar covers; best is None when
    a day failed to load. Days the calendar marks 'Not available' have no award seats on any cabin, so only the others are opened."""
    _search(page, orig, dest, start)
    days = _read_calendar(page, start, end)
    want = sorted(d for d in days if start.isoformat() <= d <= end.isoformat())
    for iso in (d for d in want if not days[d][1]):
        yield iso, dict(NONE)
    todo = [d for d in want if days[d][1]]
    if not todo:
        return
    shown = [(i, lab) for i, lab, ok in _tabs(page, start) if ok]  # the calendar window moved on: open any day it still shows
    _open_day(page, next((lab for i, lab in shown if i in todo), shown[0][1]))
    pause(page, 2, 5)
    page.get_by_role("button", name="Continue").last.click()
    try:
        page.wait_for_function("()=>document.body.innerText.includes('flight page is loaded')", timeout=60000)
    except PWTimeout:
        _check_blocked(page, log, after_timeout=True)  # a page that hangs here is a throttle: stop, do not retry
        raise
    _check_blocked(page, log)
    for done, iso in enumerate(todo):
        if done and done % 10 == 0:
            pause(page, 15, 35)  # a longer break now and then, like a person reading
        try:
            for _ in range(25):  # the flight list shows one week; step weeks until the day is in view
                shown = {i: lab for i, lab, _ in _tabs(page, start)}
                if iso in shown:
                    break
                page.get_by_role("button", name=re.compile("goNextWeek" if iso > max(shown) else "goPrevWeek")).click()
                pause(page, 2.5, 5)
            try:  # each day click asks the server for that day's flights: wait for the answer, not the stale list
                with page.expect_response(lambda r: r.request.method == "POST" and "aegeanair" in r.url, timeout=12000):
                    _open_day(page, shown[iso])
            except PWTimeout:  # no request: the day was already selected
                pass
            page.wait_for_function("()=>document.body.innerText.includes('Flight details')||/no (flights|seats)/i.test(document.body.innerText)",
                                   timeout=15000)
            page.mouse.wheel(0, 6000)
            pause(page, 1, 2.5)
            yield iso, parse_flights(page.inner_text("body"))
        except Exception as e:  # day did not load: report it, the next run retries
            _check_blocked(page, log, after_timeout=True)  # unless AEGEAN is refusing or not answering: then the whole run stops
            log(f"{iso} {type(e).__name__}")
            yield iso, None
        pause(page, 3, 8)  # keep it slow and irregular

def save_csv(rows, path=OUT_CSV, today=None, stamps=None):
    """Merge new rows into the CSV: one row per (day, orig, dest, cabin, nonstop), newest wins, past days dropped. `stamps` updates the
    calendar column, also on rows of days that were not asked about."""
    old = {}
    if os.path.exists(path):
        with open(path, newline="") as f:
            old = {(r["day"], r["orig"], r["dest"], r["cabin"], r["nonstop"]): r for r in csv.DictReader(f)}
    old.update({(r["day"], r["orig"], r["dest"], r["cabin"], str(r["nonstop"])): {**r, "nonstop": str(r["nonstop"])} for r in rows})
    for k, r in old.items():
        r["calendar"] = (stamps or {}).get(k[:3], r.get("calendar", ""))
    today = (today or date.today()).isoformat()
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(r for k, r in sorted(old.items()) if k[0] > today)


def append_history(rows, path=HISTORY_CSV):
    """Add this run's changes to the permanent history, which is never trimmed: a row only when the miles differ from the last one stored
    for that day, route, cabin and nonstop flag."""
    rows = [{k: v for k, v in r.items() if k != "calendar"} for r in rows]
    return history.append_changes(path, rows, ("day", "orig", "dest", "cabin", "nonstop"), ("miles", "taxes"))


def day_rows(iso, orig, dest, best, now, calendar=""):
    """The CSV rows of one day. `best` maps (cabin, nonstop) to miles (the page-reading mode), to (miles, taxes) (the request mode) or to
    None (no award seats): the miles cell is empty then, and so are the taxes when they were not read."""
    rows = []
    for (cabin, nonstop), m in best.items():
        miles, taxes = m if isinstance(m, tuple) else (m, None)
        rows.append(dict(day=iso, orig=orig, dest=dest, cabin=cabin, miles=miles or "", taxes="" if taxes is None else round(taxes, 2),
                         nonstop=nonstop, fetched=now, calendar=calendar))
    return rows


def last_checked(orig, dest, path=OUT_CSV):
    """{day: (calendar fare, fetched)} of one leg, as last stored."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {r["day"]: (r.get("calendar") or "", r["fetched"]) for r in csv.DictReader(f) if r["orig"] == orig and r["dest"] == dest}


def days_in_run(path):
    """{(orig, dest): days} already in a run's file."""
    done = {}
    if os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                done.setdefault((r["orig"], r["dest"]), set()).add(r["day"])
    return done


def days_in_cycle(today, runs_dir=None, back=CYCLE_DAYS):
    """The same for the run files of the last `back` days: the year takes several MAX_MINUTES runs, each goes on where the last one stopped."""
    done = {}
    for i in range(back):
        day = (today - timedelta(i)).isoformat()
        for leg, days in days_in_run(os.path.join(runs_dir or RUNS_DIR, f"{day}.csv")).items():
            done.setdefault(leg, set()).update(days)
    return done


def lowest_miles(orig, dest, path=OUT_CSV):
    """{cabin: lowest nonstop miles stored} of one leg."""
    low = {}
    if os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                if r["orig"] == orig and r["dest"] == dest and r["miles"] and r["nonstop"] == "1":
                    low[r["cabin"]] = min(int(r["miles"]), low.get(r["cabin"], int(r["miles"])))
    return low


def collected_today(orig, dest, path=OUT_CSV, today=None):
    """True when this leg was already collected today: running it again only adds load on AEGEAN, which is what got us blocked."""
    if not os.path.exists(path):
        return False
    today = (today or date.today()).isoformat()
    with open(path, newline="") as f:
        return any(r["orig"] == orig and r["dest"] == dest and r["fetched"][:10] == today for r in csv.DictReader(f))


def fetch(legs, days=DAYS, headless=False, log=say, today=None, force=False, api=False, full=False):
    """Collect AEGEAN award miles for the next `days` days on every (orig, dest) leg and save them. Prints `DAY <iso> ok|error` per day.
    A leg already collected today is skipped unless force."""
    today = today or date.today()
    start, end = today + timedelta(1), today + timedelta(days)
    rows, stamps, now, deadline = [], {}, datetime.now().isoformat(timespec="seconds"), time.time() + MAX_MINUTES * 60
    _need_browser()

    def save():
        os.makedirs(RUNS_DIR, exist_ok=True)
        save_csv(rows, today=today, stamps=stamps)
        save_csv(rows, os.path.join(RUNS_DIR, f"{today.isoformat()}.csv"), today, stamps)
        if rows:
            append_history(rows)

    with sync_playwright() as p:
        ctx = _open(p, headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        session = None

        def keep(orig, dest, iso, best):
            if best is None:
                log(f"DAY {iso} error")
                return
            before = len(rows) // 80
            rows.extend(day_rows(iso, orig, dest, best, now, stamps.get((iso, orig, dest), "")))
            log(f"DAY {iso} ok")
            if len(rows) // 80 > before:  # every 20 days or so: a killed process never reaches the finally, and a leg can take a while
                save()

        try:
            pairs = list(dict.fromkeys(tuple(sorted(l)) for l in legs))  # a leg and its way back share their calls, so both always run
            if full:
                cal_file = os.path.join(RUNS_DIR, f"{today.isoformat()}-calendar.json")
                os.makedirs(RUNS_DIR, exist_ok=True)
                calendars = json.load(open(cal_file)) if os.path.exists(cal_file) else {}
                for leg in (l for a, b in pairs for l in ((a, b), (b, a))):
                    if "-".join(leg) not in calendars:
                        calendars["-".join(leg)] = read_year(page, leg, start, end)
                        json.dump(calendars, open(cal_file, "w"))
                        log(f"CALENDAR {leg[0]}-{leg[1]}: {len(calendars['-'.join(leg)])} days")
                        pause(page, *PACE)
                page.goto("about:blank")
                session = open_session(page, pairs[0], start)
                first_ticket = session["ticket"]
                for a, b in pairs:
                    for row in collect_pair_api(page, session, a, b, start, end, log, stamps, calendars, days_in_cycle(today)):
                        keep(*row)
                        if time.time() > deadline:
                            log(f"STOPPED at the {MAX_MINUTES} minute limit")
                            return rows
                        log(f"CALLS {session['ticket'] - first_ticket} in {round(time.time() - deadline + MAX_MINUTES * 60)} s")
                    save()
            elif api:
                for a, b in pairs:
                    if not force and collected_today(a, b, today=today) and collected_today(b, a, today=today):
                        log(f"SKIP {a}-{b} and back: already collected today (use --force to run them again)")
                        continue
                    session = session or open_session(page, (a, b), start)
                    for row in collect_pair_api(page, session, a, b, start, end, log, stamps):
                        keep(*row)
                    save()
            else:
                for n, (orig, dest) in enumerate(legs):
                    if not force and collected_today(orig, dest, today=today):
                        log(f"SKIP {orig}-{dest}: already collected today (use --force to run it again)")
                        continue
                    if n:
                        pause(page, 30, 60)  # a real break between legs
                    for iso, best in collect_leg(page, orig, dest, start, end, log):
                        keep(orig, dest, iso, best)
                    save()
        finally:  # save what we have even if the run broke half way
            save()
            if not os.environ.get("AEGEAN_CDP"):
                ctx.close()
    return rows


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("mode", choices=["login", "record", "fetch"])
    a.add_argument("legs", nargs="*", help="fetch only: ORIG-DEST pairs, e.g. ATH-FRA FRA-ATH")
    a.add_argument("--days", type=int, default=DAYS)
    a.add_argument("--force", action="store_true", help="fetch: collect legs that were already collected today")
    a.add_argument("--full", action="store_true", help="fetch: the monthly run: today's calendar, then real answers for the nearest days, up to MAX_MINUTES")
    a.add_argument("--year", action="store_true", help="fetch --full without the limits: a real answer for every day with seats, about 2 hours; run it again to go on after a refusal")
    a.add_argument("--api", action="store_true", help="fetch: one search, then paced round-trip requests from inside the page, two days each, for the days that may have changed")
    a = a.parse_args()
    if a.mode == "fetch":
        legs = [tuple(x.split("-")) for x in a.legs or ["ATH-FRA", "FRA-ATH", "ATH-ZRH", "ZRH-ATH"]]
        if a.year:
            a.full, FILL, MAX_MINUTES = True, 10000, 240
        try:
            fetch(legs, a.days, force=a.force, api=a.api, full=a.full)
        except Blocked as e:  # partial results are already saved by fetch()
            raise SystemExit(f"BLOCKED: {e}")
    else:
        run(a.mode)





