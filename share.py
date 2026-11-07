"""Shareable Overview searches (a URL that restores the pickers) and the calendar file for a trip. Pure functions, stdlib only."""
import re
from datetime import date, datetime, timedelta, timezone


def parse_search(q, froms, dests, today, horizon, anywhere="ANY"):
    """The valid parts of a query string (any mapping of str to str) as {from, to, when, trip, nights, cabin, pay, pax}.
    It is user input: anything unknown, malformed or out of range is left out, never raised."""
    out = {}
    get = lambda k: q.get(k) if isinstance(q.get(k), str) else ""
    if get("from") in froms:
        out["from"] = get("from")
    if get("to") in dests or get("to") == anywhere:
        out["to"] = get("to")
    try:
        days = [date.fromisoformat(s) for s in re.split(r"\.\.|,", get("when"))]
        if len(days) in (1, 2) and today + timedelta(1) <= days[0] <= days[-1] <= today + timedelta(horizon):
            out["when"] = (days[0], days[-1])
    except ValueError:
        pass
    if get("trip") in ("return", "oneway"):
        out["trip"] = get("trip")
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", get("nights"))
    if m and 1 <= int(m[1]) <= int(m[2]) <= 60:
        out["nights"] = (int(m[1]), int(m[2]))
    if get("cabin") in ("economy", "business"):
        out["cabin"] = get("cabin")
    if get("pay") in ("cash", "miles"):
        out["pay"] = get("pay")
    if get("pax").isdigit() and 1 <= int(get("pax")) <= 9:
        out["pax"] = int(get("pax"))
    return out


def build_search(orig, dest, when, trip, nights, cabin, pay, pax):
    """The query string values (all str) that parse_search reads back into the same search."""
    d = {"from": orig, "to": dest, "when": "..".join(dict.fromkeys(x.isoformat() for x in when)), "trip": trip,
         "cabin": cabin, "pay": pay, "pax": str(pax)}
    if trip == "return":
        d["nights"] = f"{nights[0]}-{nights[1]}"
    return d


def _esc(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics(events, now=None):
    """An iCalendar file (str) of all-day events: events is a list of (day, title, description)."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//FlyCheap//EN", "CALSCALE:GREGORIAN"]
    for i, (day, title, desc) in enumerate(events):
        lines += ["BEGIN:VEVENT", f"UID:flycheap-{day:%Y%m%d}-{i}@flycheap", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{day + timedelta(1):%Y%m%d}", f"SUMMARY:{_esc(title)}", f"DESCRIPTION:{_esc(desc)}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    # ponytail: no 75-octet line folding; calendar apps accept long lines
    return "\r\n".join(lines) + "\r\n"
