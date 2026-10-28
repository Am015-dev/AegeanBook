from datetime import date, datetime

import pandas as pd
import streamlit as st

from db import RETENTION_DAYS, connect, cutoff
from hotels import fetch_range
from ui import ECONOMY, kpis, price_chart, route

db = connect()

with st.expander("Search settings", expanded=False):
    c = st.columns(2)
    query, filt = c[0].text_input("Search", "Steigenberger Frankfurt"), c[1].text_input("Name must contain", "Frankfurter Hof")
    c = st.columns(4)
    start, end = c[0].date_input("Check-in from", date(2026, 11, 1)), c[1].date_input("Check-in to", date(2026, 11, 30))
    nights, adults = c[2].number_input("Nights", 1, 14, 1), c[3].number_input("Adults", 1, 6, 2)
    fetch = st.button("Update prices", type="primary")
    recheck = st.checkbox("Re-check days already saved")
    st.caption(f"Google Hotels' lowest rate across booking sites. About 2 seconds per new day. "
               f"Results are saved for {RETENTION_DAYS} days and reused.")

route(filt.title(), None, f"{query} · {start:%d %b} – {end:%d %b %Y} · {nights} night{'s' * (nights > 1)}, {adults} adults")

if fetch:
    key = (query, filt, int(nights), int(adults), cutoff())
    skip = set() if recheck else {r[0] for r in db.execute(
        "SELECT day FROM hotel WHERE query=? AND filt=? AND nights=? AND adults=? AND fetched>=?", key)}
    bar, n = st.progress(0.0, text="Checking rates…"), max(1, (end - start).days + 1 - len(skip))
    for i, (d, name, price) in enumerate(fetch_range(start, end, query, filt, nights, adults, skip=skip)):
        if price is not None:
            db.execute("INSERT INTO hotel VALUES(?,?,?,?,?,?,?,?)",
                       (d.isoformat(), query, filt, nights, name, price, datetime.now().isoformat(), int(adults)))
            db.commit()
        bar.progress(min(1.0, (i + 1) / n), text=f"Checking {d:%d %b}…")
    bar.empty()

p = pd.read_sql("SELECT * FROM hotel WHERE query=? AND filt=? AND nights=? AND adults=? AND day BETWEEN ? AND ? ORDER BY fetched",
                db, params=(query, filt, int(nights), int(adults), start.isoformat(), end.isoformat()))
if p.empty:
    kpis([("Cheapest check-in", "—", "No data yet", ECONOMY)])
    st.info("No rates yet. Set your dates, then open **Search settings** above and press **Update prices**.")
else:
    s = p.groupby("day").agg(hotel=("hotel", "last"), price=("price", "last"))
    low, high = s.price.idxmin(), s.price.idxmax()
    ts = lambda i: f"{pd.Timestamp(i):%a %d %b}"
    kpis([("Cheapest check-in", f"€{s.price.min():.0f}", f"{ts(low)} · {s.hotel[low]}", ECONOMY),
          ("Average", f"€{s.price.mean():.0f}", f"{len(s)} dates checked", "#5B6B82"),
          ("Most expensive", f"€{s.price.max():.0f}", ts(high), "#C2410C")])
    st.markdown('<p class="card-title">Price per night by check-in date, cheapest ringed</p>', unsafe_allow_html=True)
    st.plotly_chart(price_chart(s, {"price": ("Per night", ECONOMY)}), width="stretch", config={"displayModeBar": False})
    with st.expander("Table and export"):
        st.dataframe(s.rename(columns={"hotel": "Hotel", "price": "€ per night"}), width="stretch")
        s.to_excel("hotel_export.xlsx")
        st.download_button("Download Excel", open("hotel_export.xlsx", "rb"), "hotel_prices.xlsx")
