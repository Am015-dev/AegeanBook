"""Price alerts, free: each alert is an open GitHub issue labelled `price-alert`, made by any visitor on the app's Alerts page
(alert_store.py) or by hand. After the daily run saves the day's fares, `python alerts.py` (in .github/workflows/daily.yml) checks every
alert and tells its owner when it is met:
- `ntfy: <topic>`: a phone notification through ntfy.sh (free, no account; the visitor subscribes to the topic in the ntfy app);
- `email: <address>`: an email through SMTP (SMTP_USER and SMTP_PASSWORD, e.g. a Gmail app password, as GitHub Actions secrets), only
  once the address is confirmed: an unconfirmed email alert gets one confirmation email and nothing else;
- neither (an alert written by hand): a comment on the issue, which GitHub's own notifications carry.
Closing the issue stops the alert; the links in every message stop it too.

An alert's issue body is one setting per line:
    route: ATH-FRA
    cabin: economy
    below: 150          (euros; or `miles below: 16000` for award miles)
    trip: return        (optional: a return watches the two one-way fares added up, as the Overview's boarding pass prices a trip;
    nights: 2-7          without it the alert watches the one-way fare; nights default to 2-7)
    from: 2026-12-01    (optional: first and last departure day; without them, the next 90 days)
    to: 2026-12-31
    email: you@example.com   (or `ntfy: aegean-…`; written by the Alerts page with a random `code:` for its links)
It tells the first time the alert is met and again only when a cheaper trip (or fewer miles) appears, never the same news twice.

The fares come from the same database the app reads (db.connect: the daily legs, every route fetched from the app, award miles), priced
exactly as deals.best_trip prices the boarding pass: the newest fare of each day, nonstop, and for a return the cheapest outbound + return
pair within the nights asked. Only the standard library, so the daily run needs nothing installed for this step."""
import csv
import json
import os
import re
import smtplib
import subprocess
import urllib.parse
import urllib.request
from datetime import date, timedelta
from email.message import EmailMessage

import booking
from db import connect

VERSION = 1  # pages reload this module when Streamlit Cloud still has an older copy loaded (it keeps modules across redeploys)
REPO = "Am015-dev/AegeanBook"
APP = "https://aegeanbook-fdb67dfpcw4qxu8t2ornzt.streamlit.app/"
LABEL = "price-alert"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DAYS = 90  # an alert without dates watches the next 90 days, like the Overview's "Any date"
NIGHTS = (2, 7)  # a return alert without nights watches 2 to 7 nights, the Overview's default
MARK = re.compile(r"<!-- alert best: ([\d.]+) -->")
SENT = "<!-- confirmation sent -->"
CONFIRMED = "confirmed"


def parse(body):
    """The alert's settings from its issue body, or None when it has no route, cabin and limit. trip is "return" or "oneway"; nights the
    (shortest, longest) stay of a return."""
    kv = {k.strip().lower(): v.strip() for k, v in re.findall(r"^\s*([A-Za-z ]+?)\s*:\s*(.+?)\s*$", body or "", re.M)}
    route = re.fullmatch(r"([A-Za-z]{3})\s*[-→>]+\s*([A-Za-z]{3})", kv.get("route", ""))
    cabin = kv.get("cabin", "").lower()
    number = lambda v: float(re.sub(r"[^\d.]", "", v)) if v and re.search(r"\d", v) else None
    miles, below = number(kv.get("miles below")), number(kv.get("below"))
    if not route or cabin not in ("economy", "business") or not (miles or below):
        return None
    trip = "return" if kv.get("trip", "").lower().startswith("ret") else "oneway"
    span = re.findall(r"\d+", kv.get("nights", ""))
    nights = (int(span[0]), int(span[-1])) if span else NIGHTS
    return dict(orig=route[1].upper(), dest=route[2].upper(), cabin=cabin, unit="miles" if miles else "eur", limit=miles or below,
                trip=trip, nights=(min(nights), max(nights)), start=kv.get("from", ""), end=kv.get("to", ""),
                email=kv.get("email", ""), ntfy=kv.get("ntfy", ""), code=kv.get("code", ""))


