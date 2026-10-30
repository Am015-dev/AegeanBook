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
import trip_plan as tp
from ui import BUSINESS, ECONOMY, kpis, route

if getattr(ex, "VERSION", 0) < 2:  # Streamlit Cloud keeps an older copy of the module across redeploys
    ex = importlib.reload(ex)
if getattr(ov, "VERSION", 0) < 3:
    ov = importlib.reload(ov)
if getattr(tp, "VERSION", 0) < 3:
    tp = importlib.reload(tp)
if getattr(deals, "VERSION", 0) < 3:
    deals = importlib.reload(deals)
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
    st.dataframe(deals.styled(months, "€{:,.0f}".format, deals.month_colours), width="stretch")
    st.caption("The cheapest one-way fare of each month. Green: the route's cheapest month; orange: at least 15% dearer than that. "
               "Months with less than a week of fares saved are left out.")

st.markdown('<p class="group">Return trip by date</p>', unsafe_allow_html=True)
pairs = [(o, d) for o, d in fares if (d, o) in fares]  # routes with fares saved both ways
froms = list(dict.fromkeys(o for o, _ in pairs))
c = st.columns([1, 2]) + st.columns([1.3, 1.1, 1.6])  # From and To, then the cabin, the payment and the dates
orig = c[0].selectbox("From", froms, format_func=tp.city, key="ov-grid-orig")
dests = [d for o, d in pairs if o == orig]
dest = c[1].pills("To", dests, default=dests[0] if dests else None, format_func=tp.city, required=True, key=f"ov-grid-dest-{orig}")
gcab = c[2].pills("Cabin", ["economy", "business"], default="economy", format_func=str.capitalize, required=True, key="ov-grid-cabin")
pay = c[3].pills("Pay with", ["cash", "miles"], default="cash", format_func={"cash": "Cash €", "miles": "Miles"}.get, required=True,
                 key="ov-grid-pay")
lastday = max(date.fromisoformat(s.index.max()) for f in fares.values() for s in f.values() if len(s))
periods = tp.months(today, lastday)
when = c[4].selectbox("Departing", range(len(periods)), format_func=lambda i: periods[i][0], key="ov-grid-when")
if dest:
    _, a, b = periods[when]
    src = fares if pay == "cash" else awards
    out_, back_ = src.get((orig, dest)), src.get((dest, orig))
    grid = deals.trip_grid(out_[gcab], back_[gcab], a, b) if out_ is not None and back_ is not None else pd.DataFrame()
    money = (lambda v: f"€{v:,.0f}") if pay == "cash" else (lambda v: f"{v:,.0f} miles")
    if len(grid):
        st.dataframe(deals.styled(grid, ("€{:,.0f}" if pay == "cash" else "{:,.0f}").format, deals.grid_colours),
                     width="stretch", height=min(36 * len(grid) + 38, 560))
        leave, stay = grid.stack().idxmin()
        same = grid.min().min() == grid.max().max()
        st.caption(f"Return {'fare' if pay == 'cash' else 'award'} {tp.city(orig)} → {tp.city(dest)} → {tp.city(orig)}, {gcab}: the day you leave down the "
                   "side, the nights you stay across. "
                   + (f"Every trip shown costs **{money(grid.min().min())}**; green marks the dates you can go." if same else
                      f"Cheapest: **{money(grid.min().min())}**, leaving {leave} for {stay} (dark green); light green: within 10% of it.") + (" Miles only: award taxes and fees come on top. — means no award seat that "
                                                       "way on one of the two days." if pay == "miles" else ""))
        try:
            st.page_link("views/alerts.py", label=f"Alert me when {orig} → {dest} {gcab} gets cheaper", icon=":material/notifications:",
                         query_params={"route": f"{orig}-{dest}", "cabin": gcab, "unit": "eur" if pay == "cash" else "miles"})
        except Exception:  # the page runs on its own (tests), outside the app's navigation
            pass
    else:
        st.caption("No return " + ("fares" if pay == "cash" else "award seats both ways") + " saved for those days.")

if say := briefing(summary, trips, weekday):
    st.markdown('<p class="group">In short</p>', unsafe_allow_html=True)
    st.markdown("\n".join(f"- {html.escape(s)}" for s in say))

st.markdown('<p class="group">Price alerts</p>', unsafe_allow_html=True)
try:
    st.page_link("views/alerts.py", label="Get an email or phone notification when a fare drops below your limit", icon=":material/notifications:")
except Exception:  # the page runs on its own (tests), outside the app's navigation
    st.markdown("[Get an email or phone notification when a fare drops below your limit](alerts)")
