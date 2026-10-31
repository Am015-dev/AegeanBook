"""Overview, the first page and the one place to find a trip: one search bar (From, To or Anywhere, When, Nights, Cabin, Pay with,
Passengers) and one answer under it. A route gives a boarding pass with the cheapest trip, the dates behind it and, in expanders, what the
saved fares say about weekdays and booking ahead; Anywhere lists every destination with fares saved, cheapest first; a route with nothing
saved gets one button that fetches it on GitHub. The AI expert lives on its own page; this one shows only numbers the data proves."""
import html
import importlib
import time
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import booking
import deals
import explore as xp
import fetch_ahead as fa
import flight_times
import gh
import overview as ov
import price_insights as pi
import skyexpress as sx
import trip_plan as tp
from db import connect
from insights import DAYS, ahead_chart, ahead_stats, ahead_takeaway, weekday_chart, weekday_stats, weekday_takeaway
from trip_cost import night_prices
from ui import (BUSINESS, ECONOMY, FONT, GRID, INK, MUTED, SHADOW, SURFACE, aegean_rows, github_note, github_progress, grid_frame, html_table,
                kpis, no_service_note, price_chart, route)

if getattr(ov, "VERSION", 0) < 4:  # Streamlit Cloud keeps an older copy of a module across redeploys
    ov = importlib.reload(ov)
if getattr(tp, "VERSION", 0) < 6:
    tp = importlib.reload(tp)
if getattr(deals, "VERSION", 0) < 6:
    deals = importlib.reload(deals)
if getattr(pi, "VERSION", 0) < 1:
    pi = importlib.reload(pi)
if getattr(sx, "VERSION", 0) < 2:
    sx = importlib.reload(sx)
if getattr(fa, "VERSION", 0) < 1:
    fa = importlib.reload(fa)
if getattr(xp, "VERSION", 0) < 2:
    xp = importlib.reload(xp)
db = connect()
today = date.today()
fares, summary, trips, weekday, hotel_night, awards = ov.collect(db, today)
last = db.execute("SELECT MAX(fetched) FROM prices WHERE fetched IS NOT NULL").fetchone()[0]
route("Overview", None, "Where to, when, and for how much" + (f" · fares checked {pd.Timestamp(last):%a %d %b, %H:%M}" if last else ""))
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
c = st.container(key="ov-bar-route").columns([1.1, 1.3, 1.1])
orig = c[0].selectbox("From", froms, format_func=place, key="ov-grid-orig", on_change=touched,
                      help="Every airport with fares saved: the daily routes, plus any route searched from here or on the Flights page.")
