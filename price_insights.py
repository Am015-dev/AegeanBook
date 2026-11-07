"""Google Flights' price insights from our own history: is this fare Low, Typical or High for the route, and how did it move? The data is
data/history_prices.csv, where collect_daily.py and fetch_route.py keep every fare change ever seen (one row per change, never trimmed).
Pure pandas; the page is views/overview.py.
A new module on purpose: Streamlit Cloud can run a page against older, already loaded modules."""
import os

import pandas as pd

import gh

VERSION = 4  # pages reload this module when Streamlit Cloud still has an older copy loaded

HISTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "history_prices.csv")
WINDOW = 60  # days of snapshots a verdict looks back over, as Google's graph does
MIN_DAYS = 7  # distinct fetch days a route needs before its fares are called low or high
COLUMNS = ["day", "orig", "dest", "cabin", "price", "source", "fetched", "nonstop"]


def history(path=None):
    """The history CSV as a DataFrame (COLUMNS; `fetched` as timestamps, `price` numeric, NaN on a day with no flight); empty with the same
    columns when there is no file yet. Without `path`, the repository's newest copy when GitHub can be read (gh.data_file), else the file
    the app was deployed with."""
    path = path or gh.data_file("data/history_prices.csv") or HISTORY
    if not os.path.exists(path) or not os.path.getsize(path):
        return pd.DataFrame(columns=COLUMNS)
    h = pd.read_csv(path, dtype=str)
    h["price"] = pd.to_numeric(h["price"], errors="coerce").astype(float)
    h["fetched"] = pd.to_datetime(h["fetched"], errors="coerce")
    if "nonstop" not in h:  # a file from before the column existed
        h["nonstop"] = "1"
    return h[h["fetched"].notna()]


def _snapshots(hist, orig, dest, cabin):
    """The route's fares as one frame: a row per day the route was fetched (DatetimeIndex), a column per departure day (iso), the fare that
    fetch saw. The CSV stores only changes, so a fare is carried forward until the next change (collect_daily keeps yesterday's price when
    a day fails), -1 where the day had no flight, NaN before the day was first seen and after it has gone by (no longer on sale, so a stale
    fare must not count). Nonstop rows only: the Overview compares nonstop fares. Empty when the route was never fetched."""
    pair = hist[(((hist["orig"] == orig) & (hist["dest"] == dest)) | ((hist["orig"] == dest) & (hist["dest"] == orig)))
                & (hist["nonstop"].astype(str) != "0")]
    r = pair[(pair["orig"] == orig) & (pair["cabin"] == cabin)]
    if not len(r):
        return pd.DataFrame(index=pd.DatetimeIndex([]))
    # ponytail: a fetch leaves rows only where a fare changed, so the days the route was fetched are read off the whole pair (both ways,
    # both cabins, which the daily run and the Flights page fetch together); a day on which none of its ~360 fares moved is missed
    at = pd.DatetimeIndex(sorted(set(pair["fetched"].dt.normalize())))
    r = r.assign(at=r["fetched"].dt.normalize(), price=r["price"].fillna(-1)).sort_values("fetched")
    w = r.groupby(["at", "day"])["price"].last().unstack("day").reindex(at).ffill()  # the day's latest fare when fetched twice a day
    gone = pd.DataFrame({d: w.index > pd.Timestamp(d) for d in w.columns}, index=w.index)
    return w.mask(gone)


def _range(hist, orig, dest, cabin, today):
    """(low, high, snapshots, since) for the route: the 25th and 75th percentiles of every one-way fare in every snapshot of the last
    WINDOW days, how many fetch days that is, and the first day the route was ever fetched (dates). low/high are None under MIN_DAYS days."""
    w = _snapshots(hist, orig, dest, cabin)
    since = w.index.min().date() if len(w) else None
    w = w[w.index >= pd.Timestamp(today) - pd.Timedelta(days=WINDOW)].where(lambda v: v >= 0)  # -1 is "no flight", not a fare
    fares = pd.Series(w.to_numpy().ravel(), dtype=float).dropna()
    days = int(w.notna().any(axis=1).sum())
    if days < MIN_DAYS:
        return None, None, days, since
    return float(fares.quantile(0.25)), float(fares.quantile(0.75)), days, since


def _level(price, low, high):
    if low is None or price is None or pd.isna(price):
        return None
    return "low" if price < low else "high" if price > high else "typical"


