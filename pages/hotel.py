import sqlite3
from datetime import date, datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from hotels import fetch_range

db = sqlite3.connect("tracker.db", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS hotel(day TEXT, query TEXT, filt TEXT, nights INT, hotel TEXT, price REAL, fetched TEXT)")

st.title("Cheapest day to book a hotel")
query = st.text_input("Google Hotels search", "Steigenberger Frankfurt")
filt = st.text_input("Hotel name must contain", "Frankfurter Hof")
c1, c2, c3 = st.columns(3)
start, end = c1.date_input("Check-in from", date(2026, 11, 1)), c2.date_input("Check-in to", date(2026, 11, 30))
nights = c3.number_input("Nights", 1, 14, 1)
adults = st.number_input("Adults", 1, 6, 2)

if st.button("Fetch prices (≈2s/day)"):
    bar, n = st.progress(0.0), (end - start).days + 1
    for i, (d, name, price) in enumerate(fetch_range(start, end, query, filt, nights, adults)):
        if price is not None:
            db.execute("INSERT INTO hotel VALUES(?,?,?,?,?,?,?)",
                       (d.isoformat(), query, filt, nights, name, price, datetime.now().isoformat()))
            db.commit()
        bar.progress((i + 1) / n)

p = pd.read_sql("SELECT * FROM hotel WHERE query=? AND filt=? AND nights=? AND day BETWEEN ? AND ? ORDER BY fetched",
                db, params=(query, filt, int(nights), start.isoformat(), end.isoformat()))
if p.empty:
    st.info("No data yet — press Fetch.")
else:
    s = p.groupby("day").agg(hotel=("hotel", "last"), price=("price", "last"))
    s["price per night €"] = s.price  # Google reports the nightly rate
    best = s.price.idxmin()
    st.success(f"Cheapest check-in: {best} — €{s.price.min():.0f}/night ({s.hotel[best]}); "
               f"average €{s.price.mean():.0f}, most expensive €{s.price.max():.0f}")
    st.plotly_chart(px.line(s.reset_index(), x="day", y="price", markers=True, labels={"price": "€ per night"}))
    st.dataframe(s[["hotel", "price per night €"]])
    s.to_excel("hotel_export.xlsx")
    st.download_button("Download Excel", open("hotel_export.xlsx", "rb"), "hotel_prices.xlsx")
