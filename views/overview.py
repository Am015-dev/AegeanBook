"""Overview, the first page and the one place to find a trip: one search bar (From, To or Anywhere, When, Nights, Cabin, Pay with,
Passengers) and one answer under it. A route gives a boarding pass with the cheapest trip, the dates behind it and, in expanders, what the
saved fares say about weekdays and booking ahead; Anywhere lists every destination with fares saved, cheapest first; a route with nothing
saved gets one button that fetches it on GitHub. The AI expert lives on its own page; this one shows only numbers the data proves."""
import html
import os
import math
import importlib
import time
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import booking
import deals
import explore as xp
import fetch_ahead as fa
import flight_times
import gh
import miles_value
import mix
import nearby
import network
import overview as ov
import price_insights as pi
import skyexpress as sx
import trip_plan as tp
from db import connect
from award_view import load_award
from insights import DAYS, cash_by_day, ahead_chart, ahead_stats, ahead_takeaway, weekday_chart, weekday_stats, weekday_takeaway
from trip_cost import night_prices
from layout import page_head
from ui import (BUSINESS, ECONOMY, FONT, GRID, INK, MUTED, SHADOW, SURFACE, aegean_rows, github_note, github_progress, grid_frame, html_table,
                kpis, no_service_note, price_chart, route)

if getattr(ov, "VERSION", 0) < 4:  # Streamlit Cloud keeps an older copy of a module across redeploys
    ov = importlib.reload(ov)
if getattr(tp, "VERSION", 0) < 6:
    tp = importlib.reload(tp)
if getattr(deals, "VERSION", 0) < 7:
    deals = importlib.reload(deals)
if getattr(pi, "VERSION", 0) < 2:
    pi = importlib.reload(pi)
if getattr(sx, "VERSION", 0) < 3:
    sx = importlib.reload(sx)
if getattr(fa, "VERSION", 0) < 3:
    fa = importlib.reload(fa)
if getattr(xp, "VERSION", 0) < 3:
    xp = importlib.reload(xp)
db = connect()
today = date.today()
fares, summary, trips, weekday, hotel_night, awards = ov.collect(db, today)
last = db.execute("SELECT MAX(fetched) FROM prices WHERE fetched IS NOT NULL").fetchone()[0]
# no page title: the search bar is the page, as on Google Flights; when the fares were last checked is said under the bar
if not summary:
    st.info("No fares are saved yet. The daily check fills this page every morning; or open Flights and press Update prices.")
    st.stop()

e = html.escape
st.markdown(f"""<style>
.pass {{ display:flex; background:{SURFACE}; border-radius:18px; box-shadow:{SHADOW}; margin:6px 0 10px; overflow:hidden; }}
.pass-main {{ flex:1; padding:22px 26px; min-width:0; }}
.pass-codes {{ display:flex; align-items:center; gap:14px; font:650 clamp(2rem,6vw,2.8rem)/1 {FONT}; letter-spacing:-.035em; color:{INK}; }}
.pass-codes .line {{ flex:1; height:2px; background:{GRID}; min-width:30px; }}
.pass-cities {{ display:flex; justify-content:space-between; color:{MUTED}; font-size:.85rem; margin-top:6px; }}
.pass-fields {{ display:flex; gap:28px; flex-wrap:wrap; margin:18px 0 0; }}
.pass-fields dt {{ font-size:.85rem; color:{MUTED}; margin:0; }}
.pass-fields dd {{ font-size:1.02rem; font-weight:600; color:{INK}; margin:2px 0 0; }}
.pass-stub {{ position:relative; width:240px; padding:22px 24px; border-left:2px dashed {GRID}; display:flex; flex-direction:column;
  justify-content:center; }}
.pass-stub::before, .pass-stub::after {{ content:''; position:absolute; left:-11px; width:20px; height:20px; border-radius:50%;
  background:var(--background-color, #F5F5F7); }}
.pass-stub::before {{ top:-10px; }} .pass-stub::after {{ bottom:-10px; }}
.pass-stub .k {{ font-size:.85rem; color:{MUTED}; line-height:1.35; }}
.pass-stub .price {{ font:700 2.6rem/1.05 {FONT}; letter-spacing:-.04em; color:{ECONOMY}; margin:6px 0 4px; }}
.pass-stub .price small {{ font-size:.42em; font-weight:600; letter-spacing:0; }}
.pass-stub .note {{ font-size:.82rem; color:{MUTED}; }}
.pbar {{ position:relative; margin:14px 0 4px; }}
.pbar .bands {{ display:flex; height:8px; border-radius:4px; overflow:hidden; }} .pbar span {{ flex:1; }} .pbar .lo {{ background:#0E7A53; }} .pbar .mid {{ background:#C9CDD3; }} .pbar .hi {{ background:#C2410C; }}
.pbar i {{ position:absolute; top:-4px; width:4px; height:16px; margin-left:-2px; background:{INK}; border-radius:2px; box-shadow:0 0 0 2px #fff; }}
.pbar-k {{ display:flex; justify-content:space-between; font-size:.75rem; color:{MUTED}; }}
.pass-stub .ctx {{ font-size:.82rem; color:{INK}; margin-top:8px; line-height:1.4; }}
.pass-stub .good {{ display:inline-block; font-size:.8rem; font-weight:650; color:{ECONOMY}; border:1px solid {ECONOMY}; border-radius:6px;
  padding:1px 7px; margin-right:6px; }}
.h2h {{ width:100%; border-collapse:collapse; background:{SURFACE}; border-radius:14px; box-shadow:{SHADOW}; overflow:hidden; margin:2px 0 10px; font-size:.95rem; }}
.h2h th, .h2h td {{ padding:10px 14px; text-align:left; vertical-align:top; border-top:1px solid {GRID}; }}
.h2h tr:first-child th {{ border-top:0; color:{MUTED}; font-weight:600; font-size:.85rem; }}
.h2h tbody th, .h2h tr th:first-child {{ color:{MUTED}; font-weight:500; width:34%; }}
.h2h td {{ font-weight:600; color:{INK}; font-variant-numeric:tabular-nums; }}
.h2h.scroll {{ display:block; max-height:520px; overflow:auto; }}
.h2h.scroll tr:first-child th {{ position:sticky; top:0; background:{SURFACE}; z-index:1; }}
.h2h.scroll th:first-child {{ width:auto; white-space:nowrap; }}
.months {{ display:flex; gap:8px; flex-wrap:wrap; margin:4px 0 18px; }}
.months > div {{ background:{SURFACE}; border-radius:12px; padding:10px 14px; min-width:92px; box-shadow:{SHADOW}; }}
.months .m {{ display:block; font-size:.82rem; color:{MUTED}; }} .months .v {{ display:block; font-weight:650; font-size:1.05rem; color:{INK}; }}
.months .low {{ outline:2px solid {ECONOMY}; }} .months .low .v {{ color:{ECONOMY}; }}
@media (max-width: 640px) {{ .pass {{ flex-direction:column; }} .pass-stub {{ width:auto; border-left:0; border-top:2px dashed {GRID}; }}
  .pass-stub::before, .pass-stub::after {{ top:-11px; left:auto; }} .pass-stub::before {{ left:-10px; }} .pass-stub::after {{ right:-10px; }}
  .pass-main {{ padding:18px; }} .pass-fields {{ gap:18px; }} }}
</style>""", unsafe_allow_html=True)