def city(iata):
    """The airport's city, as the app names it (trip_plan.city); from the airline lists alone where pandas is not installed (the daily run)."""
    try:
        import trip_plan
        return trip_plan.city(iata)
    except ImportError:
        pass
    for name in ("aegean_airports.csv", "skyexpress_airports.csv"):
        path = os.path.join(DATA, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if r["iata"] == iata and r["city"]:
                        return r["city"]
    return iata


def describe(alert, when=""):
    """The alert in the traveller's words: "Athens → Frankfurt economy drops below €150 return (2–7 nights), leaving in December 2026"."""
    fmt = (lambda v: f"{v:,.0f} miles") if alert["unit"] == "miles" else (lambda v: f"€{v:,.0f}")
    lo, hi = alert["nights"]
    kind = f"return ({lo} nights)" if lo == hi else f"return ({lo}–{hi} nights)"
    return (f"{city(alert['orig'])} → {city(alert['dest'])} {alert['cabin']} drops below {fmt(alert['limit'])} "
            + (kind if alert["trip"] == "return" else "one way") + (f", leaving {when}" if when else ""))


def fares(db, orig, dest, cabin, unit, start, end):
    """{iso day: fare} between `start` and `end` (dates), the newest saved for each day, nonstop: what insights.cash_by_day (euros) and
    award_view.load_award (miles) give the Overview."""
    if unit == "miles":
        sql = ("SELECT day, miles FROM award_miles WHERE orig=? AND dest=? AND cabin=? AND nonstop=1 AND miles IS NOT NULL AND miles > 0 "
               "AND day BETWEEN ? AND ? ORDER BY fetched")
    else:
        sql = ("SELECT day, price FROM prices WHERE orig=? AND dest=? AND cabin=? AND nonstop=1 AND price IS NOT NULL AND day BETWEEN ? AND ? "
               "ORDER BY fetched")
    out = {}
    for day, v in db.execute(sql, (orig, dest, cabin, start.isoformat(), end.isoformat())):
        out[day] = float(v)  # the last row of a day is the newest
    return out


def trips(out, back, start, end, nights):
    """Every trip leaving between `start` and `end` (dates), cheapest first: [dict(depart, back, nights, price)]. With `back` a return,
    the two one-way fares added up for every stay in `nights` (lo, hi), as deals.trip_grid does; without it (None) the one-way fares.
    Ties are in date order, then shortest stay first, so the first trip is the one deals.best_trip names."""
    found = []
    for i in range((end - start).days + 1):
        d = start + timedelta(i)
        price = out.get(d.isoformat())
        if price is None:
            continue
        if back is None:
            found.append(dict(depart=d, back=None, nights=None, price=price))
            continue
        for n in range(nights[0], nights[1] + 1):
            ret = back.get((d + timedelta(n)).isoformat())
            if ret is not None:
                found.append(dict(depart=d, back=d + timedelta(n), nights=n, price=price + ret))
    return sorted(found, key=lambda t: (t["price"], t["depart"], t["nights"] or 0))


def window(alert, today):
    """(first, last) departure day the alert watches: its own dates, else tomorrow to 90 days ahead; never a day that has passed."""
    first = max(date.fromisoformat(alert["start"]) if alert["start"] else today + timedelta(1), today + timedelta(1))
    last = date.fromisoformat(alert["end"]) if alert["end"] else today + timedelta(DAYS)
    return first, last


def matches(alert, today=None, db=None):
    """The saved trips that meet the alert, cheapest first: [dict(depart, back, nights, price)]. A return alert is met only by a return
    (never by a one-way on its own); the fare must be strictly below the limit, as "drops below €150" says."""
    today = today or date.today()
    db = db or connect(":memory:")
    first, last = window(alert, today)
    if last < first:
        return []
    out = fares(db, alert["orig"], alert["dest"], alert["cabin"], alert["unit"], first, last)
    back = None
    if alert["trip"] == "return":
        back = fares(db, alert["dest"], alert["orig"], alert["cabin"], alert["unit"], first, last + timedelta(alert["nights"][1]))
    return [t for t in trips(out, back, first, last, alert["nights"]) if t["price"] < alert["limit"]]


def link(action, number, code):
    """A link to the app's Alerts page that confirms or stops alert `number`."""
    return f"{APP}alerts?{urllib.parse.urlencode({action: number, 'code': code})}"


def book(alert, trip):
    """AEGEAN's search, filled in with the trip's dates."""
    return booking.aegean_url(alert["orig"], alert["dest"], trip["depart"], trip["back"], cabin=alert["cabin"])


def message(alert, hits, last, stop=None):
    """The message for an alert that is met, or None when there is nothing new: met for the first time, or cheaper than last told.
    One sentence a person understands, then the dates, then the booking link:
    "Athens → Frankfurt is now €139 return in economy, below your €150: leave Thu 19 Nov, back Sat 21 Nov (2 nights). Book: <link>"."""
    if not hits or (last is not None and hits[0]["price"] >= last):
        return None
    fmt = (lambda v: f"{v:,.0f} miles") if alert["unit"] == "miles" else (lambda v: f"€{v:,.0f}")
    day = lambda d: f"{d:%a %d %b}"
    best = hits[0]
    dates = lambda t: f"leave {day(t['depart'])}" + (f", back {day(t['back'])} ({t['nights']} nights)" if t["back"] else "")
    kind = "return" if alert["trip"] == "return" else "one way"
    head = (f"**{city(alert['orig'])} → {city(alert['dest'])}** is now {fmt(best['price'])} {kind} in {alert['cabin']}, "
            + (f"below your {fmt(alert['limit'])}" if last is None else f"cheaper than the {fmt(last)} I told you about")
            + f": {dates(best)}. Book: {book(alert, best)}")
    others = [f"- {dates(t)}: {fmt(t['price'])}" for t in hits[1:6]]
    more = [f"Other dates under {fmt(alert['limit'])}:", *others] if others else []
    if len(hits) > 6:
        more.append(f"- …and {len(hits) - 6} more. See them all in the app: {APP}")
    return "\n".join([head, "", *more, "", "Fares as Google Flights showed them this morning; check on aegeanair.com before you book. "
                      + (f"Stop this alert: {stop}" if stop else "Close this issue to stop the alert."), f"<!-- alert best: {best['price']} -->"])


def gh(*args):
    return subprocess.run(["gh", *args, "--repo", REPO], check=True, capture_output=True, text=True).stdout


def body_lines(orig, dest, cabin, unit, limit, trip="oneway", nights=NIGHTS, start="", end=""):
    """The settings lines of an alert's issue body, as parse reads them."""
    lines = [f"route: {orig}-{dest}", f"cabin: {cabin}", f"{'miles below' if unit == 'miles' else 'below'}: {limit:.0f}"]
    if trip == "return":
        lines += ["trip: return", f"nights: {nights[0]}-{nights[1]}"]
    return lines + ([f"from: {start}"] if start else []) + ([f"to: {end}"] if end else [])


def title(orig, dest, cabin, unit, limit, trip="oneway"):
    """The issue's title: "Alert: ATH → FRA economy return below €150"."""
    what = f"{limit:,.0f} miles" if unit == "miles" else f"€{limit:,.0f}"
    return f"Alert: {orig} → {dest} {cabin} {'return' if trip == 'return' else 'one way'} below {what}"


def new_issue_link(orig, dest, cabin, unit, limit, start="", end="", trip="oneway", nights=NIGHTS):
    """A link to GitHub's new-issue form, filled in with an alert, for the app's alert form when it has no token."""
    body = "\n".join(body_lines(orig, dest, cabin, unit, limit, trip, nights, start, end)
                     + ["", "Price alert from FlyCheap, the AEGEAN fare tracker: the daily run checks it every morning and comments here when it is met. "
                            "Close this issue to stop it."])
    q = urllib.parse.urlencode({"title": title(orig, dest, cabin, unit, limit, trip), "labels": LABEL, "body": body})
    return f"https://github.com/{REPO}/issues/new?{q}"


def push(topic, title, text, click):
    """A phone notification through ntfy.sh (free, no key)."""
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=text.encode(), method="POST",
                                 headers={"Title": title, "Tags": "airplane", "Click": click})  # headers must be plain ASCII
    urllib.request.urlopen(req, timeout=30).read()


