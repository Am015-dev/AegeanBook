from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from collector import fetch_range
from db import RETENTION_DAYS, connect, save_awards, saved_flight_days
from jobs import record, start_job, track
from ui import (BUSINESS, ECONOMY, MUTED, airport_picker, best_days, excel_bytes, kpis, near_cheapest, on_weekdays,
                price_chart, route, smart_filters, window_picker)

db = connect()
today = date.today()
HORIZON = 330  # Google's calendar reaches about 11 months ahead; later days would look like "no flight"

with st.container(border=True):
    c = st.columns(2)
    orig = airport_picker(c[0], "From", "ATH", "orig")
    dest = airport_picker(c[1], "To", "FRA", "dest")
    start, end = window_picker(st, "fl", today, HORIZON)
    c = st.columns(2)
    fetch = c[0].button("Update prices", type="primary", width="stretch")
    with c[1].popover("Options", width="stretch"):
        nonstop = st.checkbox("Nonstop flights only", value=True)
        recheck = st.checkbox("Re-check days already saved")
        st.caption(f"One-way, 1 passenger, AEGEAN flights only, from Google's price calendar. Busy routes take seconds, quiet "
                   f"routes a few minutes. Results are kept {RETENTION_DAYS} days and reused.")

if orig == dest:
    st.error("From and To are the same airport.")
    st.stop()

route(orig, dest, f"{start:%a %d %b} – {end:%a %d %b %Y} · {(end - start).days + 1} days · one-way · AEGEAN")

key = f"flights|{orig}|{dest}|{start}|{end}|{nonstop}"
if fetch:
    skip = set() if recheck else saved_flight_days(db, orig, dest, nonstop, start, end)
    total = 2 * ((end - start).days + 1) - len(skip)

    def work(j):
        tdb = connect()
        for d, cabin, price, source, status, err in fetch_range(start, end, orig, dest, skip=skip, nonstop=nonstop):
            if j["cancel"]:
                break
            if status != "error":  # failed lookups are not saved, so the next run retries them
                tdb.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)",  # `airlines` column holds the source
                            (d.isoformat(), orig, dest, cabin, price, source, datetime.now().isoformat(), int(nonstop)))
                tdb.commit()
            record(j, status, err)

    if total > 0:
        start_job(key, total, work, "{n} day and cabin combinations have no AEGEAN flight. AEGEAN may not fly this route on those "
                  "days, or only in some seasons. Google occasionally misses a day: tick Re-check days already saved (Options) if you "
                  "expect more.", "with no flight")
    else:
        st.info("Every day in this range is already saved. Tick Re-check days already saved under Options to fetch again.")
track(key)

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
out["value"] = ((out.business - out.taxes.fillna(0)) / out.miles.where(out.miles > 0) * 1000).round(1)


tol, weekdays = smart_filters(st, "fl")
view = on_weekdays(out, weekdays)  # the cards, chips and chart only look at the chosen weekdays
near = {c: near_cheapest(view[c], tol) for c in ("economy", "business")}


def best(col):
    if near[col].size:
        n = len(near[col])
        return f"€{near[col].iloc[0]:.0f}", (f"{pd.Timestamp(near[col].index[0]):%a %d %b}" +
                                             (f" · {n} days within {tol:.0%}" if tol and n > 1 else f" · {n} days at this price" if n > 1 else ""))
    return "—", "No AEGEAN flight found" if (seen.cabin == col).any() else "No data yet"


(ev, es), (bv, bs) = best("economy"), best("business")
vv, vs = (f"€{view.value.max():.0f} / 1,000 mi", f"{pd.Timestamp(view.value.idxmax()):%a %d %b} · cash minus taxes per 1,000 miles") \
    if view.value.notna().any() else ("—", "Add award prices in the Awards tab")
kpis([("Cheapest economy", ev, es, ECONOMY), ("Cheapest business", bv, bs, BUSINESS), ("Best miles value", vv, vs, MUTED)])
best_days(near["economy"], ECONOMY, "Cheap economy days")
best_days(near["business"], BUSINESS, "Cheap business days")

if len(seen) < 2 * len(days):
    st.caption(f"Checked {len(seen)} of {2 * len(days)} day and cabin combinations: {int(seen.price.notna().sum())} priced, "
               f"{int(seen.price.isna().sum())} with no flight, {2 * len(days) - len(seen)} not checked or failed. "
               "Press Update prices to check the rest.")
if len(p):
    st.caption(f"Last updated {p.fetched.max()[:16].replace('T', ' ')} · saved results are kept for {RETENTION_DAYS} days")
t1, t2, t3 = st.tabs(["Prices", "Awards", "Table"])
with t1:
    if view[["economy", "business"]].notna().any().any():
        st.markdown('<p class="card-title">Daily AEGEAN fare, cheap days ringed</p>', unsafe_allow_html=True)
        st.plotly_chart(price_chart(view, {"economy": ("Economy", ECONOMY), "business": ("Business", BUSINESS)},
                                    near={c: list(near[c].index) for c in near}),
                        width="stretch", config={"displayModeBar": False})
    else:
        st.info("No AEGEAN fares on these days. Try other weekdays, a longer window, or turn off Nonstop flights only in Options."
                if len(p) else "No fares yet. Pick the route and dates above, then press **Update prices**.")
with t2:
    st.caption("Business Miles+Bonus awards: type the miles and taxes you see after logging in to AEGEAN. AEGEAN offers no public award data.")
    ed = st.data_editor(days.join(a).reset_index(), hide_index=True, disabled=["day"], width="stretch",
                        column_config={"day": "Date", "miles": st.column_config.NumberColumn("Miles", format="%d"),
                                       "taxes": st.column_config.NumberColumn("Taxes €", format="€%.0f")},
                        key=f"{orig}{dest}{start}{end}")
    save_awards(db, ed[["day", "miles", "taxes"]].itertuples(index=False), orig, dest)
with t3:
    table = out.rename(columns={"economy": "Economy €", "business": "Business €", "miles": "Miles",
                                "taxes": "Taxes €", "value": "€ per 1,000 mi"})
    checked = set(zip(seen.day, seen.cabin))
    for cabin in ("economy", "business"):  # say why a price is missing: no flight, or never checked
        table[f"{cabin.capitalize()} status"] = ["priced" if pd.notna(out.loc[d, cabin]) else "no flight" if (d, cabin) in checked
                                                 else "not checked" for d in out.index]
    st.dataframe(table, width="stretch")
    st.download_button("Download Excel", excel_bytes(table), f"aegean_{orig}_{dest}.xlsx")