# --- the search bar: everything below follows it ----------------------------------------------------------------------------------------
ANY = "ANY"  # the To entry that turns the page into "where can I fly cheaply?"
far = ov.legs(db, today, tp.HORIZON)  # cash fares as far ahead as a month can be fetched (ov.collect stops at 90 days, enough for the table)
froms = xp.origins(far)
# ponytail: the To list names saved airports by trip_plan.city (one CSV read each, a handful) and the other ~130 by the AEGEAN list's city
cities = {r["iata"]: r["city"] for r in aegean_rows()}
place = lambda a: f"{tp.city(a)} ({a})"


def touched():
    st.session_state["ov-touched"] = True  # the first-load caption goes once the visitor picks anything


def every_route():
    """Every route with fares saved and the cheapest one-way fare on it, as a table that wraps on a phone."""
    st.markdown('<p class="group">The cheapest one-way fare on every route saved, next 90 days</p>', unsafe_allow_html=True)
    cell = lambda v: f"€{v['min']:,.0f} on {pd.Timestamp(v['day']):%a\u00a0%d\u00a0%b}" if v else "no fares"  # a date never breaks mid-way
    t = pd.DataFrame([{"Route": f"{o} → {d}", "Economy": cell(s.get("economy")), "Business": cell(s.get("business"))} for (o, d), s in summary.items()])
    html_table(t.set_index("Route"), cls="h2h scroll")  # many routes: the table scrolls under its header instead of running down the page


q = st.query_params
if q.get("from") in froms:  # ?from=SKG&to=ATH (the Expert, an old Explore link): open the page on that route
    st.session_state["ov-grid-orig"] = q["from"]
    if q.get("to") in cities or q.get("to") == ANY:
        st.session_state[f"ov-grid-dest-{q['from']}"] = q["to"]
    touched()
    q.clear()
# ponytail: the two rows get keys so ui.CSS can lay the second out differently on a phone (Streamlit stacks every column there)
page_head("Overview", "The cheapest AEGEAN trip for your dates, and when to fly.")
c = st.container(key="ov-bar-route").columns([1.1, 1.3, 1.1])
orig = c[0].selectbox("From", froms, format_func=place, key="ov-grid-orig", on_change=touched)
saved = [d for o, d in far if o == orig]
# airports searched from here where Google Flights found no AEGEAN flight on any day: AEGEAN flies there, but not from this airport
# (or not in these months); they go last, saying so, so nobody takes them for a route
none_found = {r[0] for r in db.execute("SELECT dest FROM prices WHERE orig=? AND nonstop=1 GROUP BY dest HAVING COUNT(price)=0", (orig,))}
net, home = network.current(), network.home_airports("A3")
rest = [a for a in cities if a not in saved and a != orig and network.flies(net, "A3", orig, a, home) is not False]  # never a pair it does not fly
options = [ANY] + saved + sorted((a for a in rest if a not in none_found), key=cities.get) + sorted((a for a in rest if a in none_found), key=cities.get)
seasonal = {r["iata"] for r in aegean_rows() if r["seasonal"] == "1"}  # AEGEAN's own list marks them: flights only part of the year
names = {ANY: "Anywhere", **{d: place(d) for d in saved},
         **{a: f"{cities[a]} ({a}) · " + ("no AEGEAN flight from here found" if a in none_found else "not searched yet")
            + (" · seasonal" if a in seasonal else "") for a in rest}}
