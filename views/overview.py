"""Overview, the first page: pick a route and see the answer to "when should I fly, and for how much?" as a boarding pass, then the
month-by-month view, the date grid behind it, one tip, and every route with fares saved. The AI expert lives on its own page; this one
shows only numbers the data proves, so it never changes unless the fares do."""
import html
import importlib
import urllib.parse
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import deals
import overview as ov
import trip_plan as tp
from db import connect
from insights import DAYS, FULL, weekday_stats
from trip_cost import night_prices
from ui import ECONOMY, FONT, GRID, INK, MUTED, SHADOW, SURFACE, route

if getattr(ov, "VERSION", 0) < 3:  # Streamlit Cloud keeps an older copy of a module across redeploys
    ov = importlib.reload(ov)
if getattr(tp, "VERSION", 0) < 3:
    tp = importlib.reload(tp)
if getattr(deals, "VERSION", 0) < 5:
    deals = importlib.reload(deals)
db = connect()
today = date.today()
fares, summary, trips, weekday, hotel_night, awards = ov.collect(db, today)
last = db.execute("SELECT MAX(fetched) FROM prices WHERE fetched IS NOT NULL").fetchone()[0]
route("Overview", None, "When to fly, and for how much" + (f" · fares checked {pd.Timestamp(last):%a %d %b, %H:%M}" if last else ""))
if not summary:
    st.info("No fares are saved yet. The daily check fills this page every morning; or open Flights and press Update prices.")
    st.stop()

e = html.escape
st.markdown(f"""<style>
.pass {{ display:flex; background:{SURFACE}; border-radius:18px; box-shadow:{SHADOW}; margin:6px 0 10px; overflow:hidden; }}
.pass-main {{ flex:1; padding:22px 26px; min-width:0; }}
.pass-codes {{ display:flex; align-items:center; gap:14px; font:650 clamp(2rem,6vw,2.8rem)/1 {FONT}; letter-spacing:-.035em; color:{INK}; }}
.pass-codes .line {{ flex:1; height:2px; background:{GRID}; position:relative; min-width:30px; }}
.pass-codes .line::after {{ content:'✈'; position:absolute; left:50%; top:-.62em; transform:translateX(-50%); font-size:.42em;
  color:{MUTED}; background:{SURFACE}; padding:0 6px; letter-spacing:0; }}
.pass-cities {{ display:flex; justify-content:space-between; color:{MUTED}; font-size:.85rem; margin-top:6px; }}
.pass-fields {{ display:flex; gap:28px; flex-wrap:wrap; margin:18px 0 0; }}
.pass-fields dt {{ font-size:.7rem; text-transform:uppercase; letter-spacing:.08em; color:{MUTED}; margin:0; }}
.pass-fields dd {{ font-size:1.02rem; font-weight:600; color:{INK}; margin:2px 0 0; }}
.pass-stub {{ position:relative; width:220px; padding:22px 24px; border-left:2px dashed {GRID}; display:flex; flex-direction:column;
  justify-content:center; }}
.pass-stub::before, .pass-stub::after {{ content:''; position:absolute; left:-11px; width:20px; height:20px; border-radius:50%;
  background:var(--background-color, #F5F5F7); }}
.pass-stub::before {{ top:-10px; }} .pass-stub::after {{ bottom:-10px; }}
.pass-stub .k {{ font-size:.7rem; text-transform:uppercase; letter-spacing:.08em; color:{MUTED}; }}
.pass-stub .price {{ font:700 2.6rem/1.05 {FONT}; letter-spacing:-.04em; color:{ECONOMY}; margin:6px 0 4px; }}
.pass-stub .price small {{ font-size:.42em; font-weight:600; letter-spacing:0; }}
.pass-stub .note {{ font-size:.82rem; color:{MUTED}; }}
.pass-stub .ctx {{ font-size:.78rem; color:{MUTED}; margin-top:8px; line-height:1.35; }}
.pass-stub .good {{ display:inline-block; font-size:.68rem; font-weight:650; color:{ECONOMY}; border:1px solid {ECONOMY}; border-radius:6px;
  padding:1px 6px; margin-right:6px; vertical-align:1px; }}
.months {{ display:flex; gap:8px; flex-wrap:wrap; margin:4px 0 18px; }}
.months > div {{ background:{SURFACE}; border-radius:12px; padding:10px 14px; min-width:92px; box-shadow:{SHADOW}; }}
.months .m {{ display:block; font-size:.75rem; color:{MUTED}; }} .months .v {{ display:block; font-weight:650; font-size:1.05rem; color:{INK}; }}
.months .low {{ outline:2px solid {ECONOMY}; }} .months .low .v {{ color:{ECONOMY}; }}
@media (max-width: 640px) {{ .pass {{ flex-direction:column; }} .pass-stub {{ width:auto; border-left:0; border-top:2px dashed {GRID}; }}
  .pass-stub::before, .pass-stub::after {{ top:-11px; left:auto; }} .pass-stub::before {{ left:-10px; }} .pass-stub::after {{ right:-10px; }}
  .pass-main {{ padding:18px; }} .pass-fields {{ gap:18px; }} }}
</style>""", unsafe_allow_html=True)

