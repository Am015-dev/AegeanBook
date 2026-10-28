from datetime import date, datetime

import pandas as pd
import streamlit as st

from collector import fetch_range
from db import RETENTION_DAYS, connect, cutoff
from ui import BUSINESS, ECONOMY, MUTED, kpis, price_chart, route

db = connect()

with st.expander("Search settings", expanded=False):
    c = st.columns([1, 1, 1.4, 1.4])
    orig, dest = c[0].text_input("From (IATA)", "ATH").upper(), c[1].text_input("To (IATA)", "FRA").upper()
    start, end = c[2].date_input("First departure", date(2026, 11, 1)), c[3].date_input("Last departure", date(2026, 11, 30))
    fetch = st.button("Update prices", type="primary")
    recheck = st.checkbox("Re-check days already saved")
    st.caption(f"One-way, 1 passenger, AEGEAN flights only. About 4 seconds per new day. "
               f"Results are saved for {RETENTION_DAYS} days and reused.")

MAX_DAYS = 90
if end < start:
    st.error("The last departure is before the first departure. Swap the two dates.")
    st.stop()
if (end - start).days >= MAX_DAYS:
    st.error(f"Choose a range of at most {MAX_DAYS} days.")
    st.stop()

route(orig, dest, f"{start:%d %b} – {end:%d %b %Y} · one-way · AEGEAN")

if fetch:
    skip = set() if recheck else set(db.execute(
        "SELECT day, cabin FROM prices WHERE orig=? AND dest=? AND fetched>=?", (orig, dest, cutoff())))
    total = max(1, 2 * ((end - start).days + 1) - len(skip))
    bar, failed, empty = st.progress(0.0, text="Checking fares…"), 0, 0
    for i, (d, cabin, price, air, status) in enumerate(fetch_range(start, end, orig, dest, skip=skip)):
        if status != "error":  # failed lookups are not saved, so the next run retries them
            db.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?)",
                       (d.isoformat(), orig, dest, cabin, price, air, datetime.now().isoformat()))
            db.commit()
        failed, empty = failed + (status == "error"), empty + (status == "none")
        bar.progress(min(1.0, (i + 1) / total), text=f"Checked {i + 1} of {total}")
    bar.empty()
    if failed:
        st.warning(f"{failed} lookups failed because Google did not answer. Press Update prices again to retry just those.")
    if empty:
        st.caption(f"{empty} day and cabin combinations have no AEGEAN flight on this route.")

q, args = "WHERE orig=? AND dest=? AND day BETWEEN ? AND ?", (orig, dest, start.isoformat(), end.isoformat())
p = pd.read_sql(f"SELECT * FROM prices {q} ORDER BY fetched", db, params=args)
latest = p.drop_duplicates(["day", "cabin"], keep="last").pivot(index="day", columns="cabin", values="price") if len(p) else pd.DataFrame()
days = pd.DataFrame(index=pd.date_range(start, end).strftime("%Y-%m-%d").rename("day"))
for k in ("economy", "business"):
    if k not in latest:
        latest[k] = float("nan")
a = pd.read_sql(f"SELECT day, miles, taxes FROM awards {q}", db, params=args).set_index("day")
out = days.join(latest).join(a)
out["value"] = ((out.business - out.taxes.fillna(0)) / out.miles * 1000).round(1)

def best(col):
    return (f"€{out[col].min():.0f}", f"{pd.Timestamp(out[col].idxmin()):%a %d %b}") if out[col].notna().any() else ("—", "No data yet")

(ev, es), (bv, bs) = best("economy"), best("business")
vv, vs = (f"€{out.value.max():.0f} / 1,000 mi", f"{pd.Timestamp(out.value.idxmax()):%a %d %b} · cash minus taxes per 1,000 miles") \
    if out.value.notna().any() else ("—", "Add award prices in the Awards tab")
kpis([("Cheapest economy", ev, es, ECONOMY), ("Cheapest business", bv, bs, BUSINESS), ("Best miles value", vv, vs, MUTED)])

if len(p):
    st.caption(f"Last updated {p.fetched.max()[:16].replace('T', ' ')} · saved results are kept for {RETENTION_DAYS} days")
t1, t2, t3 = st.tabs(["Prices", "Awards", "Table"])
with t1:
    if out[["economy", "business"]].notna().any().any():
        st.markdown('<p class="card-title">Daily AEGEAN fare, cheapest day ringed</p>', unsafe_allow_html=True)
        st.plotly_chart(price_chart(out, {"economy": ("Economy", ECONOMY), "business": ("Business", BUSINESS)}),
                        width="stretch", config={"displayModeBar": False})
    else:
        st.info("No AEGEAN fares found for this route and these dates. Check the airport codes, or widen the dates." if len(p)
                else "No fares yet. Open **Search settings** above, check the route and dates, then press **Update prices**.")
with t2:
    st.caption("Business Miles+Bonus awards: type the miles and taxes you see after logging in to AEGEAN. AEGEAN offers no public award data.")
    ed = st.data_editor(days.join(a).reset_index(), hide_index=True, disabled=["day"], width="stretch",
                        column_config={"day": "Date", "miles": st.column_config.NumberColumn("Miles", format="%d"),
                                       "taxes": st.column_config.NumberColumn("Taxes €", format="€%.0f")},
                        key=f"{orig}{dest}{start}{end}")
    for r in ed.dropna(subset=["miles"]).itertuples():
        db.execute("INSERT OR REPLACE INTO awards VALUES(?,?,?,?,?)", (r.day, orig, dest, int(r.miles), r.taxes or 0))
    db.commit()
with t3:
    st.dataframe(out.rename(columns={"economy": "Economy €", "business": "Business €", "miles": "Miles",
                                     "taxes": "Taxes €", "value": "€ per 1,000 mi"}), width="stretch")
    out.to_excel("export.xlsx")
    st.download_button("Download Excel", open("export.xlsx", "rb"), f"aegean_{orig}_{dest}.xlsx")