dkey = f"ov-grid-dest-{orig}"
dest = c[1].selectbox("To", options, format_func=names.get, key=dkey, on_change=touched, **({} if dkey in st.session_state else {"index": 1}))
farday = max(date.fromisoformat(s.index.max()) for f in far.values() for s in f.values() if len(s))  # the last day with fares, any route
when = c[2].date_input("Leaving between", (today + timedelta(1), today + timedelta(30)), min_value=today + timedelta(1),
                       max_value=today + timedelta(tp.HORIZON), key="ov-when", on_change=touched, format="DD/MM/YYYY",
                       help="The days you could leave on: a weekend, mid-December, the whole summer. Dates with no fares saved yet can be "
                            "searched below.")
when = tuple(when) if isinstance(when, (tuple, list)) else (when,)
p = tp.period(when[0], when[-1], farday, today)  # one date while the second is being picked: that single day
c = st.container(key="ov-bar-trip").columns([1.2, 1.35, 1.3, 1.05, 0.7])
trip = c[0].pills("Trip", ["return", "oneway"], default="return", format_func={"return": "Return", "oneway": "One way"}.get, required=True,
                  key="ov-trip", on_change=touched)
nights = (2, 7)
if trip == "return":
    nights = c[1].slider("Nights", 1, 60, (2, 7), key="ov-nights", on_change=touched,
                         help="How long to stay, from the shortest to the longest you would accept: 2 to 7 for a city break, 14 to 21 for a holiday.")
gcab = c[2].pills("Cabin", ["economy", "business"], default="economy", format_func=str.capitalize, required=True, key="ov-grid-cabin",
                  on_change=touched)
pay = c[3].pills("Pay with", ["cash", "miles"], default="cash", format_func={"cash": "Cash €", "miles": "Miles"}.get, required=True,
                 key="ov-grid-pay", on_change=touched)
pax = c[4].number_input("Passengers", 1, 9, 1, key="ov-pax", on_change=touched)

stays = list(range(nights[0], nights[1] + 1))
src = far if pay == "cash" else awards
money = (lambda v: f"€{v:,.0f}") if pay == "cash" else (lambda v: f"{v:,.0f} miles")
# ponytail: the weekday pills render beside the grid below, but the pass must honour them, so their value is read from session state here
wdays = st.session_state.get("ov-days") or DAYS  # none picked: no constraint
who = "one adult" if pax == 1 else f"{pax} adults"
colour = ECONOMY if gcab == "economy" else BUSINESS

# --- Anywhere: every destination from here, cheapest first (Google Flights' Explore) -----------------------------------------------------
def see():
    """See dates for a destination in the Anywhere list: the same page, with that destination in To."""
    if st.session_state.get("xp-see"):
        st.session_state[dkey] = st.session_state["xp-see"]
        touched()


def pick_on_map():
    """A place clicked on the Explore map: the same page, with that destination in To."""
    pts = (st.session_state.get("xp-map") or {}).get("selection", {}).get("points") or []
    if pts and pts[0].get("customdata"):
        st.session_state[dkey] = pts[0]["customdata"]
        touched()


def pick_weekend(wk):
    """A row picked in the weekends table: the same page, with that destination in To and that weekend's dates and nights."""
    sel = (st.session_state.get("xp-wk") or {}).get("selection", {}).get("rows") or []
    if sel:
        r = wk.iloc[sel[0]]
        st.session_state[dkey] = r.dest
        st.session_state["ov-when"] = (r.depart, r.depart)
        st.session_state["ov-trip"] = "return"
        st.session_state["ov-nights"] = (int(r.nights), int(r.nights))
        touched()


def weekend_label(a, b):
    return f"{a:%a} {a.day}{a.strftime(' %b') if a.month != b.month else ''} – {b:%a} {b.day} {b:%b}"


places = pd.read_csv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "airport_places.csv")).set_index("iata")


def go_near(end, a):
    """A nearby airport picked under the pass: the same page, that airport in From (keeping To) or in To."""
    if end == "from":
        st.session_state["ov-grid-orig"] = a
        st.session_state[f"ov-grid-dest-{a}"] = dest
    else:
        st.session_state[dkey] = a
    touched()


