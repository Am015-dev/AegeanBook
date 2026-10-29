import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from collector import fetch_range
from db import RETENTION_DAYS, connect, save_awards, saved_flight_days
from jobs import record, start_job, track
from ui import (BUSINESS, ECONOMY, MUTED, airport_picker, best_days, best_trips, excel_bytes, kpis, near_cheapest, on_weekdays,
                price_chart, round_trips, route, scraped, smart_filters, window_picker)

db = connect()
today = date.today()
HORIZON = 330  # Google's calendar reaches about 11 months ahead; later days would look like "no flight"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AWARD_PROFILE = os.path.join(ROOT, ".aegean_profile")  # exists only after the one-time `python award_collector.py login`
AWARD_LEGS = [("ATH", "FRA"), ("FRA", "ATH"), ("ATH", "ZRH"), ("ZRH", "ATH")]  # the legs collect_daily.py also tracks
AWARD_DAYS = 90


@st.cache_data(ttl=3600, show_spinner=False)
def award_history_xlsx(path, mtime):
    """(xlsx bytes, snapshot count, last snapshot day) of the whole history, built once per file version instead of on every rerun."""
    h = pd.read_csv(path)
    if h.empty:
        return None
    h = h.rename(columns={"fetched": "collected", "miles": "award miles"}).sort_values(["collected", "orig", "dest", "day", "cabin"])
    return excel_bytes(h.set_index("collected")), h.collected.str[:10].nunique(), h.collected.max()[:10]


def award_job():
    """Button: run award_collector.py on this computer for the four legs. Its `DAY <iso> ok|error` lines drive the progress bar."""
    key = "awards|all"
    if st.button("Collect award miles now (90 days, 4 routes, a few minutes)", key="aw-go"):
        def work(j):
            pr = subprocess.Popen([sys.executable, "award_collector.py", "fetch", *[f"{o}-{d}" for o, d in AWARD_LEGS], "--days", str(AWARD_DAYS)],
                                  cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            last = ""
            for line in pr.stdout:
                last = line.strip() or last
                if line.startswith("DAY "):
                    record(j, "ok" if line.split()[-1] == "ok" else "error", "A day did not load; the next run retries it")
                if j["cancel"]:
                    pr.terminate()
                    return
            if pr.wait():
                j["error"] = last[-300:]
        start_job(key, len(AWARD_LEGS) * AWARD_DAYS, work, "{n} days.", "checked", "Award miles")
    track(key)

with st.container(border=True):
    c = st.columns(2)
    orig = airport_picker(c[0], "From", "ATH", "orig")
    dest = airport_picker(c[1], "To", "FRA", "dest")
    c = st.columns([1, 2])
    trip = c[0].pills("Trip", ["One-way", "Return"], default="One-way", required=True, key="fl-trip")
    nights = c[1].pills("Nights away", [2, 3, 4, 5, 7, 10, 14], selection_mode="multi", default=[7], key="fl-nights",
                        help="Pick one or more stay lengths; every combination is compared.") if trip == "Return" else []
    start, end = window_picker(st, "fl", today, HORIZON)
    c = st.columns(2)
    fetch = c[0].button("Update prices", type="primary", width="stretch")
    with c[1].popover("Options", width="stretch"):
        nonstop = st.checkbox("Nonstop flights only", value=True)
        recheck = st.checkbox("Re-check days already saved")
        st.caption(f"1 passenger, AEGEAN flights only, from Google's price calendar. A return is two one-way fares. Busy routes take seconds, quiet "
                   f"routes a few minutes. Results are kept {RETENTION_DAYS} days and reused.")

if orig == dest:
    st.error("From and To are the same airport.")
    st.stop()
if trip == "Return" and not nights:
    st.warning("Pick at least one stay length under Nights away.")
    st.stop()
# return flights leave between the first departure + shortest stay and the last departure + longest stay
rstart, rend = (start + timedelta(min(nights)), end + timedelta(max(nights))) if nights else (None, None)
if rend and rend > today + timedelta(HORIZON):
    st.error(f"The return dates would go past {today + timedelta(HORIZON):%d %b %Y}, as far as Google's calendar reaches. "
             "Choose an earlier start, a shorter window or fewer nights.")
    st.stop()

stay = f"return, {', '.join(map(str, sorted(nights)))} nights" if nights else "one-way"
route(orig, dest, f"{start:%a %d %b} – {end:%a %d %b %Y} · {(end - start).days + 1} days · {stay} · AEGEAN")

legs = [(orig, dest, start, end)] + ([(dest, orig, rstart, rend)] if nights else [])
key = f"flights|{orig}|{dest}|{start}|{end}|{nonstop}|{rend}"
if fetch:
    skips = [set() if recheck else saved_flight_days(db, o, d, nonstop, a, b) for o, d, a, b in legs]
    total = sum(2 * ((b - a).days + 1) for _, _, a, b in legs) - sum(map(len, skips))

    def work(j):
        tdb = connect()
        for (o, d_, a, b), skip in zip(legs, skips):  # outbound, then the way back
            for d, cabin, price, source, status, err in fetch_range(a, b, o, d_, skip=skip, nonstop=nonstop):
                if j["cancel"]:
                    return
                if status != "error":  # failed lookups are not saved, so the next run retries them
                    tdb.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)",  # `airlines` column holds the source
                                (d.isoformat(), o, d_, cabin, price, source, datetime.now().isoformat(), int(nonstop)))
                    tdb.commit()
                record(j, status, err)

    if total > 0:
        start_job(key, total, work, "{n} day and cabin combinations have no AEGEAN flight. AEGEAN may not fly this route on those "
                  "days, or only in some seasons. Google occasionally misses a day: tick Re-check days already saved (Options) if you "
                  "expect more.", "with no flight", f"{orig}–{dest} {start:%d %b}–{end:%d %b}" + (" return" if nights else ""))
    else:
        st.info("Every day in this range is already saved. Tick Re-check days already saved under Options to fetch again.")