# --- the route bar: everything below follows it -------------------------------------------------------------------------------------
place = lambda a: f"{tp.city(a)} ({a})"
froms = list(dict.fromkeys(o for o, _ in fares))
c = st.columns([1.2, 1.2, 1, 1])
orig = c[0].selectbox("From", froms, format_func=place, key="ov-grid-orig",
                      help="Every airport with fares saved: the daily routes, plus any route searched on the Flights page.")
dests = [d for o, d in fares if o == orig]
dest = c[1].selectbox("To", dests, format_func=place, key=f"ov-grid-dest-{orig}")
gcab = c[2].pills("Cabin", ["economy", "business"], default="economy", format_func=str.capitalize, required=True, key="ov-grid-cabin")
pay = c[3].pills("Pay with", ["cash", "miles"], default="cash", format_func={"cash": "Cash €", "miles": "Miles"}.get, required=True,
                 key="ov-grid-pay")

src = fares if pay == "cash" else awards
out_s = src.get((orig, dest), {})
back_s = src.get((dest, orig), {})
out_s = out_s[gcab] if gcab in out_s else pd.Series(dtype=float)
back_s = back_s[gcab] if gcab in back_s else None
lastday = max(date.fromisoformat(s.index.max()) for f in fares.values() for s in f.values() if len(s))
money = (lambda v: f"€{v:,.0f}") if pay == "cash" else (lambda v: f"{v:,.0f} miles")
NIGHTS = [2, 3, 4, 5, 6, 7]
fkey = f"{orig}{dest}{gcab}{pay}"
# ponytail: the filter pills render beside the grid below, but the pass must honour them, so their values are read from session state here
wdays = st.session_state.get(f"ov-grid-days-{fkey}") or DAYS  # none picked: no constraint
stays = sorted(st.session_state.get(f"ov-grid-nights-{fkey}") or NIGHTS)
best = deals.best_trip(out_s, back_s, today + timedelta(1), lastday, stays, wdays)
ctx = deals.trip_context(out_s, back_s, today + timedelta(1), lastday)

# --- the answer, as a boarding pass ---------------------------------------------------------------------------------------------------
if best is None:
    st.info(f"No {gcab} {'fares' if pay == 'cash' else 'award seats'} saved from {tp.city(orig)} to {tp.city(dest)}. "
            "Try the other cabin, or search this route on the Flights page.")
else:
    ret = best["back"] is not None
    fields = [("Leave", f"{best['depart']:%a %d %b}")] + ([("Return", f"{best['back']:%a %d %b}"), ("Stay", f"{best['nights']} nights")]
                                                          if ret else [("Trip", "One way")]) + [("Cabin", gcab.capitalize())]
    price = money(best["price"])
    price = price.replace(" miles", "<small> miles</small>")
    context = ""
    if ctx and ctx["typical"] > 0:
        say = f"a typical {'trip' if ret else 'day'} costs {money(ctx['typical'])}"
        if abs(best["price"] - ctx["typical"]) <= ctx["typical"] * 0.05:
            say = "prices are flat"
        say += f" · lowest of {ctx['days']} days" if best["price"] <= ctx["low"] else f" · lowest is {money(ctx['low'])}"
        good = '<span class="good">Good price</span>' if best["price"] <= ctx["typical"] * 0.75 else ""
        context = f'<span class="ctx">{good}{e(say)}</span>'
    st.markdown(
        f'<div class="pass"><div class="pass-main"><div class="pass-codes">{e(orig)}<span class="line"></span>{e(dest)}</div>'
        f'<div class="pass-cities"><span>{e(tp.city(orig))}</span><span>{e(tp.city(dest))}</span></div><dl class="pass-fields">'
        + "".join(f"<div><dt>{k}</dt><dd>{e(v)}</dd></div>" for k, v in fields)
        + f'</dl></div><div class="pass-stub"><span class="k">Cheapest {"return" if ret else "one way"}</span>'
        f'<span class="price">{price}</span><span class="note">'
        f'{"plus taxes and fees, " if pay == "miles" else "one adult, "}best of the next {(lastday - today).days} days</span>{context}</div></div>',
        unsafe_allow_html=True)
    q = f"Flights from {orig} to {dest} on {best['depart']:%Y-%m-%d}" + (f" returning {best['back']:%Y-%m-%d}" if ret else "") + " on Aegean"
    c = st.columns([1, 1, 1])
    c[0].link_button("Check it on Google Flights", "https://www.google.com/travel/flights?q=" + urllib.parse.quote(q), width="stretch",
                     icon=":material/open_in_new:")
    for col, page, label, icon, params in (
            (c[1], "views/alerts.py", "Alert me if it gets cheaper", ":material/notifications:",
             {"route": f"{orig}-{dest}", "cabin": gcab, "unit": "eur" if pay == "cash" else "miles"}),
            (c[2], "views/expert.py", "Ask the expert about it", ":material/forum:", {"from": orig, "to": dest})):
        try:
            col.page_link(page, label=label, icon=icon, query_params=params)
        except Exception:  # the page runs on its own (tests), outside the app's navigation
            pass
    hotels = tp.hotels(db, dest) if ret else []
    if hotels and pay == "cash":
        h = hotels[0]
        nights = night_prices(db, h["query"], h["filt"], h["adults"], best["depart"], best["back"])
        stay = [(best["depart"] + timedelta(i)).isoformat() for i in range(best["nights"])]
        if all(s in nights.index for s in stay):
            cost = sum(nights[s] for s in stay)
            st.caption(f"With {h['name']} ({h['adults']} adults) for those {best['nights']} nights: €{cost:,.0f}, "
                       f"so €{best['price'] + cost:,.0f} for the whole trip.")

