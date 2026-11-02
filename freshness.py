"""How old is each dataset? Read from the `fetched` column of the CSV each collector writes, so the Health page can say what is stale
and why, instead of the app quietly showing old numbers."""
import csv
import os
from datetime import date, timedelta

# (name, file, days before it counts as late, how it is collected)
SOURCES = [
    ("Cash fares", "daily.csv", 2, "Collected every morning by the GitHub Action 'Daily fares'. If it is late, open the Actions tab on GitHub."),
    ("Hotel prices", "daily_hotel.csv", 2, "Collected every morning by the same GitHub Action."),
    ("Award miles", "daily_award.csv", 35, "Collected once a month on the owner's computer (award_daily.bat), not by GitHub. It only updates "
                                          "when that computer runs it."),
]


def latest_fetched(path):
    """The newest `fetched` stamp (ISO text) in a CSV, or None when the file is missing, empty or has no such column."""
    if not os.path.exists(path) or not os.path.getsize(path):
        return None
    with open(path, newline="", encoding="utf-8") as f:
        stamps = [r["fetched"] for r in csv.DictReader(f) if r.get("fetched")]
    return max(stamps) if stamps else None


def freshness(data_dir, today=None):
    """[(name, last stamp or None, age in days or None, level, how)] with level ok / late (up to three times the limit) / stale."""
    today, out = today or date.today(), []
    for name, file, limit, how in SOURCES:
        last = latest_fetched(os.path.join(data_dir, file))
        if last is None:
            out.append((name, None, None, "stale", how))
            continue
        age = (today - date.fromisoformat(last[:10])).days
        out.append((name, last, age, "ok" if age <= limit else "late" if age <= 3 * limit else "stale", how))
    return out


def award_coverage(data_dir, today=None, days=365):
    """[(leg, last collected, updated, older, missing, updated up to)] of the award file, next `days` days, nonstop rows. A day is updated when
    it came from the leg's latest collection day. "Updated up to" is the last day of the unbroken updated stretch from tomorrow, which is what
    can be trusted without looking further. ponytail: "latest" is one calendar day, so a year split over several days shows the last part only."""
    today = today or date.today()
    path = os.path.join(data_dir, "daily_award.csv")
    if not os.path.exists(path) or not os.path.getsize(path):
        return []
    span = [(today + timedelta(i)).isoformat() for i in range(1, days + 1)]
    legs = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("nonstop") == "1" and span[0] <= r["day"] <= span[-1]:
                seen = legs.setdefault(f"{r['orig']} → {r['dest']}", {})
                seen[r["day"]] = max(seen.get(r["day"], ""), r["fetched"][:10])
    out = []
    for leg, seen in sorted(legs.items()):
        last = max(seen.values())
        updated = {d for d, f in seen.items() if f == last}
        up_to = None
        for d in span:
            if d not in updated:
                break
            up_to = d
        out.append((leg, last, len(updated), len(seen) - len(updated), len(span) - len(seen), up_to))
    return out


if __name__ == "__main__":  # self-check
    import tempfile
    d = tempfile.mkdtemp()
    open(os.path.join(d, "daily.csv"), "w").write("day,fetched\nx,2026-10-27T07:00:00\ny,2026-10-29T07:00:00\n")
    r = {x[0]: x for x in freshness(d, date(2026, 10, 30))}
    assert r["Cash fares"][2] == 1 and r["Cash fares"][3] == "ok" and r["Award miles"][3] == "stale" and r["Award miles"][1] is None
    assert freshness(d, date(2026, 11, 3))[0][3] == "late" and freshness(d, date(2026, 12, 1))[0][3] == "stale"
    with open(os.path.join(d, "daily_award.csv"), "w") as f:
        f.write("day,orig,dest,nonstop,fetched\n2026-11-02,ATH,FRA,1,2026-11-01T07:00\n2026-11-03,ATH,FRA,1,2026-10-20T07:00\n2026-11-04,ATH,FRA,1,2026-11-01T07:00\n2026-11-02,ATH,ZRH,0,2026-11-01T07:00\n")
    assert award_coverage(d, date(2026, 11, 1), 5) == [("ATH → FRA", "2026-11-01", 2, 1, 2, "2026-11-02")]
    print("ok")
