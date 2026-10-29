"""How old is each dataset? Read from the `fetched` column of the CSV each collector writes, so the Health page can say what is stale
and why, instead of the app quietly showing old numbers."""
import csv
import os
from datetime import date

# (name, file, days before it counts as late, how it is collected)
SOURCES = [
    ("Cash fares", "daily.csv", 2, "Collected every morning by the GitHub Action 'Daily fares'. If it is late, open the Actions tab on GitHub."),
    ("Hotel prices", "daily_hotel.csv", 2, "Collected every morning by the same GitHub Action."),
    ("Award miles", "daily_award.csv", 3, "Collected on the owner's computer (award_daily.bat), not by GitHub. It only updates when that "
                                         "computer runs it."),
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


if __name__ == "__main__":  # self-check
    import tempfile
    d = tempfile.mkdtemp()
    open(os.path.join(d, "daily.csv"), "w").write("day,fetched\nx,2026-10-27T07:00:00\ny,2026-10-29T07:00:00\n")
    r = {x[0]: x for x in freshness(d, date(2026, 10, 30))}
    assert r["Cash fares"][2] == 1 and r["Cash fares"][3] == "ok" and r["Award miles"][3] == "stale" and r["Award miles"][1] is None
    assert freshness(d, date(2026, 11, 3))[0][3] == "late" and freshness(d, date(2026, 12, 1))[0][3] == "stale"
    print("ok")
