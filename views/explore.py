"""Explore: where can you fly cheaply from here, and when? (Google Flights' Explore, for AEGEAN.) Every destination with fares saved from the
chosen airport, cheapest first, for the next 30 days or a month, one way or a 2–7 night return, within a budget. The maths is in explore.py."""
import html
import importlib
from datetime import date

import streamlit as st

import explore as xp
import fetch_ahead as fa
import overview as ov
import trip_plan as tp
from db import connect
from ui import BUSINESS, ECONOMY, FONT, INK, MUTED, SHADOW, SURFACE, route

if getattr(xp, "VERSION", 0) < 1:  # Streamlit Cloud keeps an older copy of a module across redeploys
    xp = importlib.reload(xp)
if getattr(ov, "VERSION", 0) < 4:
    ov = importlib.reload(ov)
if getattr(tp, "VERSION", 0) < 4:
    tp = importlib.reload(tp)
if getattr(fa, "VERSION", 0) < 1:
    fa = importlib.reload(fa)
db = connect()
today = date.today()
legs = ov.legs(db, today, tp.HORIZON)  # as far ahead as a month can be fetched, so a fetched summer month lists its destinations
route("Explore", None, "Where can you fly cheaply, and when? Every destination with fares saved, cheapest first.")
if not legs:
    st.info("No fares are saved yet. The daily check fills this page every morning; or open Flights and press Update prices.")
    st.stop()

e = html.escape
st.markdown(f"""<style>
[class*="st-key-xp-card-"], [class*="st-key-xp-over-"] {{ background:{SURFACE}; border-radius:14px; box-shadow:{SHADOW}; padding:14px 18px 6px;
  margin:0 0 10px; }}
[class*="st-key-xp-over-"] {{ opacity:.55; }}
[class*="st-key-xp-card-"] [data-testid="stPageLink"] a, [class*="st-key-xp-over-"] [data-testid="stPageLink"] a {{ padding-left:0; }}
.xp {{ display:flex; justify-content:space-between; align-items:center; gap:16px; }}
.xp .city {{ font-weight:600; font-size:1.05rem; color:{INK}; }}
.xp .code {{ color:{MUTED}; font-weight:500; font-size:.9rem; margin-left:6px; }}
.xp .when {{ color:{MUTED}; font-size:.85rem; margin-top:2px; text-wrap:pretty; }}
.xp .price {{ font:650 1.7rem/1 {FONT}; letter-spacing:-.02em; white-space:nowrap; text-align:right; }}
.xp .price small {{ display:block; font-size:.72rem; font-weight:500; color:{MUTED}; letter-spacing:0; margin-top:5px; }}
@media (max-width: 640px) {{ [class*="st-key-xp-card-"], [class*="st-key-xp-over-"] {{ padding:12px 14px 4px; }} .xp .price {{ font-size:1.45rem; }} }}
</style>""", unsafe_allow_html=True)

# --- the controls ---------------------------------------------------------------------------------------------------------------------
place = lambda a: f"{tp.city(a)} ({a})"
froms = xp.origins(legs)
lastday = max(date.fromisoformat(s.index.max()) for f in legs.values() for s in f.values() if len(s))
periods = tp.months(today, lastday)  # every month up to the horizon: a month with no fares yet can be fetched below
c = st.columns([1.3, 1.2, 1.3, 1])
orig = c[0].selectbox("From", froms, format_func=place, key="xp-orig", help="Every airport with fares saved from it.")
when = c[1].selectbox("When", range(len(periods)), format_func=lambda i: periods[i].title, key="xp-when")
trip = c[2].pills("Trip", ["one way", "return"], default="one way", format_func={"one way": "One way", "return": "Return, 2–7 nights"}.get,
                  required=True, key="xp-trip")
cabin = c[3].pills("Cabin", ["economy", "business"], default="economy", format_func=str.capitalize, required=True, key="xp-cabin")
p = periods[when]
rows = xp.destinations(legs, orig, p.start, p.end, cabin, xp.NIGHTS if trip == "return" else None)
if rows.empty:
    st.info(f"No {cabin} {'return trips' if trip == 'return' else 'fares'} saved from {tp.city(orig)} for {p.name}. "
            "Try the other cabin or another month, or search a route on the Flights page.")
    # nothing saved for the period: one request fetches every route from here, both ways where the way back is known
    dests = [d for o, d in legs if o == orig]
    todo, blank = fa.missing(db, [(orig, d, p.start, p.end) for d in dests] + [(d, orig, p.start, p.end) for d in dests if (d, orig) in legs], today)
    if todo:
        fa.button(f"ahead|{orig}|all|{p.start}", f"Fetch {p.name} for all routes from {tp.city(orig)}", todo, f"{p.name.capitalize()} from {tp.city(orig)}")
    st.stop()
top = int(-(-rows.price.max() // 10) * 10)  # the dearest destination, rounded up to €10, so the default budget fits every one
budget = st.slider("Budget (€, one adult)", 0, top, top, 10, format="€%d", key=f"xp-budget-{orig}-{when}-{trip}-{cabin}")
within, over = xp.fit(rows, budget)

# --- one card per destination, over budget greyed at the bottom ----------------------------------------------------------------------
colour = ECONOMY if cabin == "economy" else BUSINESS
st.markdown(f'<p class="group">From {e(tp.city(orig))}, {e(p.name)}, {e(cabin)}, '
            f'{"return" if trip == "return" else "one way"}</p>', unsafe_allow_html=True)
if within.empty:
    st.caption(f"Nothing from {tp.city(orig)} fits €{budget:,.0f}: the cheapest is {tp.city(rows.dest[0])} at €{rows.price[0]:,.0f}.")
for r in list(within.itertuples()) + list(over.itertuples()):
    dates = f"{r.depart:%a %d %b} → {r.back:%a %d %b} · {r.nights} nights" if r.back else f"{r.depart:%a %d %b}"
    with st.container(key=f"xp-{'over' if r.price > budget else 'card'}-{r.dest}"):  # the key names the class the CSS greys
        st.markdown(f'<div class="xp"><div><div class="city">{e(tp.city(r.dest))}<span class="code">{e(r.dest)}</span></div>'
                    f'<div class="when">{e(dates)} · {r.days} flight days</div></div>'
                    f'<div class="price" style="color:{colour}">€{r.price:,.0f}<small>typical €{r.typical:,.0f}</small></div></div>',
                    unsafe_allow_html=True)
        try:
            st.page_link("views/overview.py", label="See dates", icon=":material/calendar_month:", query_params={"from": orig, "to": r.dest})
        except Exception:  # the page runs on its own (tests), outside the app's navigation
            pass
st.caption(f"Cheapest {'return of 2 to 7 nights, both one-way fares added up as AEGEAN prices them' if trip == 'return' else 'one-way fare'} "
           f"on the days saved; typical is the median. Prices are per adult, from Google Flights, AEGEAN nonstop.")
