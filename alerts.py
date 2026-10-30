"""Price alerts, free: each alert is an open GitHub issue labelled `price-alert`, made by any visitor on the app's Alerts page
(alert_store.py) or by hand. After the daily run saves the day's fares, `python alerts.py` (in .github/workflows/daily.yml) checks every
alert and tells its owner when it is met:
- `ntfy: <topic>`: a phone notification through ntfy.sh (free, no account; the visitor subscribes to the topic in the ntfy app);
- `email: <address>`: an email through SMTP (SMTP_USER and SMTP_PASSWORD, e.g. a Gmail app password, as GitHub Actions secrets), only
  once the address is confirmed: an unconfirmed email alert gets one confirmation email and nothing else;
- neither (an alert written by hand): a comment on the issue, which GitHub's own notifications carry.
Closing the issue stops the alert; the links in every message stop it too.

An alert's issue body is one setting per line:
    route: ATH-ZRH
    cabin: business
    below: 250          (euros; or `miles below: 16000` for award miles)
    from: 2026-12-01    (optional: first and last departure day)
    to: 2026-12-31
    email: you@example.com   (or `ntfy: aegean-…`; written by the Alerts page with a random `code:` for its links)
It tells the first time the alert is met and again only when a cheaper fare (or fewer miles) appears, never the same news twice."""
import csv
import json
import os
import re
import smtplib
import subprocess
import urllib.parse
import urllib.request
from datetime import date
from email.message import EmailMessage

REPO = "Am015-dev/AegeanBook"
APP = "https://aegeanbook-fdb67dfpcw4qxu8t2ornzt.streamlit.app/"
LABEL = "price-alert"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MARK = re.compile(r"<!-- alert best: ([\d.]+) -->")
SENT = "<!-- confirmation sent -->"
CONFIRMED = "confirmed"


def parse(body):
    """The alert's settings from its issue body, or None when it has no route, cabin and limit."""
    kv = {k.strip().lower(): v.strip() for k, v in re.findall(r"^\s*([A-Za-z ]+?)\s*:\s*(.+?)\s*$", body or "", re.M)}
    route = re.fullmatch(r"([A-Za-z]{3})\s*[-→>]+\s*([A-Za-z]{3})", kv.get("route", ""))
    cabin = kv.get("cabin", "").lower()
    number = lambda v: float(re.sub(r"[^\d.]", "", v)) if v and re.search(r"\d", v) else None
    miles, below = number(kv.get("miles below")), number(kv.get("below"))
    if not route or cabin not in ("economy", "business") or not (miles or below):
        return None
    return dict(orig=route[1].upper(), dest=route[2].upper(), cabin=cabin, unit="miles" if miles else "eur", limit=miles or below,
                start=kv.get("from", ""), end=kv.get("to", ""), email=kv.get("email", ""), ntfy=kv.get("ntfy", ""), code=kv.get("code", ""))


def matches(alert, today=None):
    """[(iso day, value)] of saved fares (or award miles) that meet the alert, cheapest first."""
    today = (today or date.today()).isoformat()
    name, col = ("daily_award.csv", "miles") if alert["unit"] == "miles" else ("daily.csv", "price")
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["orig"] == alert["orig"] and r["dest"] == alert["dest"] and r["cabin"] == alert["cabin"]
                and r.get("nonstop", "1") == "1" and r[col] and r["day"] > today
                and (not alert["start"] or r["day"] >= alert["start"]) and (not alert["end"] or r["day"] <= alert["end"])]
    hits = {r["day"]: float(r[col]) for r in rows if float(r[col]) <= alert["limit"]}
    return sorted(hits.items(), key=lambda kv: (kv[1], kv[0]))


def link(action, number, code):
    """A link to the app's Alerts page that confirms or stops alert `number`."""
    return f"{APP}alerts?{urllib.parse.urlencode({action: number, 'code': code})}"


def message(alert, hits, last, stop=None):
    """The comment for an alert that is met, or None when there is nothing new: met for the first time, or cheaper than last told."""
    if not hits or (last is not None and hits[0][1] >= last):
        return None
    fmt = (lambda v: f"{v:,.0f} miles") if alert["unit"] == "miles" else (lambda v: f"€{v:,.0f}")
    day = lambda d: date.fromisoformat(d).strftime("%a %d %b")
    head = (f"**{alert['orig']} → {alert['dest']} {alert['cabin']}** is now {fmt(hits[0][1])} on {day(hits[0][0])}"
            + (f", below your limit of {fmt(alert['limit'])}" if last is None else f", cheaper than the {fmt(last)} I told you about")
            + f": {len(hits)} day{'s' * (len(hits) > 1)} meet the alert.")
    lines = [f"- {day(d)}: {fmt(v)}" for d, v in hits[:10]] + ([f"- …and {len(hits) - 10} more"] if len(hits) > 10 else [])
    return "\n".join([head, "", *lines, "", f"Plan the trip in the app's Expert: {APP}expert. Check the fare on aegeanair.com before "
                      "booking. " + (f"Stop this alert: {stop}" if stop else "Close this issue to stop the alert."), f"<!-- alert best: {hits[0][1]} -->"])