def verdict(hist, orig, dest, cabin, price, today):
    """Is `price` (a one-way fare) low, typical or high for the route? dict(level, low, high, snapshots, since): low/high are the 25th and
    75th percentiles of every one-way fare seen for the route, on any departure day, in any snapshot of the last WINDOW days; level is
    "low" under them, "high" above, "typical" between. `snapshots` counts the distinct fetch days in that window and `since` is the first
    day the route was ever fetched (dates), so the page can say how much history there is. With fewer than MIN_DAYS snapshots (a guess
    from three days of prices would mislead), or without a price, level is None."""
    low, high, days, since = _range(hist, orig, dest, cabin, today)
    return dict(level=_level(price, low, high), low=low, high=high, snapshots=days, since=since)


def trip(hist, legs, cabin, price, today):
    """verdict() for a whole trip: `legs` [(orig, dest), ...] and `price` their fares added up; low/high are the legs' percentiles added
    up. When any leg has too little history, level is None with the fewest snapshots and the earliest `since` among the legs."""
    rs = [_range(hist, o, d, cabin, today) for o, d in legs]
    snapshots = min(r[2] for r in rs)
    since = min((r[3] for r in rs if r[3]), default=None)
    if any(r[0] is None for r in rs):
        return dict(level=None, low=None, high=None, snapshots=snapshots, since=since)
    low, high = sum(r[0] for r in rs), sum(r[1] for r in rs)
    return dict(level=_level(price, low, high), low=low, high=high, snapshots=snapshots, since=since)


def series(hist, orig, dest, cabin, day):
    """How the fare for one departure `day` (iso) moved: Series of the fare on every day the route was fetched, from the first time that
    departure day was seen until it had gone by, indexed by the fetch day (iso), oldest first; NaN where the day had no flight. The CSV
    stores only changes, so an unchanged fare repeats (see _snapshots). Empty when the day was never seen."""
    w = _snapshots(hist, orig, dest, cabin)
    if day not in w.columns:
        return pd.Series(dtype=float)
    s = w[day]
    s = s[s.index >= s.first_valid_index()].dropna()
    return pd.Series(s.where(s >= 0).values, index=[d.strftime("%Y-%m-%d") for d in s.index], dtype=float)


def trip_series(hist, legs, cabin):
    """series() for a whole trip: `legs` [(orig, dest, iso day), ...]; the legs' fares added up on every fetch day all of them had a fare."""
    parts = [series(hist, o, d, cabin, day) for o, d, day in legs]
    return pd.concat(parts, axis=1).dropna().sum(axis=1) if all(len(x) for x in parts) else pd.Series(dtype=float)


def trend(s):
    """The story of a trip_series() (one adult's fares), the one the Overview's note and the Expert both tell: dict(days, first, last, low,
    low_day, high, since, week_ago) with low_day and since as dates and week_ago the last fare at least seven days before the newest (None
    when the history is shorter); None under 2 checks."""
    s = s.dropna()
    if len(s) < 2:
        return None
    days = pd.to_datetime(pd.Index(s.index))
    ago = s[days <= days[-1] - pd.Timedelta(days=7)]
    return dict(days=len(s), first=float(s.iloc[0]), last=float(s.iloc[-1]), low=float(s.min()), low_day=pd.Timestamp(s.idxmin()).date(),
                high=float(s.max()), since=days[0].date(), week_ago=float(ago.iloc[-1]) if len(ago) else None)


def moved_note(s, pax=1):
    """The one-line caption for a trip_series() (fares of one adult, scaled to `pax` adults like the pass): lowest seen and when, now,
    and since when / how many checks (trend()); None under 2 checks."""
    t = trend(s)
    if t is None:
        return None
    return (f"Lowest seen €{t['low'] * pax:,.0f} on {t['low_day']:%-d %b}, now €{t['last'] * pax:,.0f}{f' for {pax} adults' if pax > 1 else ''}. "
            f"Watching since {t['since']:%-d %b} ({t['days']} checks).")


if __name__ == "__main__":  # self-check against the committed history
    from datetime import date
    h = history(HISTORY)
    print(len(h), "rows;", verdict(h, "ATH", "FRA", "economy", 100, date.today()))
    print(series(h, "ATH", "FRA", "economy", h["day"].iloc[0]).to_dict())
