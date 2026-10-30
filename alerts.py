"""Price alerts, free: each alert is an open GitHub issue labelled `price-alert`, made from the app's alert form (a pre-filled 'new issue'
link) or by hand. After the daily run saves the day's fares, `python alerts.py` (in .github/workflows/daily.yml) checks every alert and
comments on its issue when it is met, so GitHub's own notifications (email, phone app) carry it. Closing the issue stops the alert.

An alert's issue body is one setting per line:
    route: ATH-ZRH
    cabin: business
    below: 250          (euros; or `miles below: 16000` for award miles)
    from: 2026-12-01    (optional: first and last departure day)
    to: 2026-12-31
It comments the first time the alert is met and again only when a cheaper fare (or fewer miles) appears, never the same news twice."""
import csv
import json
import os
import re
import subprocess
import urllib.parse
from datetime import date

REPO = "Am015-dev/AegeanBook"
APP = "https://aegeanbook-fdb67dfpcw4qxu8t2ornzt.streamlit.app/"
LABEL = "price-alert"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MARK = re.compile(r"<!-- alert best: ([\d.]+) -->")


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
                start=kv.get("from", ""), end=kv.get("to", ""))


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


def message(alert, hits, last):
    """The comment for an alert that is met, or None when there is nothing new: met for the first time, or cheaper than last told."""
    if not hits or (last is not None and hits[0][1] >= last):
        return None
    fmt = (lambda v: f"{v:,.0f} miles") if alert["unit"] == "miles" else (lambda v: f"€{v:,.0f}")
    day = lambda d: date.fromisoformat(d).strftime("%a %d %b")
    head = (f"**{alert['orig']} → {alert['dest']} {alert['cabin']}** is now {fmt(hits[0][1])} on {day(hits[0][0])}"
            + (f", below your limit of {fmt(alert['limit'])}" if last is None else f", cheaper than the {fmt(last)} I told you about")
            + f": {len(hits)} day{'s' * (len(hits) > 1)} meet the alert.")
    lines = [f"- {day(d)}: {fmt(v)}" for d, v in hits[:10]] + ([f"- …and {len(hits) - 10} more"] if len(hits) > 10 else [])
    return "\n".join([head, "", *lines, "", f"Plan the trip in the app's [Expert]({APP}expert), and check the fare on aegeanair.com "
                      "before booking. Close this issue to stop the alert.", f"<!-- alert best: {hits[0][1]} -->"])


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


def run():
    gh("label", "create", LABEL, "--color", "2563C9", "--description", "Price alert checked by the daily run", "--force")
    issues = json.loads(gh("issue", "list", "--label", LABEL, "--state", "open", "--limit", "100", "--json", "number,title,body,comments"))
    for i in issues:
        alert = parse(i["body"])
        if not alert:
            print(f"#{i['number']}: not an alert I can read (needs route, cabin and below)")
            continue
        told = [float(m) for c in i["comments"] for m in MARK.findall(c["body"])]
        msg = message(alert, matches(alert), min(told) if told else None)
        print(f"#{i['number']} {i['title']}: {'comment' if msg else 'nothing new'}")
        if msg:
            gh("issue", "comment", str(i["number"]), "--body", msg)


if __name__ == "__main__":
    run()