if dest == ANY:
    rows = xp.destinations(src, orig, p.start, p.end, gcab, stays)
    st.markdown(f'<p class="group">From {e(tp.city(orig))}, {e(p.name)}, {e(gcab)}, cheapest first</p>', unsafe_allow_html=True)
    if rows.empty:
        st.info(f"No {gcab} {'fares' if pay == 'cash' else 'award seats'} saved from {tp.city(orig)} for {p.name}. "
                "Try the other cabin or another month, or pick a destination to search it.")
        if pay == "cash":  # nothing saved for the period: one request fetches every route from here, both ways where the way back is known
            todo, blank = fa.missing(db, [(orig, d, p.start, p.end) for d in saved] + [(d, orig, p.start, p.end) for d in saved if (d, orig) in far], today)
            if todo:
                fa.button(f"ahead|{orig}|all|{p.start}", f"Search all routes from {tp.city(orig)} on GitHub", todo, f"{p.name.capitalize()} from {tp.city(orig)}")
    else:  # a map of the places with their prices (Google Flights' Explore), then one grouped list, the price trailing (ui.kpis)
        on_map = rows[rows.dest.isin(places.index)]
        if len(on_map) and orig in places.index:
            pts = places.loc[list(on_map.dest)]
            cheap = on_map.price <= on_map.price.quantile(0.34)  # the cheapest third in green, as the hotel map marks cheaper nights
            fig = go.Figure()
            fig.add_scattermap(lat=[places.lat[orig]], lon=[places.lng[orig]], mode="markers", marker=dict(size=14, color=INK),
                               hovertemplate=f"<b>{e(tp.city(orig))}</b><extra></extra>", showlegend=False)
            fig.add_scattermap(lat=pts.lat, lon=pts.lng, mode="markers+text", customdata=list(on_map.dest),
                               text=[f"{tp.city(d)} {money(v * pax)}" for d, v in zip(on_map.dest, on_map.price)], textposition="top center",
                               textfont=dict(size=12, color=INK), marker=dict(size=13, color=["#0E7A53" if c else colour for c in cheap]),
                               hovertemplate="<b>%{text}</b><br>click to see the dates<extra></extra>", showlegend=False)
            lat, lng = list(pts.lat) + [places.lat[orig]], list(pts.lng) + [places.lng[orig]]
            span = max(max(lat) - min(lat), (max(lng) - min(lng)) / 1.6, 1)
            fig.update_layout(height=430, margin=dict(l=0, r=0, t=0, b=0), font=dict(family=FONT),
                              map=dict(style="carto-positron", center=dict(lat=(max(lat) + min(lat)) / 2, lon=(max(lng) + min(lng)) / 2),
                                       zoom=max(1.5, min(7, 7.6 - math.log2(span * 1.3)))))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False, "scrollZoom": True}, key="xp-map", on_select=pick_on_map,
                            selection_mode="points")
            st.caption("Green: the cheapest third of the places. Click a place to see its dates.")
        when_ = lambda r: (f"{r.depart:%a %d %b} → {r.back:%a %d %b} · {int(r.nights)} nights" if pd.notna(r.back) else f"{r.depart:%a %d %b} · one way")
        ndays = (p.end - p.start).days + 1
        kpis([(f"{tp.city(r.dest)} · {r.dest}", money(r.price * pax), f"{when_(r)} · flights on {r.days} of {ndays} days · usually {money(r.typical * pax)}", colour)
              for r in rows.itertuples()])
        st.pills("See dates for", list(rows.dest), format_func=tp.city, key="xp-see", on_change=see)
        st.caption(f"The cheapest trip to each place {p.name}{', for ' + who if pax > 1 else ''}. A place with fares saved one way only shows "
                   "its one-way. \"Usually\" is the price in the middle of all the trips like it. AEGEAN nonstop, from Google Flights.")
    wk = xp.weekends(src, orig, gcab, today, ok=lambda d: network.flies(net, "A3", orig, d, home) is not False)
    if len(wk):
        st.markdown(f'<p class="group">Cheapest weekends away from {e(tp.city(orig))}, next 8 weekends</p>', unsafe_allow_html=True)
        vs = lambda r: "usual price" if r.price == r.typical else f"{'−' if r.price < r.typical else '+'}{money(abs(r.price - r.typical) * pax)}"
        st.dataframe(pd.DataFrame({"Destination": [tp.city(d) for d in wk.dest],
                                   "Weekend": [weekend_label(r.depart, r.back) for r in wk.itertuples()],
                                   "Price": [money(v * pax) for v in wk.price],
                                   "vs usual": [vs(r) for r in wk.itertuples()]}),
                     hide_index=True, width="stretch", key="xp-wk", on_select=lambda: pick_weekend(wk), selection_mode="single-row")
        st.caption("Leaving Thursday, Friday or Saturday, back Sunday or Monday, from the fares saved. \"vs usual\" compares with the "
                   "middle price of that place's weekends. Pick a row to see its dates.")
    every_route()
    st.stop()

# --- one route: the fares in the chosen period, both ways ---------------------------------------------------------------------------------
out_s = src.get((orig, dest), {})
back_s = src.get((dest, orig), {})
out_s = out_s[gcab] if gcab in out_s else pd.Series(dtype=float)
back_s = back_s[gcab] if gcab in back_s else None
if trip == "oneway":
    back_s = None  # one way: the way back is not priced, whatever is saved