track(key)

scraped([(f"{o} → {d_}", pd.Series([r[0] for r in db.execute(
    "SELECT MAX(fetched) FROM prices WHERE orig=? AND dest=? AND nonstop=? AND day BETWEEN ? AND ? GROUP BY day",
    (o, d_, int(nonstop), a.isoformat(), b.isoformat()))], dtype=object), (b - a).days + 1) for o, d_, a, b in legs])
q, args = "WHERE orig=? AND dest=? AND day BETWEEN ? AND ?", (orig, dest, start.isoformat(), end.isoformat())
p = pd.read_sql("SELECT * FROM prices WHERE orig=? AND dest=? AND nonstop=? AND day BETWEEN ? AND ? ORDER BY fetched",
                db, params=(orig, dest, int(nonstop), start.isoformat(), end.isoformat()))
seen = p.drop_duplicates(["day", "cabin"], keep="last")  # one row per day and cabin: priced, or checked with no flight
latest = seen.pivot(index="day", columns="cabin", values="price") if len(p) else pd.DataFrame()
days = pd.DataFrame(index=pd.date_range(start, end).strftime("%Y-%m-%d").rename("day"))
for k in ("economy", "business"):
    if k not in latest:
        latest[k] = float("nan")
a = pd.read_sql(f"SELECT day, miles, taxes FROM awards {q}", db, params=args).set_index("day")
out = days.join(latest).join(a)
try:  # award miles are an extra: if their table is missing (Streamlit Cloud can run this page against an older, already loaded
      # db.py until the app restarts) the page must still work, just without them
    am = pd.read_sql("SELECT day, cabin, miles FROM award_miles WHERE orig=? AND dest=? AND nonstop=? AND day BETWEEN ? AND ? ORDER BY fetched",
                     db, params=(orig, dest, int(nonstop), start.isoformat(), end.isoformat()))
    last_aw = db.execute("SELECT MAX(fetched) FROM award_miles WHERE orig=? AND dest=?", (orig, dest)).fetchone()[0]
except (pd.errors.DatabaseError, sqlite3.Error):
    am, last_aw = pd.DataFrame(), None
am = am.drop_duplicates(["day", "cabin"], keep="last").pivot(index="day", columns="cabin", values="miles") if len(am) else pd.DataFrame()
out = out.join(am.rename(columns={"economy": "econ_miles", "business": "biz_miles"}))
for k in ("econ_miles", "biz_miles"):
    if k not in out:
        out[k] = float("nan")
