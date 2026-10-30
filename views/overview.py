"""Overview, the first page: the best fares on every route the daily run tracks, the cheapest whole Frankfurt trip, a short briefing in
plain sentences, and (when an API key is set) a virtual expert to ask about the data."""
import html
from datetime import date

import pandas as pd
import streamlit as st

import expert
from db import connect
from overview import collect, briefing, facts
from ui import BUSINESS, ECONOMY, kpis, route

db = connect()
today = date.today()
fares, summary, trips, weekday, hotel_night, awards = collect(db, today)
last = db.execute("SELECT MAX(fetched) FROM prices WHERE fetched IS NOT NULL").fetchone()[0]
route("Overview", None, "The best AEGEAN fares on your tracked routes for the next 90 days, from the daily run"
      + (f" of {pd.Timestamp(last):%a %d %b, %H:%M}." if last else "."))
if not summary:
    st.info("No fares are saved yet. The daily run fills this page every morning; or open Flights and press Update prices.")
    st.stop()

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
           + (f" + €{r['hotel']:,.0f} Steigenberger Frankfurter Hof" if r.get("hotel") is not None else ""), ECONOMY)
          for n, r in sorted(trips.items())])

if say := briefing(summary, trips, weekday):
    st.markdown('<p class="group">In short</p>', unsafe_allow_html=True)
    st.markdown("\n".join(f"- {html.escape(s)}" for s in say))

st.markdown('<p class="group">Ask the expert</p>', unsafe_allow_html=True)
try:
    key = expert.available(st.secrets)
except Exception:
    key = expert.available()
if not key:
    with st.expander("A virtual expert can answer questions about these prices"):
        st.markdown("Claude Haiku reads every saved fare, award and hotel price and answers questions such as *Which week is cheapest for a "
                    "4-night Frankfurt trip?* or *Is it worth using miles for business to Zurich?*\n\n"
                    "It is the only paid part of the app: about €0.001 per question, paid from an Anthropic API key. To turn it on, add "
                    "`ANTHROPIC_API_KEY = \"...\"` under **Settings → Secrets** of this app on Streamlit Cloud. Set a monthly spend limit in "
                    "the Anthropic Console as well; the app also stops after "
                    f"{expert.MAX_PER_DAY} questions a day.")
else:
    examples = ["Which dates give the cheapest 3-night Frankfurt trip, flights and hotel?",
                "Is business to Zurich worth it this month compared with economy?",
                "Where do my miles save the most cash?"]
    pick = st.pills("Try", examples, key="ex-pick")
    with st.form("ask", border=False):
        q = st.text_area("Your question", value=pick or "", placeholder="For example: when should I fly to Frankfurt for a long weekend?",
                         max_chars=500, height=80)
        go = st.form_submit_button("Ask", type="primary")
    if go and q.strip():
        try:
            st.write_stream(expert.ask(q.strip(), facts(summary, fares, trips, hotel_night, awards, today), key))
        except Exception as err:  # never crash the page for an API problem; say what happened
            st.error(str(err) if isinstance(err, RuntimeError) else f"The expert is unavailable right now ({type(err).__name__}).")
        st.caption(f"Answers come from the saved prices only and can be wrong: check the fare on aegeanair.com before booking. "
                   f"{expert.left_today()} questions left today.")