best = deals.best_trip(out_s, back_s, p.start, p.end, stays, wdays)
ctx = deals.trip_context(out_s, back_s, p.start, p.end)
# ponytail: the history CSV is read on every rerun (a few hundred rows a day, so years before it takes a visible fraction of a second)
hist = pi.history() if pay == "cash" else None  # every fare change ever seen, for the verdict and the "how it moved" chart; miles have none
gq, gq_out, gq_back, gq_best = False, pd.Series(dtype=float), None, None
gqkey = f"ov-gq|{orig}|{dest}"
if best is not None and gcab == "economy" and pay == "cash" and sx.serves(orig, dest):  # Sky Express beside AEGEAN's answer: economy cash, both airports in its network
    gq_out = sx.cash_by_day(db, orig, dest, p.start, p.end)
    if len(gq_out):  # the toggle only once there is something to compare (its state is kept per route)
        gq = st.toggle("Compare with Sky Express", value=True, key=f"ov-gq-{orig}{dest}",
                       help="Sky Express's economy fares for the same days, nonstop, one adult, fetched on request like AEGEAN's.")
        if gq:
            gq_back = sx.cash_by_day(db, dest, orig, p.start, p.end + timedelta(max(stays))) if back_s is not None else None
            gq_best = deals.best_trip(gq_out, gq_back, p.start, p.end, stays, wdays)
    elif (tok := gh.token()) and not st.session_state.get(f"gh-{gqkey}"):
        if st.button("Search Sky Express fares", type="tertiary", icon=":material/cloud_download:", key=f"gq-fetch-{orig}{dest}",
                     help=f"Sky Express's economy fares both ways for {p.name}, on GitHub's servers like AEGEAN's, to compare them here."):
            try:
                legs = [(orig, dest, p.start, p.end), (dest, orig, p.start, p.end + timedelta(max(stays)))]
                st.session_state[f"gh-{gqkey}"] = (gh.fetch(tok, legs, True, airline="GQ"), time.time())
                st.rerun()  # the button gives way to the progress note at once
            except gh.GitHubError as err:
                st.warning(str(err), icon=":material/warning:")
    github_progress(gqkey, "fetch-route.yml", "data/skyexpress.csv", "Sky Express fares")
    github_note(gqkey)
elif best is not None:  # no comparison here: say why, so it is not taken for missing
    why = ("Sky Express sells economy only" if gcab != "economy" else "Sky Express fares are compared in cash" if pay != "cash"
           else f"Sky Express does not fly {tp.city(orig)} to {tp.city(dest)}")
    st.caption(f"No Sky Express comparison: {why}.")

# --- the answer, as a boarding pass; or, with nothing saved, the one button that searches the route -----------------------------------------
if best is None and wdays != DAYS and deals.best_trip(out_s, back_s, p.start, p.end, stays) is not None:
    st.info(f"Nothing leaves {tp.city(orig)} on a {' or '.join(wdays)} {p.name}. Pick other days to leave on.")
    st.pills("Leave on", DAYS, selection_mode="multi", default=DAYS, key="ov-days")
elif best is None:
    seen = pd.read_sql("SELECT day, price FROM prices WHERE orig=? AND dest=? AND nonstop=1 AND day BETWEEN ? AND ?", db,
                       params=(orig, dest, p.start.isoformat(), p.end.isoformat()))
    if len(seen) and seen.price.isna().all():  # every day checked has no flight: AEGEAN does not fly it (then)
        st.info(no_service_note(seen, orig, dest), icon=":material/event_busy:")
    elif pay == "miles":  # award miles cannot be fetched from here: they are collected on the owner's computer, 90 days ahead
        st.info(f"No {gcab} award seats saved from {tp.city(orig)} to {tp.city(dest)} for {p.name}. Try the other cabin, or pay with cash.")
    else:
        st.info(f"No {gcab} fares saved from {tp.city(orig)} to {tp.city(dest)} for {p.name} yet.")
        todo, _ = fa.missing(db, [(orig, dest, p.start, p.end), (dest, orig, p.start + timedelta(min(stays)), p.end + timedelta(max(stays)))], today)
        if todo:
            fa.button(f"ahead|{orig}|{dest}|{p.start}", "Search this route on GitHub" if (orig, dest) not in far else f"Search {p.name} on GitHub",
                      todo, f"{p.name.capitalize()} from {tp.city(orig)} to {tp.city(dest)}")
