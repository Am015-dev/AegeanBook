"""Daily collector (run by .github/workflows/daily.yml): both cabins, nonstop, for the next 90 days on four legs.
Writes data/daily.csv, which the app loads on start. Retries failed days over several passes; exits 1 if any are still missing."""
import csv
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from collector import fetch_range

LEGS = [("ATH", "FRA"), ("FRA", "ATH"), ("ATH", "ZRH"), ("ZRH", "ATH")]
DAYS, PASSES, PAUSE = 90, 4, 120  # PAUSE seconds between passes lets a Google rate limit cool down
OUT = Path(__file__).parent / "data" / "daily.csv"
FIELDS = ["day", "orig", "dest", "cabin", "price", "source", "fetched", "nonstop"]


def collect(fetch=fetch_range, today=None, pause=PAUSE):
    today = today or date.today()
    start, end = today + timedelta(1), today + timedelta(DAYS)
    want = {(d.isoformat(), o, t, c) for o, t in LEGS for c in ("economy", "business")
            for d in (start + timedelta(i) for i in range(DAYS))}
    got = {}
    for n in range(PASSES):
        for o, t in LEGS:
            skip = {(day, c) for day, o2, t2, c in got if (o2, t2) == (o, t)}
            if len(skip) == 2 * DAYS:
                continue
            for d, cabin, price, source, status, err in fetch(start, end, o, t, skip=skip, nonstop=True):
                if status != "error":
                    got[(d.isoformat(), o, t, cabin)] = (price, source)
                elif err:
                    print(f"  {o}-{t} {d} {cabin}: {err}")
        missing = want - got.keys()
        print(f"pass {n + 1}: {len(got)}/{len(want)} collected, {len(missing)} missing", flush=True)
        if not missing:
            break
        if n < PASSES - 1:
            time.sleep(pause)
    return got, want - got.keys()


def save(got, path=OUT):
    """Overwrite the file with today's results; days that failed today keep yesterday's row, so the app always has a price."""
    now = datetime.now().isoformat(timespec="seconds")
    rows = {}
    if path.exists():
        with path.open() as f:
            rows = {(r["day"], r["orig"], r["dest"], r["cabin"]): r for r in csv.DictReader(f)}
    for (day, o, t, c), (price, source) in got.items():
        rows[(day, o, t, c)] = dict(day=day, orig=o, dest=t, cabin=c, price="" if price is None else price, source=source,
                                    fetched=now, nonstop=1)
    today = date.today().isoformat()
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(r for k, r in sorted(rows.items()) if k[0] > today)  # past days are dropped


if __name__ == "__main__":
    got, missing = collect()
    save(got)
    priced = sum(p is not None for p, _ in got.values())
    print(f"done: {priced} prices, {len(got) - priced} days with no flight, {len(missing)} missing")
    sys.exit(1 if missing else 0)