# ponytail: collected miles stay in their own columns. Filling `miles` with them would make the 'best miles value' metric use zero
# taxes (collected rows have none) and overstate it, so that metric stays typed-awards-only.
out["value"] = ((out.business - out.taxes.fillna(0)) / out.miles.where(out.miles > 0) * 1000).round(1)


tol, weekdays = smart_filters(st, "fl")
view = on_weekdays(out, weekdays)  # the cards, chips and chart only look at the chosen weekdays (of departure)
near = {c: near_cheapest(view[c], tol) for c in ("economy", "business")}
if nights:  # the way back, and every departure + stay combination
    pb = pd.read_sql("SELECT * FROM prices WHERE orig=? AND dest=? AND nonstop=? AND day BETWEEN ? AND ? ORDER BY fetched",
                     db, params=(dest, orig, int(nonstop), rstart.isoformat(), rend.isoformat()))
    back = pb.drop_duplicates(["day", "cabin"], keep="last").pivot(index="day", columns="cabin", values="price") if len(pb) else pd.DataFrame()
    back = pd.DataFrame(index=pd.date_range(rstart, rend).strftime("%Y-%m-%d").rename("day")).join(back)
    for k in ("economy", "business"):
        if k not in back:
            back[k] = float("nan")
    trips = round_trips(view, back, nights)


def best(col):
    if near[col].size:
        n = len(near[col])
        return f"€{near[col].iloc[0]:.0f}", (f"{pd.Timestamp(near[col].index[0]):%a %d %b}" +
                                             (f" · {n} days within {tol:.0%}" if tol and n > 1 else f" · {n} days at this price" if n > 1 else ""))
    return "—", "No AEGEAN flight found" if (seen.cabin == col).any() else "No data yet"


def best_trip(col):
    t = trips[trips.cabin == col]
    if t.empty:
        return "—", "Press Update prices to fetch the way back" if not len(pb) else "No return pair with flights both ways"
    r = t.iloc[0]
    return f"€{r.total:.0f}", f"{pd.Timestamp(r.depart):%a %d %b} → {pd.Timestamp(r.back):%a %d %b} · {r.nights} nights"


(ev, es), (bv, bs) = (best_trip("economy"), best_trip("business")) if nights else (best("economy"), best("business"))
vv, vs = (f"€{view.value.max():.0f} / 1,000 mi", f"{pd.Timestamp(view.value.idxmax()):%a %d %b} · cash minus taxes per 1,000 miles") \
    if view.value.notna().any() else ("—", "Add award prices in the Awards tab")
kind = "return" if nights else ""
kpis([(f"Cheapest {kind} economy".replace("  ", " "), ev, es, ECONOMY), (f"Cheapest {kind} business".replace("  ", " "), bv, bs, BUSINESS),
      ("Best miles value", vv, vs, MUTED)])
if nights:
    best_trips(trips[trips.cabin == "economy"], ECONOMY, "Cheap economy returns", tol)
    best_trips(trips[trips.cabin == "business"], BUSINESS, "Cheap business returns", tol)
else:
    best_days(near["economy"], ECONOMY, "Cheap economy days")
    best_days(near["business"], BUSINESS, "Cheap business days")

if len(seen) < 2 * len(days):
    st.caption(f"Checked {len(seen)} of {2 * len(days)} day and cabin combinations: {int(seen.price.notna().sum())} priced, "
               f"{int(seen.price.isna().sum())} with no flight, {2 * len(days) - len(seen)} not checked or failed. "
               "Press Update prices to check the rest.")
st.caption(f"Saved results are kept {RETENTION_DAYS} days. ATH–FRA and ATH–ZRH (both ways, next 90 days) are fetched "
           "automatically every morning.")