saved = [d for o, d in far if o == orig]
options = [ANY] + saved + sorted((a for a in cities if a not in saved and a != orig), key=cities.get)
names = {ANY: "Anywhere", **{d: place(d) for d in saved}, **{a: f"{cities[a]} ({a}) · no fares yet" for a in options[1 + len(saved):]}}
dkey = f"ov-grid-dest-{orig}"
dest = c[1].selectbox("To", options, format_func=names.get, key=dkey, on_change=touched, **({} if dkey in st.session_state else {"index": 1}),
                      help="Anywhere lists every destination with fares saved, cheapest first. An airport with no fares yet can be searched "
                           "from here. Only AEGEAN's destinations are listed.")
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
pax = c[4].number_input("Passengers", 1, 9, 1, key="ov-pax", on_change=touched, help="Adults. Fares are collected for one adult; the pass and "
                        "the booking links use this number.")

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
    else:  # one grouped list, the price trailing (ui.kpis), then one pill row to open a destination's dates
        when_ = lambda r: (f"{r.depart:%a %d %b} → {r.back:%a %d %b} · {int(r.nights)} nights" if pd.notna(r.back) else f"{r.depart:%a %d %b} · one way")
        ndays = (p.end - p.start).days + 1
        kpis([(f"{tp.city(r.dest)} · {r.dest}", money(r.price * pax), f"{when_(r)} · flights on {r.days} of {ndays} days · usually {money(r.typical * pax)}", colour)
              for r in rows.itertuples()])
        st.pills("See dates for", list(rows.dest), format_func=tp.city, key="xp-see", on_change=see)
        st.caption(f"The cheapest trip to each place {p.name}{', for ' + who if pax > 1 else ''}. A place with fares saved one way only shows "
                   "its one-way. \"Usually\" is the price in the middle of all the trips like it. AEGEAN nonstop, from Google Flights.")
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
        gq = st.toggle("Compare with Sky Express", key=f"ov-gq-{orig}{dest}",
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
    with st.container(key="ov-answer"):  # ponytail: keyed so ui.CSS can pin the Book button to the bottom of a phone screen while the pass shows
        st.markdown(
            f'<div class="pass"><div class="pass-main"><div class="pass-codes">{e(orig)}<span class="line"></span>{e(dest)}</div>'
            f'<div class="pass-cities"><span>{e(tp.city(orig))}</span><span>{e(tp.city(dest))}</span></div><dl class="pass-fields">'
            + "".join(f"<div><dt>{k}</dt><dd>{e(v)}</dd></div>" for k, v in fields)
            + f'</dl></div><div class="pass-stub"><span class="k">{"AEGEAN " if gq else ""}Cheapest {kind} · {e(tp.city(orig))} → {e(tp.city(dest))}</span>'
            f'<span class="price">{price}</span><span class="note">{"plus taxes and fees" if pay == "miles" else who}, leaving in {e(p.name)}</span>'
            + (f'<span class="ctx">{good}{e(" ".join(said))}</span>' if said or good else "") + '</div></div>', unsafe_allow_html=True)
        if pax > 1:
            st.caption(f"The price is for {pax} adults, from fares collected for one adult; the airline may price a group differently.")
        links = dict(depart=best["depart"], back=best["back"], adults=pax, cabin=gcab)
        st.link_button("Book on aegeanair.com", booking.aegean_url(orig, dest, **links), type="primary", width="content", icon=":material/airplane_ticket:")
        # the pass as the Alerts page reads it (views/alerts.py, every name optional there): the trip shown, its per-adult fare as the limit
        # to start from, and the bar's nights and window as the Expert link carries them
        alert = {"route": f"{orig}-{dest}", "cabin": gcab, "unit": "eur" if pay == "cash" else "miles", "price": f"{best['price']:.0f}",
                 "depart": best["depart"].isoformat(), "trip": "return" if ret else "oneway", "nights": f"{nights[0]}-{nights[1]}",
                 "when": f"{p.start},{p.end}"}
        if ret:
            alert["back"] = best["back"].isoformat()
        c = st.columns(3)
        c[0].link_button("Check it on Google Flights", booking.google_url(orig, dest, **links), type="tertiary", icon=":material/open_in_new:")
        for col, page, label, icon, params in (
                (c[1], "views/alerts.py", "Alert me if it gets cheaper", ":material/notifications:", alert),
                (c[2], "views/expert.py", "Ask the expert about it", ":material/forum:",  # every filter the pass used, so the Expert opens on it
                 {"from": orig, "to": dest, "cabin": gcab, "pay": pay, "nights": f"{nights[0]}-{nights[1]}", "trip": trip, "when": f"{p.start},{p.end}"})):
            try:
                col.page_link(page, label=label, icon=icon, query_params=params)
            except Exception:  # the page runs on its own (tests), outside the app's navigation
                pass
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

# --- Sky Express against AEGEAN (the toggle under the bar), economy only: one table a traveller can read in one look -----------------
if gq:
    st.markdown('<p class="group">AEGEAN or Sky Express?</p>', unsafe_allow_html=True)
    span = (out_s.index >= p.start.isoformat()) & (out_s.index <= p.end.isoformat())  # iso days sort as text
    a3_back = back_s[(back_s.index >= p.start.isoformat()) & (back_s.index <= (p.end + timedelta(max(stays))).isoformat())] if back_s is not None else None
    args = (best, gq_best, out_s[span], a3_back, gq_out, gq_back, pax)
    st.markdown(sx.html_table(sx.head_to_head(*args)), unsafe_allow_html=True)
    st.markdown(f"**{e(sx.verdict(*args))}**")
    st.caption(f"Economy, nonstop, {who}, as last checked. Pick other nights or days above and both airlines follow.")
    st.markdown(f'<p class="group">Day by day, one way {e(orig)} → {e(dest)}</p>', unsafe_allow_html=True)
    st.markdown(sx.html_table(sx.table(sx.compare(out_s[span], gq_out))), unsafe_allow_html=True)

# --- by month -----------------------------------------------------------------------------------------------------------------------------
m = deals.by_month(out_s)
if len(m) > 1:
    st.markdown(f'<p class="group">Cheapest {"AEGEAN " if gq else ""}one-way {e(orig)} → {e(dest)} by month</p>', unsafe_allow_html=True)
    st.markdown('<div class="months">' + "".join(
        f'<div class="{"low" if v == m.min() else ""}"><div class="m">{e(k)}</div><div class="v">{e(money(v))}</div></div>'
        for k, v in m.items()) + "</div>", unsafe_allow_html=True)

# --- the dates behind it ------------------------------------------------------------------------------------------------------------------
if best is not None and back_s is not None and len(back_s.dropna()):
    st.markdown('<p class="group">Pick your dates</p>', unsafe_allow_html=True)
    st.pills("Leave on", DAYS, selection_mode="multi", default=DAYS, key="ov-days")
    grid = deals.trip_grid(out_s, back_s, p.start, p.end, stays, wdays)
    if len(grid):
        grid_frame(deals.grid_html(grid, ("€{:,.0f}" if pay == "cash" else "{:,.0f}").format), len(grid))
        same = grid.min().min() == grid.max().max()
        st.caption(f"Return {tp.city(orig)} → {tp.city(dest)} → {tp.city(orig)}{' in miles' if pay == 'miles' else ''}, {who}, by the day you "
                   "leave and the nights you stay. "
                   + ("Every trip shown costs the same; green marks the dates with seats both ways." if same else
                      "Darkest green is the cheapest; light green is within 10% of it.")
                   + (" Scroll the table sideways for longer stays." if len(grid.columns) > 4 else ""))
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
        st.caption(f"One way {tp.city(orig)} → {tp.city(dest)}{' in miles' if pay == 'miles' else ''}, {who}, by the day you leave. "
                   "Darkest green is the cheapest; light green is within 10% of it.")
    else:
        st.caption("No one-way fares saved for those days.")
elif best is not None:
    st.caption(f"Only {tp.city(orig)} → {tp.city(dest)} is saved, so return trips can't be priced. Search {tp.city(dest)} → {tp.city(orig)} on the "
               "Flights page to add them.")

# --- how the pass's fares moved over our snapshots, in the open once there is something to draw (Google Flights' price graph) ------------
if best is not None and hist is not None:
    moved = {"out": (f"Out {best['depart']:%a %d %b}", ECONOMY, pi.series(hist, orig, dest, gcab, best["depart"].isoformat()))}
    if best["back"] is not None:
        moved["back"] = (f"Back {best['back']:%a %d %b}", MUTED, pi.series(hist, dest, orig, gcab, best["back"].isoformat()))
    if max(len(s) for _, _, s in moved.values()) >= 3:  # one or two points are not a line
        st.markdown('<p class="group">How this fare has moved</p>', unsafe_allow_html=True)
        fig = price_chart(pd.DataFrame({k: s for k, (_, _, s) in moved.items()}).sort_index(), {k: (l, c) for k, (l, c, _) in moved.items()})
        fig.update_layout(height=260)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        first = min(s.index.min() for _, _, s in moved.values() if len(s))
        st.caption(f"The fare for those days each time it was checked, since {pd.Timestamp(first):%d %b}. The ring marks the lowest it has been.")

# --- when to fly: what the saved fares say about weekdays and booking ahead, in the open, side by side -------------------------------------
if pay == "cash" and len(out_s):
    label, config = f"{tp.city(orig)} → {tp.city(dest)}", {"displayModeBar": False}
    wd, ah = weekday_stats(out_s), ahead_stats(out_s, today)
    st.markdown('<p class="group">When to fly</p>', unsafe_allow_html=True)
    c = st.columns(2, gap="medium")
    with c[0]:
        st.markdown(f'<p class="card-title">{e(weekday_takeaway(wd, label))}</p>', unsafe_allow_html=True)
        if len(wd) >= 3:
            st.plotly_chart(weekday_chart(wd, colour), width="stretch", config=config)
        st.caption(f"The average {gcab} fare by the weekday you leave, over the {len(out_s)} days with a fare saved.")
    with c[1]:
        st.markdown(f'<p class="card-title">{e(ahead_takeaway(ah, label))}</p>', unsafe_allow_html=True)
        if len(ah) >= 2:
            st.plotly_chart(ahead_chart(ah, colour), width="stretch", config=config)
        st.caption("What a flight costs by how many weeks away it is, from today's fares for different dates.")

with st.expander("Every route with fares saved, cheapest one-way in the next 90 days"):  # the long table, out of the way of the answer
    every_route()
