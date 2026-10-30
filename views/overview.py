"""Overview, the first page: the best fares on every route the daily run tracks, the cheapest whole Frankfurt trip, a short briefing in
plain sentences, and (when an AI key is set) the expert's take on today's prices with a link to ask it more."""
import html
import importlib
from datetime import date

import pandas as pd
import streamlit as st

import deals
import expert_chat as ex
from db import connect
import overview as ov
from ui import BUSINESS, ECONOMY, kpis, route

if getattr(ex, "VERSION", 0) < 2:  # Streamlit Cloud keeps an older copy of the module across redeploys
    ex = importlib.reload(ex)
if getattr(ov, "VERSION", 0) < 2:
    ov = importlib.reload(ov)
collect, briefing, facts = ov.collect, ov.briefing, ov.facts
db = connect()
today = date.today()
fares, summary, trips, weekday, hotel_night, awards = collect(db, today)
last = db.execute("SELECT MAX(fetched) FROM prices WHERE fetched IS NOT NULL").fetchone()[0]
route("Overview", None, "The best AEGEAN fares on your tracked routes for the next 90 days, from the daily run"
      + (f" of {pd.Timestamp(last):%a %d %b, %H:%M}." if last else "."))
if not summary:
    st.info("No fares are saved yet. The daily run fills this page every morning; or open Flights and press Update prices.")
    st.stop()

try:
    cfg = ex.available(st.secrets)
except Exception:
    cfg = ex.available()


def expert_link(label):
    try:
        st.page_link("views/expert.py", label=label, icon=":material/forum:")
    except Exception:  # the page runs on its own (tests), outside the app's navigation
        st.markdown(f"[{label}](expert)")


st.markdown(ex.CARD, unsafe_allow_html=True)
with st.container(border=True, key="brief"):
    st.markdown('<p class="group">The expert’s take on today’s prices</p>', unsafe_allow_html=True)
    if cfg:
        with st.spinner("Reading today’s prices…"):
            take = ex.briefing(facts(summary, fares, trips, hotel_night, awards, today), cfg, today.isoformat())
        if isinstance(take, tuple):
            st.caption(take[1])
        else:
            st.markdown(take)
        expert_link("Ask the expert a question")
    else:
        st.caption("An AI expert can read these prices every day and answer your questions about them. It needs a free key: see how.")
        expert_link("Set up the expert")

day = lambda d: f"{pd.Timestamp(d):%a %d %b}"
st.markdown('<p class="group">Cheapest one-way fare on each route</p>', unsafe_allow_html=True)
cols = st.columns(2)
for i, ((o, d), s) in enumerate(summary.items()):
    with cols[i % 2]:
        items = []
        for cabin, colour in (("economy", ECONOMY), ("business", BUSINESS)):
            v = s.get(cabin)
            items.append((f"{o} → {d} · {cabin.capitalize()}", f"€{v['min']:,.0f}" if v else "—",
                          f"{day(v['day'])} · typical €{v['typical']:,.0f}" if v else "No fare saved", colour))
        kpis(items)

if trips:
    st.markdown('<p class="group">Cheapest whole trip to Frankfurt, economy return' + (" plus hotel" if hotel_night is not None and len(hotel_night)
                else "") + '</p>', unsafe_allow_html=True)
    kpis([(f"{n} nights", f"€{r['total']:,.0f}", f"{day(r['depart'])} → {day(r['back'])} · €{r['flights']:,.0f} flights"
           + (f" + €{r['hotel']:,.0f} {hotel_night.name}" if r.get("hotel") is not None else ""), ECONOMY)
          for n, r in sorted(trips.items())])
    st.caption("The hotel is the Frankfurt hotel you searched most recently on the Hotels page; search another there to change it."
               if hotel_night is not None and len(hotel_night) else
               "Search a Frankfurt hotel on the Hotels page (1 night) to add it to these trips.")

st.markdown('<p class="group">Cheapest month to fly</p>', unsafe_allow_html=True)
cab = st.pills("Cabin", ["economy", "business"], default="economy", format_func=str.capitalize, required=True, key="ov-month-cabin",
               label_visibility="collapsed")
months = deals.month_table(fares, cab)
if len(months):
    st.dataframe(months.style.format("€{:,.0f}", na_rep="—").apply(deals.month_colours, axis=None), width="stretch")
    st.caption("The cheapest one-way fare of each month. Green: the route's cheapest month; orange: at least 15% dearer than that. "
               "Months with less than a week of fares saved are left out.")

if say := briefing(summary, trips, weekday):
    st.markdown('<p class="group">In short</p>', unsafe_allow_html=True)
    st.markdown("\n".join(f"- {html.escape(s)}" for s in say))