else:
    ret = best["back"] is not None
    fields = [("Leave", f"{best['depart']:%a %d %b}")] + ([("Return", f"{best['back']:%a %d %b}"), ("Stay", f"{best['nights']} nights")]
                                                          if ret else [("Trip", "One way")]) + [("Cabin", gcab.capitalize())]
    price = money(best["price"] * pax).replace(" miles", "<small> miles</small>")
    kind = "return" if ret else "one way"
    # what the price means, in at most two short sentences: from our own history of the route's fares when there is enough (price_insights,
    # Google Flights' low / typical / high), else from the trips saved for this period (deals.trip_context)
    said, good = [], ""
    ins = pi.trip(hist, [(orig, dest)] + ([(dest, orig)] if ret else []), gcab, best["price"], today) if hist is not None else None
    if ins and ins["level"]:
        said = [f"Usually €{ins['low'] * pax:,.0f}–€{ins['high'] * pax:,.0f} {kind}.",
                f"{money(best['price'] * pax)} is {dict(low='cheap', typical='about average', high='high')[ins['level']]}."]
    elif ctx and ctx["typical"] > 0:
        said = ["Prices are much the same whichever day you pick." if abs(best["price"] - ctx["typical"]) <= ctx["typical"] * 0.05 else
                f"{'Trips like this' if ret else 'This flight'} usually cost{'' if ret else 's'} about {money(ctx['typical'] * pax)}."]
    if ctx and ctx["typical"] > 0 and best["price"] <= ctx["typical"] * 0.75:
        good = '<span class="good">Good price</span> '
    # Google Flights' price bar: low, typical and high as three bands, a marker where this price sits; from our own history of the route
    # (price_insights) when there is enough, else from every trip saved for this period (low to twice the typical gap above it)
    lo, hi = ((ins["low"], ins["high"]) if ins and ins["level"] else
              (ctx["low"], ctx["typical"] + (ctx["typical"] - ctx["low"])) if ctx and ctx["typical"] > ctx["low"] else (None, None))
    bar = ""
    if lo is not None and hi > lo:
        at = min(max((best["price"] - lo) / (hi - lo), 0), 1) * 100
        bar = (f'<div class="pbar" title="Typical €{lo * pax:,.0f}–€{hi * pax:,.0f}"><div class="bands"><span class="lo"></span>'
               f'<span class="mid"></span><span class="hi"></span></div><i style="left:{max(min(at, 98), 2):.0f}%"></i></div>'
               f'<div class="pbar-k"><span>€{lo * pax:,.0f}</span>'
               f'<span>typical</span><span>€{hi * pax:,.0f}</span></div>')
    with st.container(key="ov-answer"):  # ponytail: keyed so ui.CSS can pin the Book button to the bottom of a phone screen while the pass shows
        st.markdown(
            f'<div class="pass"><div class="pass-main"><div class="pass-codes">{e(orig)}<span class="line"></span>{e(dest)}</div>'
            f'<div class="pass-cities"><span>{e(tp.city(orig))}</span><span>{e(tp.city(dest))}</span></div><dl class="pass-fields">'
            + "".join(f"<div><dt>{k}</dt><dd>{e(v)}</dd></div>" for k, v in fields)
            + f'</dl></div><div class="pass-stub"><span class="k">{"AEGEAN " if gq else ""}Cheapest {kind} · {e(tp.city(orig))} → {e(tp.city(dest))}</span>'
            f'<span class="price">{price}</span><span class="note">{"plus taxes and fees" if pay == "miles" else who}, leaving in {e(p.name)}</span>'
            + (f'<span class="ctx">{good}{e(" ".join(said))}</span>' if said or good else "") + bar + '</div></div>', unsafe_allow_html=True)
        if pax > 1:
            st.caption(f"The price is for {pax} adults, from fares collected for one adult; the airline may price a group differently.")
        # nearby airports (Skyscanner's "include nearby airports"): the same window and stay from or to an airport close by, when cheaper;
        # only pairs the From/To pickers can show (cash fares saved), never one the network says is not flown
        near = [r for r in nearby.cheaper(src, places, orig, dest, p.start, p.end, gcab, stays, best["price"], wdays, not ret,
                                          lambda o, d: network.flies(net, "A3", o, d, home))
                if ((r["airport"], dest) if r["end"] == "from" else (orig, r["airport"])) in far]
        if near:
            st.markdown('<p class="group">Cheaper from a nearby airport</p>', unsafe_allow_html=True)
            for i, r in enumerate(near):
                dates = f"{r['depart']:%a %d %b}" + (f" → {r['back']:%a %d %b}" if r["back"] else "")
                cc = st.columns([5, 1.4], vertical_alignment="center")
                cc[0].markdown(f"**{r['end'].capitalize()} {e(place(r['airport']))} it's {money(r['saving'] * pax)} less:** "
                               f"{money(r['price'] * pax)} on {dates}. {r['airport']} is {r['km']} km from "
                               f"{e(tp.city(orig if r['end'] == 'from' else dest))} in a straight line.")
                cc[1].button(f"Search {r['airport']}", key=f"ov-near-{i}", type="tertiary", icon=":material/near_me:", on_click=go_near,
                             args=(r["end"], r["airport"]))
        links = dict(depart=best["depart"], back=best["back"], adults=pax, cabin=gcab)
        c = st.columns(2)
        c[0].link_button("Book on aegeanair.com", booking.aegean_url(orig, dest, **links), type="primary", width="stretch", icon=":material/airplane_ticket:")
        c[1].link_button("Check it on Google Flights", booking.google_url(orig, dest, **links), width="stretch", icon=":material/open_in_new:")
        # the pass as the Alerts page reads it (views/alerts.py, every name optional there): the trip shown, its per-adult fare as the limit
        # to start from, and the bar's nights and window as the Expert link carries them
        alert = {"route": f"{orig}-{dest}", "cabin": gcab, "unit": "eur" if pay == "cash" else "miles", "price": f"{best['price']:.0f}",
                 "depart": best["depart"].isoformat(), "trip": "return" if ret else "oneway", "nights": f"{nights[0]}-{nights[1]}",
                 "when": f"{p.start},{p.end}"}
        if ret:
            alert["back"] = best["back"].isoformat()
        c = st.columns(2)
        for col, page, label, icon, params in (
                (c[0], "views/alerts.py", "Track this price: tell me when it drops", ":material/notifications_active:", alert),
                (c[1], "views/expert.py", "Ask the expert about it", ":material/forum:",  # every filter the pass used, so the Expert opens on it
                 {"from": orig, "to": dest, "cabin": gcab, "pay": pay, "nights": f"{nights[0]}-{nights[1]}", "trip": trip, "when": f"{p.start},{p.end}"})):
            try:
                col.page_link(page, label=label, icon=icon, query_params=params)
            except Exception:  # the page runs on its own (tests), outside the app's navigation
                pass
        if hist is not None:  # the trip's fare (out + back) at every check since we started watching; nothing under 2 checks
            legs = [(orig, dest, best["depart"].isoformat())] + ([(dest, orig, best["back"].isoformat())] if ret else [])
            hs = pi.trip_series(hist, legs, gcab)
            if note := pi.moved_note(hs):
                st.plotly_chart(price_chart(pd.DataFrame({"t": hs}), {"t": ("Fare for your dates", colour)}).update_layout(
                    height=180, showlegend=False, margin=dict(l=28, r=8, t=8, b=0)), width="stretch", config={"displayModeBar": False},
                    key=f"hist-{orig}{dest}{gcab}")
                st.caption(note)
        # the flights of the pass's days, Google Flights' list with a time-of-day filter: saved with the fares for the cheapest days, the
        # button fetches any other day
        flight_times.render(orig, dest, gcab, best["depart"], best["back"], f"details-{orig}{dest}{gcab}")
        hotels = tp.hotels(db, dest) if ret else []
        if hotels and pay == "cash":
            h = hotels[0]
            nights_ = night_prices(db, h["query"], h["filt"], h["adults"], best["depart"], best["back"])
            stay = [(best["depart"] + timedelta(i)).isoformat() for i in range(best["nights"])]
            if all(s in nights_.index for s in stay):
                cost = sum(nights_[s] for s in stay)
                st.caption(f"With {h['name']} ({h['adults']} adults) for those {best['nights']} nights: €{cost:,.0f}, "
                           f"so €{best['price'] * pax + cost:,.0f} for the whole trip.")