t1, t2, t3 = st.tabs(["Prices", "Awards", "Table"])
with t1:
    if view[["economy", "business"]].notna().any().any():
        st.markdown(f'<p class="card-title">{orig} → {dest}, cheap days ringed</p>' if nights else
                    '<p class="card-title">Daily AEGEAN fare, cheap days ringed</p>', unsafe_allow_html=True)
        st.plotly_chart(price_chart(view, {"economy": ("Economy", ECONOMY), "business": ("Business", BUSINESS)},
                                    near={c: list(near[c].index) for c in near}),
                        width="stretch", config={"displayModeBar": False})
    else:
        st.info("No AEGEAN fares on these days. Try other weekdays, a longer window, or turn off Nonstop flights only in Options."
                if len(p) else "No fares yet. Pick the route and dates above, then press **Update prices**.")
    if nights and back[["economy", "business"]].notna().any().any():
        bnear = {c: list(near_cheapest(back[c], tol).index) for c in ("economy", "business")}
        st.markdown(f'<p class="card-title">{dest} → {orig} (the way back), cheap days ringed</p>', unsafe_allow_html=True)
        st.plotly_chart(price_chart(back, {"economy": ("Economy", ECONOMY), "business": ("Business", BUSINESS)}, near=bnear),
                        width="stretch", config={"displayModeBar": False})
with t2:
    seen_aw = am.index.size if len(am) else 0
    if not seen_aw or not last_aw:
        st.caption(f"No AEGEAN award miles collected yet for {orig} → {dest} in this range (no data, which is not the same as no seats). "
                   "They are collected daily on the owner's computer.")
    else:
        st.caption(f"AEGEAN award miles (AEGEAN-operated flights only, {'nonstop' if nonstop else 'any stops'}): {seen_aw} of {len(days)} days "
                   f"collected for {orig} → {dest}, last on {last_aw[:10]}. An empty cell means that day was checked and has no award seats; "
                   "days not collected are not listed.")
        if date.fromisoformat(last_aw[:10]) < today - timedelta(3):
            st.warning(f"These award miles were last collected on {last_aw[:10]}, so they may be out of date.")
        st.dataframe(am.rename(columns={"economy": "Economy miles", "business": "Business miles"}), width="stretch")
    hist = os.path.join(ROOT, "data", "award_history.csv")
    xl = award_history_xlsx(hist, os.path.getmtime(hist)) if os.path.exists(hist) else None
    if xl:  # every daily snapshot, all four legs, as one sheet
        st.download_button("Download award history (Excel, all routes)", xl[0], "aegean_award_history.xlsx",
                           help=f"{xl[1]} daily snapshots, last {xl[2]}")
    if os.path.isdir(AWARD_PROFILE):  # only where the AEGEAN login lives: your own computer
        award_job()
    st.caption("Miles+Bonus awards: type the miles and taxes you see after logging in to AEGEAN; they feed the 'Best miles value' figure.")
    ed = st.data_editor(days.join(a).reset_index(), hide_index=True, disabled=["day"], width="stretch",
                        column_config={"day": "Date", "miles": st.column_config.NumberColumn("Miles", format="%d"),
                                       "taxes": st.column_config.NumberColumn("Taxes €", format="€%.0f")},
                        key=f"{orig}{dest}{start}{end}")
    save_awards(db, ed[["day", "miles", "taxes"]].itertuples(index=False), orig, dest)
with t3:
    table = out.rename(columns={"economy": "Economy €", "business": "Business €", "miles": "Miles",
                                "taxes": "Taxes €", "value": "€ per 1,000 mi",
                                "econ_miles": "Economy award miles", "biz_miles": "Business award miles"})
    checked = set(zip(seen.day, seen.cabin))
    for cabin in ("economy", "business"):  # say why a price is missing: no flight, or never checked
        table[f"{cabin.capitalize()} status"] = ["priced" if pd.notna(out.loc[d, cabin]) else "no flight" if (d, cabin) in checked
                                                 else "not checked" for d in out.index]
    if nights:
        st.markdown('<p class="card-title">Round trips, cheapest first</p>', unsafe_allow_html=True)
        rt = trips.rename(columns={"cabin": "Cabin", "depart": "Depart", "back": "Return", "nights": "Nights",
                                   "outbound": "Outbound €", "return": "Return €", "total": "Total €"})
        st.dataframe(rt, width="stretch", hide_index=True)
        st.download_button("Download round trips", excel_bytes(rt), f"aegean_{orig}_{dest}_returns.xlsx")
        st.markdown(f'<p class="card-title">{orig} → {dest} by day</p>', unsafe_allow_html=True)
    st.dataframe(table, width="stretch")
    st.download_button("Download Excel", excel_bytes(table), f"aegean_{orig}_{dest}.xlsx")
