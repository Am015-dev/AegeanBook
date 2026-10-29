"""Insights: when to fly and when to book, worked out from the fares we have saved. Every chart has its takeaway as a plain sentence above it."""
import html
import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from db import connect
from insights import (ahead_chart, ahead_stats, ahead_takeaway, cash_by_day, destination_chart, destination_takeaway, destinations, history_chart,
                      history_sentence, moves, routes, weekday_chart, weekday_stats, weekday_takeaway)
from ui import BUSINESS, ECONOMY, route

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = {"displayModeBar": False}
db = connect()
today = date.today()
route("Insights", None, "When to fly and when to book, worked out from the fares we have saved.")
saved = routes(db, True)
if not saved:
    st.info("No fares are saved yet. Open Flights and press Update prices, or wait for the daily run.")
    st.stop()
c = st.columns([2, 1])
orig, dest = c[0].selectbox("Route", saved, format_func=lambda p: f"{p[0]} → {p[1]}", key="ins-route")
cabin = c[1].radio("Cabin", ["economy", "business"], format_func=str.capitalize, horizontal=True, key="ins-cabin")
colour, label = (ECONOMY if cabin == "economy" else BUSINESS), f"{orig} → {dest}"
cash = cash_by_day(db, orig, dest, cabin, True, today + timedelta(1), today + timedelta(90))
if cash.empty:
    st.info(f"No {cabin} fares are saved for {label} yet.")
    st.stop()
heading = lambda text: st.markdown(f'<p class="card-title">{html.escape(text)}</p>', unsafe_allow_html=True)

wd = weekday_stats(cash)
heading(weekday_takeaway(wd, label))
if len(wd) >= 3:
    st.plotly_chart(weekday_chart(wd, colour), width="stretch", config=CONFIG)
st.caption(f"Average {cabin} fare by weekday of departure, over the {len(cash)} days with a fare saved.")

ah = ahead_stats(cash, today)
heading(ahead_takeaway(ah, label))
if len(ah) >= 2:
    st.plotly_chart(ahead_chart(ah, colour), width="stretch", config=CONFIG)
st.caption("Typical (median) fare by how many weeks ahead the flight is, from today's prices for different dates. It shows how fares differ with "
           "distance, not how one fare moves: the next chart does that once fares have been recorded for a few days.")


@st.cache_data(ttl=600, show_spinner=False)
def load_history(path, mtime):
    return pd.read_csv(path) if os.path.exists(path) and os.path.getsize(path) else pd.DataFrame()


hist_path = os.path.join(ROOT, "data", "history_prices.csv")
hist = load_history(hist_path, os.path.getmtime(hist_path) if os.path.exists(hist_path) else 0)
moved = moves(hist, orig, dest, cabin)
heading("Is the fare rising or falling?")
if moved:
    day = st.selectbox("Departure day", sorted(moved), key="ins-day", format_func=lambda d: f"{pd.Timestamp(d):%a %d %b}",
                       help="Only days whose fare changed since we started recording are listed.")
    st.markdown(f"**{history_sentence(moved[day], f'{pd.Timestamp(day):%a %d %b}')}**")
    st.plotly_chart(history_chart(moved[day], colour), width="stretch", config=CONFIG)
    st.caption(f"{len(moved)} of {len(cash)} days on this route changed price since recording started.")
else:
    recorded = sorted(hist.fetched.str[:10].unique()) if not hist.empty else []
    if len(recorded) <= 1:
        st.info(f"We started recording on {recorded[0] if recorded else 'the first daily run'}. Fares are saved once a day, so after the next daily run "
                "this will show whether a fare is going up or down, and whether it is worth waiting.")
    else:
        st.info(f"No fare on this route has changed since {recorded[0]}: prices have been steady for {len(recorded)} recordings.")

dests = destinations(db, orig, cabin, True, today + timedelta(1), today + timedelta(90))
if len(dests) >= 2:
    heading(destination_takeaway(dests, orig))
    st.plotly_chart(destination_chart(dests), width="stretch", config=CONFIG)
    st.caption(f"Cheapest and typical (median) {cabin} fare from {orig} to each destination, over the next 90 days.")