def gh(*args):
    return subprocess.run(["gh", *args, "--repo", REPO], check=True, capture_output=True, text=True).stdout


def new_issue_link(orig, dest, cabin, unit, limit, start="", end=""):
    """A link to GitHub's new-issue form, filled in with an alert, for the app's alert form."""
    what = f"{limit:,.0f} miles" if unit == "miles" else f"€{limit:,.0f}"
    body = "\n".join([f"route: {orig}-{dest}", f"cabin: {cabin}", f"{'miles below' if unit == 'miles' else 'below'}: {limit:.0f}"]
                     + ([f"from: {start}"] if start else []) + ([f"to: {end}"] if end else [])
                     + ["", "Price alert from the AEGEAN fare tracker: the daily run checks it every morning and comments here when it is met. "
                            "Close this issue to stop it."])
    q = urllib.parse.urlencode({"title": f"Alert: {orig} → {dest} {cabin} below {what}", "labels": LABEL, "body": body})
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
    m["From"], m["To"], m["Subject"] = f"AEGEAN fare tracker <{user}>", to, subject
    m.set_content(text)
    with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"), 465, timeout=30) as s:
        s.login(user, password)
        s.send_message(m)


def _plain(md):
    """The message without its hidden marker and markdown bold, for email and phone."""
    return MARK.sub("", md).replace("**", "").strip()


def handle(issue, alert, today=None):
    """Deliver one alert. Returns the issue body to save (with what was told), or None when nothing changed."""
    body, n = issue["body"], issue["number"]
    labels = {l["name"] for l in issue.get("labels", [])}
    told = [float(m) for m in MARK.findall(body)] + [float(m) for c in issue.get("comments", []) for m in MARK.findall(c["body"])]
    stop = link("stop", n, alert["code"]) if alert["code"] else None
    if alert["email"] and CONFIRMED not in labels:  # an unconfirmed address gets one confirmation email, nothing else
        if SENT in body or not alert["code"]:
            return None
        what = f"{alert['orig']} → {alert['dest']} {alert['cabin']}"
        mail(alert["email"], f"Confirm your price alert: {what}",
             f"Someone (hopefully you) asked the AEGEAN fare tracker to email this address when {what} drops below your limit.\n\n"
             f"Turn the alert on: {link('confirm', n, alert['code'])}\n\nNot you? Ignore this email and nothing more will be sent.")
        return body + "\n" + SENT
    msg = message(alert, matches(alert, today), min(told) if told else None, stop)
    if not msg:
        return None
    title = f"{alert['orig']}-{alert['dest']} {alert['cabin']}: price alert"
    if alert["ntfy"]:
        push(alert["ntfy"], title, _plain(msg), f"{APP}expert")
    elif alert["email"]:
        mail(alert["email"], title, _plain(msg))
    else:
        gh("issue", "comment", str(n), "--body", msg)  # an alert written by hand: GitHub notifies its author
        return None
    return MARK.sub("", body).rstrip() + "\n" + MARK.search(msg)[0]  # remember the best told, without a comment that would notify


def run():
    gh("label", "create", LABEL, "--color", "2563C9", "--description", "Price alert checked by the daily run", "--force")
    issues = json.loads(gh("issue", "list", "--label", LABEL, "--state", "open", "--limit", "500",
                           "--json", "number,title,body,comments,labels"))
    for i in issues:
        alert = parse(i["body"])
        if not alert:
            print(f"#{i['number']}: not an alert I can read (needs route, cabin and below)")
            continue
        try:
            new = handle(i, alert)
        except Exception as err:  # one bad address or a service hiccup must not stop the other alerts
            print(f"#{i['number']}: could not deliver ({type(err).__name__}: {err})")
            continue
        print(f"#{i['number']} {i['title']}: {'sent' if new else 'nothing new'}")
        if new:
            gh("issue", "edit", str(i["number"]), "--body", new)


if __name__ == "__main__":
    run()
