"""Compare routes: every route the app has data for, side by side. The numbers come from the saved prices and award miles (see route_board)."""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from db import connect
from route_board import STYLE, card_html, load, summarise, winners
from ui import BUSINESS, ECONOMY, MUTED, excel_bytes, kpis, route

db = connect()
today = date.today()
route("Compare routes", None, "Cheapest fares and award miles on every route we track, side by side.")
c = st.columns([3, 1])
days = c[0].pills("Look at the next", [15, 30, 60, 90], default=30, format_func=lambda d: f"{d} days", key="rb-days") or 30
nonstop = c[1].toggle("Nonstop only", value=True, key="rb-nonstop")
prices, miles = load(db, today + timedelta(1), today + timedelta(days), nonstop)
summary = summarise(prices, miles)
if summary.empty:
    st.info("No fares are saved for these days yet. Open Flights and press Update prices, or wait for the daily run.")
    st.stop()
win = winners(summary)
row = lambda col, what: summary[summary.route == win[col]].iloc[0] if col in win else None


def top(label, col, fmt, sub, colour):
    r = row(col, label)
    return (label, "—", "Nothing saved yet", MUTED) if r is None else (label, fmt(r[col]), f"{r['route']} · {sub(r)}", colour)


day = lambda iso: f"{pd.Timestamp(iso):%a %d %b}"
kpis([top("Cheapest economy", "econ_cash", lambda v: f"€{v:.0f}", lambda r: day(r["econ_cash_day"]), ECONOMY),
      top("Cheapest business", "biz_cash", lambda v: f"€{v:.0f}", lambda r: day(r["biz_cash_day"]), BUSINESS),
      top("Lowest economy award", "econ_miles", lambda v: f"{v:,.0f} miles", lambda r: day(r["econ_miles_day"]), ECONOMY),
      top("Lowest business award", "biz_miles", lambda v: f"{v:,.0f} miles", lambda r: day(r["biz_miles_day"]), BUSINESS)])
st.markdown(STYLE, unsafe_allow_html=True)
ordered = summary.sort_values("econ_cash", na_position="last").to_dict("records")
for left, right in zip(ordered[::2], ordered[1::2] + [None]):
    cols = st.columns(2, gap="medium")
    cols[0].markdown(card_html(left, win), unsafe_allow_html=True)
    if right is not None:
        cols[1].markdown(card_html(right, win), unsafe_allow_html=True)
table = summary.drop(columns=["orig", "dest"]).rename(columns={
    "route": "Route", "econ_cash": "Economy €", "econ_cash_day": "Economy day", "econ_typical": "Economy typical €", "biz_cash": "Business €",
    "biz_cash_day": "Business day", "biz_typical": "Business typical €", "econ_miles": "Economy miles", "econ_miles_day": "Economy miles day",
    "econ_seat_days": "Economy seat days", "biz_miles": "Business miles", "biz_miles_day": "Business miles day", "biz_seat_days": "Business seat days",
    "econ_value": "Economy € per 1,000 mi", "econ_value_day": "Economy value day", "biz_value": "Business € per 1,000 mi",
    "biz_value_day": "Business value day"})
st.download_button("Download this comparison (Excel)", excel_bytes(table.set_index("Route")), "aegean_routes.xlsx")
st.caption(f"Cheapest fare and lowest award miles over the next {days} days, {'nonstop AEGEAN flights only' if nonstop else 'including flights with stops'}. "
           "Each direction is its own route: the cheapest day of FRA → ATH is not tied to the cheapest day of ATH → FRA. Routes differ in length, so compare each one with itself over time as well as with the others. The value per 1,000 miles is the cash fare "
           "(minus award taxes when known) divided by the miles, on the day it is highest.")
