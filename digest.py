"""What changed since the last run: fares that dropped or rose, award seats that appeared or went. Read from the permanent history files, which
store a row only when a value changes, so the rows recorded on the latest day ARE the changes (apart from days seen for the first time, which
are new, not changes). Standard library only: the daily GitHub job runs this without pandas.
Writes data/latest_changes.md (empty when nothing changed); the Database page shows it and a GitHub issue can be opened from it."""
import csv
import os
from datetime import date

LIMIT = 15  # lines per section; the rest are counted


def _rows(path):
    if not os.path.exists(path) or not os.path.getsize(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return sorted(csv.DictReader(f), key=lambda r: r["fetched"])


def _num(x):
    try:
        return float(x) if x not in ("", None) else None
    except ValueError:
        return None


def find_changes(rows, value):
    """[(row, previous value, new value)] for rows recorded on the latest day that replace an earlier value of the same day, route, cabin and
    nonstop flag. A row with no earlier one is a day seen for the first time and is not a change."""
    if not rows:
        return []
    latest = max(r["fetched"][:10] for r in rows)
    last, out = {}, []
    for r in rows:
        k = (r.get("day"), r.get("orig"), r.get("dest"), r.get("cabin"), r.get("nonstop"))
        if r["fetched"][:10] == latest and k in last:
            out.append((r, last[k], _num(r.get(value))))
        last[k] = _num(r.get(value))
    return out


def _where(r):
    return f"{r['orig']} → {r['dest']}, {date.fromisoformat(r['day']):%a %d %b}, {r['cabin']}"


def build(data_dir):
    """The digest as markdown, or '' when nothing changed."""
    prices = find_changes(_rows(os.path.join(data_dir, "history_prices.csv")), "price")
    miles = find_changes(_rows(os.path.join(data_dir, "award_history.csv")), "miles")
    sections = []

    def section(title, lines):
        if lines:
            sections.append(f"## {title} ({len(lines)})\n" + "\n".join(lines[:LIMIT]) + (f"\n- and {len(lines) - LIMIT} more" if len(lines) > LIMIT else ""))
    drops = sorted([c for c in prices if c[1] is not None and c[2] is not None and c[2] < c[1]], key=lambda c: c[2] - c[1])
    rises = sorted([c for c in prices if c[1] is not None and c[2] is not None and c[2] > c[1]], key=lambda c: c[1] - c[2])
    section("Cheaper fares", [f"- {_where(r)}: €{old:.0f} → €{new:.0f} (−€{old - new:.0f})" for r, old, new in drops])
    section("Dearer fares", [f"- {_where(r)}: €{old:.0f} → €{new:.0f} (+€{new - old:.0f})" for r, old, new in rises])
    section("New award seats", [f"- {_where(r)}: {new:,.0f} miles (was none)" for r, old, new in miles if old is None and new is not None])
    section("Award seats gone", [f"- {_where(r)}: was {old:,.0f} miles" for r, old, new in miles if old is not None and new is None])
    section("Award miles changed", [f"- {_where(r)}: {old:,.0f} → {new:,.0f} miles" for r, old, new in miles if old is not None and new is not None and old != new])
    if not sections:
        return ""
    latest = max(r["fetched"][:10] for r, _, _ in prices + miles)
    return f"# What changed on {date.fromisoformat(latest):%d %b %Y}\n\n" + "\n\n".join(sections) + "\n"


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    md = build(here)
    with open(os.path.join(here, "latest_changes.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    print(md or "No changes since the last run.")
    if os.environ.get("GITHUB_OUTPUT"):  # lets a later workflow step open an issue only when there is something to say
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"changed={'true' if md else 'false'}\n")