# --- cash or miles: for each cabin, the day an award seat replaces the most cash in the period, and whether that beats paying ---------------
if dest != ANY:
    am, at_, _ = load_award(db, orig, dest, True, p.start, p.end)
    if len(am):
        st.markdown('<p class="group">Cash or miles?</p>', unsafe_allow_html=True)
        lines = [miles_value.sentence(cab.capitalize(), miles_value.best_use(cash_by_day(db, orig, dest, cab, True, p.start, p.end),
                                                                            am.get(cab, pd.Series(dtype=float)), at_.get(cab)))
                 for cab in ("economy", "business")]
        st.markdown("\n".join(f"- {e(l)}" for l in lines))
        st.caption(f"One way {tp.city(orig)} → {tp.city(dest)}, {p.name}: AEGEAN Miles+Bonus award seats as last collected, against the cash "
                   f"fare the same day. Miles count as worth €{miles_value.WORTH:.0f} for each 1,000; the Flights page lets you change that.")

# --- Sky Express against AEGEAN (the toggle under the bar), economy only: one table a traveller can read in one look -----------------
if gq:
    st.markdown('<p class="group">AEGEAN or Sky Express?</p>', unsafe_allow_html=True)
    span = (out_s.index >= p.start.isoformat()) & (out_s.index <= p.end.isoformat())  # iso days sort as text
    a3_back = back_s[(back_s.index >= p.start.isoformat()) & (back_s.index <= (p.end + timedelta(max(stays))).isoformat())] if back_s is not None else None
    args = (best, gq_best, out_s[span], a3_back, gq_out, gq_back, pax)
    st.markdown(sx.html_table(sx.head_to_head(*args)), unsafe_allow_html=True)
    st.markdown(f"**{e(sx.verdict(*args))}**")
    if (m := mix.cheapest_mix(out_s[span], a3_back, gq_out, gq_back, stays, wdays)):  # AEGEAN one way, Sky Express the other
        st.markdown(f"**{e(mix.sentence(m, pax))}**")
        st.caption(mix.NOTE)
    st.caption(f"Economy, nonstop, {who}, as last checked. Pick other nights or days above and both airlines follow.")
    st.markdown(f'<p class="group">Day by day, one way {e(orig)} → {e(dest)}</p>', unsafe_allow_html=True)
    st.markdown(sx.html_table(sx.table(sx.compare(out_s[span], gq_out))), unsafe_allow_html=True)