# --- by month -------------------------------------------------------------------------------------------------------------------------
m = deals.by_month(out_s)
if len(m) > 1:
    st.markdown(f'<p class="group">Cheapest one-way {e(orig)} → {e(dest)} by month</p>', unsafe_allow_html=True)
    st.markdown('<div class="months">' + "".join(
        f'<div class="{"low" if v == m.min() else ""}"><div class="m">{e(k)}</div><div class="v">{e(money(v))}</div></div>'
        for k, v in m.items()) + "</div>", unsafe_allow_html=True)

# --- the dates behind it --------------------------------------------------------------------------------------------------------------
if back_s is not None and len(back_s.dropna()):
    st.markdown('<p class="group">Pick your dates</p>', unsafe_allow_html=True)
    periods = tp.months(today, lastday)
    at = next((i for i, (_, a, b) in enumerate(periods) if i and best and a <= best["depart"] <= b), 0)  # open on the pass's month
    c = st.columns([1.1, 2.6, 2.3])
    when = c[0].selectbox("Leaving", range(len(periods)), index=at, format_func=lambda i: periods[i][0], key=f"ov-grid-when-{fkey}")
    c[1].pills("Leave on", DAYS, selection_mode="multi", default=DAYS, key=f"ov-grid-days-{fkey}")
    c[2].pills("Nights", NIGHTS, selection_mode="multi", default=NIGHTS, key=f"ov-grid-nights-{fkey}")
    _, a, b = periods[when]
    grid = deals.trip_grid(out_s, back_s, a, b, stays, wdays)
    if len(grid):
        st.dataframe(deals.styled(grid, ("€{:,.0f}" if pay == "cash" else "{:,.0f}").format, deals.grid_colours), width="stretch", height=min(36 * len(grid) + 38, 560))
        same = grid.min().min() == grid.max().max()
        st.caption(f"Return {tp.city(orig)} → {tp.city(dest)} → {tp.city(orig)}{' in miles' if pay == 'miles' else ''}, by the day you "
                   "leave and the nights you stay. "
                   + ("Every trip shown costs the same; green marks the dates with seats both ways." if same else
                      "Darkest green is the cheapest; light green is within 10% of it."))
    else:
        st.caption("No return trips saved for those days" + (" and nights" if len(wdays) < 7 or len(stays) < 6 else "") + ".")
elif best is not None:
    st.caption(f"Only {orig} → {dest} is saved, so return trips can't be priced. Search {dest} → {orig} on the Flights page to add them.")

# --- one tip, only when the data shows a real difference ------------------------------------------------------------------------------
if pay == "cash":
    w = weekday_stats(out_s)
    if len(w) >= 5 and w["mean"].max() - w["mean"].min() >= 25:
        st.markdown('<p class="group">Tip</p>', unsafe_allow_html=True)
        st.markdown(f"Leaving {e(tp.city(orig))} on a **{FULL[w['mean'].idxmin()]}** is cheapest: €{w['mean'].min():,.0f} on average, "
                    f"€{w['mean'].max() - w['mean'].min():,.0f} less than a {FULL[w['mean'].idxmax()]}.")

# --- every route ----------------------------------------------------------------------------------------------------------------------
st.markdown('<p class="group">Cheapest one-way on every route, next 90 days</p>', unsafe_allow_html=True)
cell = lambda v: f"€{v['min']:,.0f} · {pd.Timestamp(v['day']):%a %d %b}" if v else "—"
st.dataframe(pd.DataFrame([{"Route": f"{o} → {d}", "Economy": cell(s.get("economy")), "Business": cell(s.get("business"))}
                           for (o, d), s in summary.items()]), hide_index=True, width="stretch")
