"""Price insights (low / typical / high from our own history), offline: a small history CSV and the Overview page with a seeded database."""
import os
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

import price_insights as pi
from test_overview import pages, seed

HERE = os.path.dirname(os.path.abspath(__file__))


def write_history(path, days, fares=(100, 110, 120, 130, 140, 150, 160, 170), legs=(("ATH", "FRA"), ("FRA", "ATH")), start=None):
    """A history CSV like collect_daily.save() writes: one snapshot a day for `days` days from `start` (default: today - days + 1), each
    pricing eight departure days at `fares`, so the 25th/75th percentiles are 117.5 / 152.5 whatever the day. ATH-FRA's first departure
    day gets cheaper by €5 every snapshot (a fare that moves); every other row repeats, as the real file only stores changes."""
    start = start or date.today() - timedelta(days - 1)
    rows = []
    for i in range(days):
        at = datetime.combine(start + timedelta(i), datetime.min.time()).replace(hour=7, minute=17)
        for o, d in legs:
            for n, f in enumerate(fares):
                price = f - 5 * i if (o, d, n) == ("ATH", "FRA", 0) else f
                if i and price == f:
                    continue  # unchanged: the real file has no row
                rows.append(dict(day=(date.today() + timedelta(10 + n)).isoformat(), orig=o, dest=d, cabin="economy", price=price,
                                 source="calendar", fetched=at.isoformat(timespec="seconds"), nonstop=1))
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_history_reads_the_csv_and_copes_without_one(tmp_path):
    assert pi.history(tmp_path / "none.csv").empty and list(pi.history(tmp_path / "none.csv").columns) == pi.COLUMNS
    h = pi.history(write_history(tmp_path / "h.csv", 3))
    assert len(h) == 2 * 8 + 2 and h["fetched"].dtype.kind == "M" and h["price"].dtype.kind == "f"
    (tmp_path / "gap.csv").write_text("day,orig,dest,cabin,price,source,fetched,nonstop\n2026-11-01,ATH,FRA,economy,,calendar,2026-10-29T07:00:00,1\n")
    assert pi.history(tmp_path / "gap.csv")["price"].isna().all()  # a day without a flight: empty price, kept as NaN


def test_verdict_thresholds_and_too_little_history(tmp_path):
    h = pi.history(write_history(tmp_path / "h.csv", 7))
    today = date.today()
    v = pi.verdict(h, "ATH", "FRA", "economy", 100, today)
    assert v["level"] == "low" and v["snapshots"] == 7 and v["since"] == today - timedelta(6)
    assert v["low"] == pytest.approx(117.5, abs=1) and v["high"] == pytest.approx(152.5, abs=1)  # the moving fare nudges the quartiles a little
    assert pi.verdict(h, "ATH", "FRA", "economy", 130, today)["level"] == "typical"
    assert pi.verdict(h, "ATH", "FRA", "economy", 170, today)["level"] == "high"
    assert pi.verdict(h, "ATH", "FRA", "economy", None, today)["level"] is None  # no fare, no verdict, but the history is still described
    assert pi.verdict(h, "ATH", "FRA", "business", 100, today) == dict(level=None, low=None, high=None, snapshots=0, since=None)  # never fetched
    few = pi.history(write_history(tmp_path / "few.csv", 6))
    v = pi.verdict(few, "ATH", "FRA", "economy", 100, today)
    assert v["level"] is None and v["low"] is None and v["snapshots"] == 6 and v["since"] == today - timedelta(5)
    old = pi.history(write_history(tmp_path / "old.csv", 10, start=today - timedelta(100)))  # snapshots older than 60 days do not count
    assert pi.verdict(old, "ATH", "FRA", "economy", 100, today)["snapshots"] == 0
    trip = pi.trip(h, [("ATH", "FRA"), ("FRA", "ATH")], "economy", 200, today)
    assert trip["level"] == "low" and trip["low"] == pytest.approx(235, abs=2) and trip["high"] == pytest.approx(305, abs=2)  # both legs added up
    assert pi.trip(h, [("ATH", "FRA"), ("FRA", "ATH")], "economy", 400, today)["level"] == "high"
    half = pi.trip(h, [("ATH", "FRA"), ("ATH", "ZRH")], "economy", 200, today)  # one leg never fetched: no verdict, the fewest snapshots
    assert half["level"] is None and half["snapshots"] == 0 and half["since"] == today - timedelta(6)


def test_series_carries_the_last_fare_forward_over_the_snapshots(tmp_path):
    h = pi.history(write_history(tmp_path / "h.csv", 4))
    today = date.today()
    moving = pi.series(h, "ATH", "FRA", "economy", (today + timedelta(10)).isoformat())
    assert list(moving.index) == [(today - timedelta(3 - i)).isoformat() for i in range(4)] and list(moving) == [100, 95, 90, 85]
    flat = pi.series(h, "ATH", "FRA", "economy", (today + timedelta(11)).isoformat())  # one row in the file, four snapshots on the chart
    assert len(flat) == 4 and list(flat) == [110, 110, 110, 110] and flat.index[0] == moving.index[0]
    assert pi.series(h, "ATH", "FRA", "economy", "2030-01-01").empty and pi.series(h, "ZRH", "ATH", "economy", moving.index[0]).empty


def test_overview_shows_the_verdict_and_how_the_fare_moved(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch, None)
    seed(tmp_path / "tracker.db")
    today, read = date.today(), pi.history
    use = lambda path: monkeypatch.setattr(pi, "history", lambda p=None: read(path))  # the page reads this history instead of the repo's
    # the pass: ATH -> FRA €60 on day 10, back on day 14 (€150): the history prices exactly those days, so both legs have a series
    use(write_history(tmp_path / "h.csv", 7))
    at = open_page("overview.py").run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    v = pi.trip(read(tmp_path / "h.csv"), [("ATH", "FRA"), ("FRA", "ATH")], "economy", 210, today)
    moved = lambda: [x for x in at.expander if x.label == "How this fare moved"]
    assert v["level"] == "low" and f"Usually €{v['low']:,.0f}–€{v['high']:,.0f} return. €210 is cheap." in text  # two plain sentences
    assert moved() and "The ring marks the lowest" in moved()[0].caption[0].value
    use(write_history(tmp_path / "few.csv", 3))
    at = open_page("overview.py").run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Tracking since" not in text and "Usually €" not in text and "usually cost about €" in text  # too little history: no verdict, no jargon
    assert moved()  # three snapshots are enough for a line
    use(write_history(tmp_path / "two.csv", 2))
    at = open_page("overview.py").run()
    assert not at.exception and "days of prices" not in " ".join(m.value for m in at.markdown) and not moved()  # two points are not a line
    at.selectbox(key="ov-grid-orig").set_value("ZRH").run()  # a route the history has never seen: nothing is claimed about it
    assert not at.exception and "Usually €" not in " ".join(m.value for m in at.markdown) and not moved()