# --- when to fly: the weekday, how far ahead, and how this exact fare has moved; in the open, right under the answer ---------------------
if best is not None and pay == "cash" and len(out_s):
    label, config = f"{tp.city(orig)} → {tp.city(dest)}", {"displayModeBar": False}
    wd, ah = weekday_stats(out_s), ahead_stats(out_s, today)
    moved = {}
    if hist is not None:
        moved = {"out": (f"Out {best['depart']:%a %d %b}", ECONOMY, pi.series(hist, orig, dest, gcab, best["depart"].isoformat()))}
        if best["back"] is not None:
            moved["back"] = (f"Back {best['back']:%a %d %b}", MUTED, pi.series(hist, dest, orig, gcab, best["back"].isoformat()))
        if max(len(x) for _, _, x in moved.values()) < 3:  # one or two points are not a line
            moved = {}
    st.markdown('<p class="group">When to fly</p>', unsafe_allow_html=True)
    cols = st.columns(3 if moved else 2, gap="medium")
    with cols[0]:
        st.markdown(f'<p class="card-title">{e(weekday_takeaway(wd, label))}</p>', unsafe_allow_html=True)
        if len(wd) >= 3:
            st.plotly_chart(weekday_chart(wd, colour).update_layout(height=220), width="stretch", config=config)
        st.caption(f"The average {gcab} fare by the weekday you leave, over the {len(out_s)} days with a fare saved.")
    with cols[1]:
        st.markdown(f'<p class="card-title">{e(ahead_takeaway(ah, label))}</p>', unsafe_allow_html=True)
        if len(ah) >= 2:
            st.plotly_chart(ahead_chart(ah, colour).update_layout(height=220), width="stretch", config=config)
        st.caption("What a flight costs by how many weeks away it is, from today's fares for different dates.")
    if moved:
        with cols[2]:
            first = min(x.index.min() for _, _, x in moved.values() if len(x))
            st.markdown(f'<p class="card-title">How this fare has moved since {pd.Timestamp(first):%-d %b}</p>', unsafe_allow_html=True)
            fig = price_chart(pd.DataFrame({k: x for k, (_, _, x) in moved.items()}).sort_index(), {k: (l, c) for k, (l, c, _) in moved.items()})
            st.plotly_chart(fig.update_layout(height=220, legend=dict(y=1.25)), width="stretch", config=config)
            st.caption("The fare for those exact days each time it was checked. The ring marks the lowest it has been.")

# --- the dates behind it ------------------------------------------------------------------------------------------------------------------
if best is not None and back_s is not None and len(back_s.dropna()):
    st.markdown('<p class="group">Pick your dates</p>', unsafe_allow_html=True)
    st.pills("Leave on", DAYS, selection_mode="multi", default=DAYS, key="ov-days")
    grid = deals.trip_grid(out_s, back_s, p.start, p.end, stays, wdays)
    if len(grid):
        at = grid.index.get_loc(f"{best['depart']:%a %d %b}") if f"{best['depart']:%a %d %b}" in grid.index else 0
        start = max(0, min(at - 3, len(grid) - 8))  # the week around the pass's departure first, not the whole month; the pass's cell is in it
        shown = grid if st.session_state.get(f"ov-grid-all-{orig}{dest}") or len(grid) <= 10 else grid.iloc[start:start + 8]
        grid_frame(deals.grid_html(shown, ("€{:,.0f}" if pay == "cash" else "{:,.0f}").format), len(shown))
        if len(shown) < len(grid):
            st.button(f"Show all {len(grid)} departure days", key=f"ov-grid-more-{orig}{dest}", type="tertiary",
                      on_click=lambda: st.session_state.__setitem__(f"ov-grid-all-{orig}{dest}", True))
        same = grid.min().min() == grid.max().max()
        st.caption(f"Return fares{' in miles' if pay == 'miles' else ''} for {who}. "
                   + ("Every trip shown costs the same; green marks the dates with seats both ways." if same else
                      "Darkest green is cheapest; light green is within 10% of it.")
                   + (" Scroll sideways for longer stays." if len(grid.columns) > 4 else ""))
    else:
        st.caption("No return trips saved for those days" + (" and nights" if len(wdays) < 7 or len(stays) < 6 else "") + ". "
                   "Try more nights, or other days to leave on.")
    # the way back not saved for this period (the pass fell back to a one-way): one button fetches what is missing, both directions
    todo, blank = fa.missing(db, [(orig, dest, p.start, p.end), (dest, orig, p.start + timedelta(min(stays)), p.end + timedelta(max(stays)))], today)
    if blank and pay == "cash":
        fa.button(f"ahead|{orig}|{dest}|{p.start}", f"Search the way back for {p.name} on GitHub", todo,
                  f"{p.name.capitalize()} from {tp.city(orig)} to {tp.city(dest)}")
elif best is not None and trip == "oneway":
    st.markdown('<p class="group">Pick your day</p>', unsafe_allow_html=True)
    st.pills("Leave on", DAYS, selection_mode="multi", default=DAYS, key="ov-days")
    days = pd.to_numeric(out_s, errors="coerce").dropna()
    days = days[(days.index >= p.start.isoformat()) & (days.index <= p.end.isoformat())]
    days = days[[f"{pd.Timestamp(d):%a}" in wdays for d in days.index]]
    if len(days):
        one = pd.DataFrame({"One way": days.values * pax}, index=[f"{pd.Timestamp(d):%a %d %b}" for d in days.index])
        grid_frame(deals.grid_html(one, ("€{:,.0f}" if pay == "cash" else "{:,.0f}").format), len(one))
        st.caption(f"One-way fares{' in miles' if pay == 'miles' else ''} for {who}. Darkest green is cheapest; light green is within 10% of it.")
    else:
        st.caption("No one-way fares saved for those days.")
elif best is not None:
    st.caption(f"Only {tp.city(orig)} → {tp.city(dest)} is saved, so return trips can't be priced. Search {tp.city(dest)} → {tp.city(orig)} on the "
               "Flights page to add them.")

with st.expander("Every route with fares saved, cheapest one-way in the next 90 days"):  # the long table, out of the way of the answer
    every_route()

if last:
    st.caption(f"AEGEAN nonstop fares from Google Flights, last checked {pd.Timestamp(last):%a %d %b, %H:%M}. Sky Express fares are its "
               "economy nonstop fares. Prices are per adult; the airline sets the final price when you book.")