def mail(to, subject, text):
    """An email through SMTP; SMTP_USER and SMTP_PASSWORD come from the workflow's secrets (SMTP_HOST defaults to Gmail)."""
    user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    if not user or not password:
        raise RuntimeError("SMTP_USER and SMTP_PASSWORD are not set, so email alerts cannot be sent")
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = f"FlyCheap <{user}>", to, subject
    m.set_content(text)
    with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"), 465, timeout=30) as s:
        s.login(user, password)
        s.send_message(m)


def _plain(md):
    """The message without its hidden marker and markdown bold, for email and phone."""
    return MARK.sub("", md).replace("**", "").strip()


def _ascii(alert, trip):
    """A one-line title in plain ASCII (ntfy's headers take nothing else): "ATH-FRA: EUR 139 return, below your EUR 150"."""
    fmt = (lambda v: f"{v:,.0f} miles") if alert["unit"] == "miles" else (lambda v: f"EUR {v:,.0f}")
    kind = "return" if alert["trip"] == "return" else "one way"
    return f"{alert['orig']}-{alert['dest']}: {fmt(trip['price'])} {kind}, below your {fmt(alert['limit'])}"


def handle(issue, alert, today=None, db=None):
    """Deliver one alert. Returns the issue body to save (with what was told), or None when nothing changed."""
    body, n = issue["body"], issue["number"]
    labels = {l["name"] for l in issue.get("labels", [])}
    told = [float(m) for m in MARK.findall(body)] + [float(m) for c in issue.get("comments", []) for m in MARK.findall(c["body"])]
    stop = link("stop", n, alert["code"]) if alert["code"] else None
    if alert["email"] and CONFIRMED not in labels:  # an unconfirmed address gets one confirmation email, nothing else
        if SENT in body or not alert["code"]:
            return None
        what = describe(alert)
        mail(alert["email"], f"Confirm your price alert: {city(alert['orig'])} → {city(alert['dest'])}",
             f"Someone (hopefully you) asked FlyCheap, the AEGEAN fare tracker, to email this address when {what}.\n\n"
             f"Turn the alert on: {link('confirm', n, alert['code'])}\n\nNot you? Ignore this email and nothing more will be sent.")
        return body + "\n" + SENT
    hits = matches(alert, today, db)
    msg = message(alert, hits, min(told) if told else None, stop)
    if not msg:
        return None
    if alert["ntfy"]:
        push(alert["ntfy"], _ascii(alert, hits[0]), _plain(msg), book(alert, hits[0]))
    elif alert["email"]:
        mail(alert["email"], _ascii(alert, hits[0]), _plain(msg))
    else:
        gh("issue", "comment", str(n), "--body", msg)  # an alert written by hand: GitHub notifies its author
        return None
    return MARK.sub("", body).rstrip() + "\n" + MARK.search(msg)[0]  # remember the best told, without a comment that would notify


def run():
    gh("label", "create", LABEL, "--color", "2563C9", "--description", "Price alert checked by the daily run", "--force")
    issues = json.loads(gh("issue", "list", "--label", LABEL, "--state", "open", "--limit", "500",
                           "--json", "number,title,body,comments,labels"))
    db = connect(":memory:")  # every fare the app shows: the daily legs, the routes fetched from the app, the award miles
    for i in issues:
        alert = parse(i["body"])
        if not alert:
            print(f"#{i['number']}: not an alert I can read (needs route, cabin and below)")
            continue
        try:
            new = handle(i, alert, db=db)
        except Exception as err:  # one bad address or a service hiccup must not stop the other alerts
            print(f"#{i['number']}: could not deliver ({type(err).__name__}: {err})")
            continue
        print(f"#{i['number']} {i['title']}: {'sent' if new else 'nothing new'}")
        if new:
            gh("issue", "edit", str(i["number"]), "--body", new)


if __name__ == "__main__":
    run()
