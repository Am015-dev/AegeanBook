import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import gh
from collector import fetch_range, gaps
from db import RETENTION_DAYS, connect, save_awards, saved_flight_days
from advice import STYLE as ADV_STYLE, card_html as adv_card, options as adv_options
from best_days import best_miles_days, best_miles_pairs, cheapest_days, cheapest_pairs
from award_calendar import STYLE as CAL_STYLE, calendar_html, cells as cal_cells
from award_view import best_award, load_award, miles_table, one_way_plan, return_plan
from jobs import record, start_job, track
from pass_plan import DEFAULT_FLIGHTS, DEFAULT_TAXES, PASSES, assess as assess_pass
from trip_cost import hotel_options, night_prices, trip_costs
from ui import (BUSINESS, ECONOMY, MUTED, airport_picker, best_days, best_trips, excel_bytes, kpis, near_cheapest, on_weekdays,
                price_chart, round_trips, route, schedule_note, scraped, smart_filters, window_picker)

db = connect()
today = date.today()
HORIZON = 330  # Google's calendar reaches about 11 months ahead; later days would look like "no flight"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AWARD_PROFILE = os.path.join(ROOT, ".aegean_profile")  # exists only after the one-time `python award_collector.py login`
AWARD_LEGS = [("ATH", "FRA"), ("FRA", "ATH"), ("ATH", "ZRH"), ("ZRH", "ATH")]  # the legs collect_daily.py also tracks
AWARD_DAYS = 90


@st.cache_data(ttl=3600, show_spinner=False)
def award_history_xlsx(path, mtime):
    """(xlsx bytes, snapshot count, last snapshot day) of the whole history, built once per file version instead of on every rerun."""
    h = pd.read_csv(path)
    if h.empty:
        return None
    h = h.rename(columns={"fetched": "collected", "miles": "award miles"}).sort_values(["collected", "orig", "dest", "day", "cabin"])
    return excel_bytes(h.set_index("collected")), h.collected.str[:10].nunique(), h.collected.max()[:10]


def award_job():
    """Button: run award_collector.py on this computer for the four legs. Its `DAY <iso> ok|error` lines drive the progress bar."""
    key = "awards|all"
    if st.button("Collect award miles now (90 days, 4 routes, about an hour: it paces itself like a person)", key="aw-go"):
        def work(j):
            pr = subprocess.Popen([sys.executable, "award_collector.py", "fetch", *[f"{o}-{d}" for o, d in AWARD_LEGS], "--days", str(AWARD_DAYS)],
                                  cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            last = ""
            for line in pr.stdout:
                last = line.strip() or last
                if line.startswith("DAY "):
                    record(j, "ok" if line.split()[-1] == "ok" else "error", "A day did not load; the next run retries it")
                if j["cancel"]:
                    pr.terminate()
                    return
            if pr.wait():
                j["error"] = last[-300:]
        start_job(key, len(AWARD_LEGS) * AWARD_DAYS, work, "{n} days.", "checked", "Award miles")
    track(key)

def swap_airports():
    """Runs before the page is drawn again, so both boxes show the swapped airports at once."""
    st.session_state["orig"], st.session_state["dest"] = st.session_state["dest"], st.session_state["orig"]


with st.container(border=True, key="search"):
    c = st.columns([10, 1, 10], vertical_alignment="bottom")  # the swap button sits between the two boxes, level with them
    orig = airport_picker(c[0], "From", "ATH", "orig")
    c[1].button("", icon=":material/swap_horiz:", key="swap-airports", on_click=swap_airports, help="Swap From and To")
    dest = airport_picker(c[2], "To", "FRA", "dest")
    c = st.columns([1, 2])
    trip = c[0].pills("Trip", ["One-way", "Return"], default="One-way", required=True, key="fl-trip")
    nights = c[1].pills("Nights away", [2, 3, 4, 5, 7, 10, 14], selection_mode="multi", default=[7], key="fl-nights",
                        help="Pick one or more stay lengths; every combination is compared.") if trip == "Return" else []
    start, end = window_picker(st, "fl", today, HORIZON)
    c = st.columns(2)
    fetch = c[0].button("Update prices", type="primary", width="stretch")
    with c[1].popover("Options", width="stretch"):
        nonstop = st.checkbox("Nonstop flights only", value=True)
        recheck = st.checkbox("Re-check days already saved")
        st.caption(f"1 passenger, AEGEAN flights only, from Google's price calendar. A return is two one-way fares. Busy routes take seconds, quiet "
                   f"routes a few minutes. Results are kept {RETENTION_DAYS} days and reused.")

if orig == dest:
    st.error("From and To are the same airport.")
    st.stop()
if trip == "Return" and not nights:
    st.warning("Pick at least one stay length under Nights away.")
    st.stop()
# return flights leave between the first departure + shortest stay and the last departure + longest stay
rstart, rend = (start + timedelta(min(nights)), end + timedelta(max(nights))) if nights else (None, None)
if rend and rend > today + timedelta(HORIZON):
    st.error(f"The return dates would go past {today + timedelta(HORIZON):%d %b %Y}, as far as Google's calendar reaches. "
             "Choose an earlier start, a shorter window or fewer nights.")
    st.stop()

stay = f"return, {', '.join(map(str, sorted(nights)))} nights" if nights else "one-way"
route(orig, dest, f"{start:%a %d %b} – {end:%a %d %b %Y} · {(end - start).days + 1} days · {stay} · AEGEAN")

# both directions, always: Bilbao -> Athens also fetches Athens -> Bilbao (the way back of a return, or the same days for a one-way)
legs = [(orig, dest, start, end), (dest, orig, rstart, rend) if nights else (dest, orig, start, end)]
key = f"flights|{orig}|{dest}|{start}|{end}|{nonstop}|{rend}"
if fetch:
    skips = [set() if recheck else saved_flight_days(db, o, d, nonstop, a, b) for o, d, a, b in legs]
    total = sum(2 * ((b - a).days + 1) for _, _, a, b in legs) - sum(map(len, skips))

    def work(j):
        tdb = connect()
        for (o, d_, a, b), skip in zip(legs, skips):  # outbound, then the way back
            for d, cabin, price, source, status, err in fetch_range(a, b, o, d_, skip=skip, nonstop=nonstop):
                if j["cancel"]:
                    return
                if status != "error":  # failed lookups are not saved, so the next run retries them
                    tdb.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)",  # `airlines` column holds the source
                                (d.isoformat(), o, d_, cabin, price, source, datetime.now().isoformat(), int(nonstop)))
                    tdb.commit()
                record(j, status, err)

    tok = gh.token()
    if total > 0 and tok:  # Google rate-limits Streamlit Cloud's shared server: let GitHub's servers do the searching
        try:
            missing = [(o, d, x, y) for (o, d, a, b), skip in zip(legs, skips) for x, y in gaps(a, b, skip)]  # only what is not saved yet
            st.session_state[f"gh-{key}"] = (gh.fetch(tok, missing, nonstop), time.time())
        except gh.GitHubError as err:
            st.warning(f"{err} Searching from this server instead.", icon=":material/warning:")
            tok = None
    if total > 0 and not tok:
        start_job(key, total, work, "{n} day and cabin combinations have no AEGEAN flight. AEGEAN may not fly this route on those "
                  "days, or only in some seasons. Google occasionally misses a day: tick Re-check days already saved (Options) if you "
                  "expect more.", "with no flight", f"{orig}–{dest} {start:%d %b}–{end:%d %b}" + (" return" if nights else ""))
    else:
        st.info("Every day in this range is already saved. Tick Re-check days already saved under Options to fetch again.")
track(key)


@st.fragment(run_every=8)
def github_fetch(key):
    """While GitHub fetches this search: say so, and reload the page when the results are in."""
    pending = st.session_state.get(f"gh-{key}")
    if not pending:
        return
    request, since = pending
    waited = int(time.time() - since)
    try:
        r = gh.run(gh.token(), request)
    except gh.GitHubError as err:
        st.error(str(err))
        return
    if r and r["status"] == "completed":
        del st.session_state[f"gh-{key}"]
        gh.forget("data/routes.csv")  # each direction saves on its own: what was found is there even if one direction failed
        if r["conclusion"] == "success":
            st.rerun()
        st.session_state[f"gh-note-{key}"] = (f"Part of the search on GitHub did not finish ({r['conclusion']}): what was found is saved, "
                                              f"press Update prices to fetch the rest. Its log: {r['url']}")
        st.rerun()
    elif waited > 1500:
        del st.session_state[f"gh-{key}"]
        st.error("The search on GitHub is taking too long. Press Update prices to try again." + (f" Its log: {r['url']}" if r else ""))
    else:
        st.info(f"Searching on GitHub's servers (Google limits this app's own server): {waited // 60}:{waited % 60:02d} so far, "
                "usually 1–3 minutes. The prices appear here when it is done.", icon=":material/cloud_sync:")


github_fetch(key)
if note := st.session_state.pop(f"gh-note-{key}", None):
    st.warning(note, icon=":material/warning:")

scraped([(f"{o} → {d_}", pd.Series([r[0] for r in db.execute(
    "SELECT MAX(fetched) FROM prices WHERE orig=? AND dest=? AND nonstop=? AND day BETWEEN ? AND ? GROUP BY day",
    (o, d_, int(nonstop), a.isoformat(), b.isoformat()))], dtype=object), (b - a).days + 1) for o, d_, a, b in legs])
q, args = "WHERE orig=? AND dest=? AND day BETWEEN ? AND ?", (orig, dest, start.isoformat(), end.isoformat())
p = pd.read_sql("SELECT * FROM prices WHERE orig=? AND dest=? AND nonstop=? AND day BETWEEN ? AND ? ORDER BY fetched",
                db, params=(orig, dest, int(nonstop), start.isoformat(), end.isoformat()))
seen = p.drop_duplicates(["day", "cabin"], keep="last")  # one row per day and cabin: priced, or checked with no flight
latest = seen.pivot(index="day", columns="cabin", values="price") if len(p) else pd.DataFrame()
days = pd.DataFrame(index=pd.date_range(start, end).strftime("%Y-%m-%d").rename("day"))
for k in ("economy", "business"):
    if k not in latest:
        latest[k] = float("nan")
a = pd.read_sql(f"SELECT day, miles, taxes FROM awards {q}", db, params=args).set_index("day")
out = days.join(latest).join(a)
am, amt, last_aw = load_award(db, orig, dest, nonstop, start, end)  # miles and taxes by day; empty when nothing was collected (see award_view)
out = out.join(am.rename(columns={"economy": "econ_miles", "business": "biz_miles"})).join(
    amt.rename(columns={"economy": "econ_taxes", "business": "biz_taxes"}))
for k in ("econ_miles", "biz_miles", "econ_taxes", "biz_taxes"):
    if k not in out:
        out[k] = float("nan")
# ponytail: collected miles stay in their own columns. Filling `miles` with them would make the 'best miles value' metric use zero
# taxes (collected rows have none) and overstate it, so that metric stays typed-awards-only.
out["value"] = ((out.business - out.taxes.fillna(0)) / out.miles.where(out.miles > 0) * 1000).round(1)


tol, weekdays = smart_filters(st, "fl")
view = on_weekdays(out, weekdays)  # the cards, chips and chart only look at the chosen weekdays (of departure)
near = {c: near_cheapest(view[c], tol) for c in ("economy", "business")}
if nights:  # the way back, and every departure + stay combination
    pb = pd.read_sql("SELECT * FROM prices WHERE orig=? AND dest=? AND nonstop=? AND day BETWEEN ? AND ? ORDER BY fetched",
                     db, params=(dest, orig, int(nonstop), rstart.isoformat(), rend.isoformat()))
    back = pb.drop_duplicates(["day", "cabin"], keep="last").pivot(index="day", columns="cabin", values="price") if len(pb) else pd.DataFrame()
    back = pd.DataFrame(index=pd.date_range(rstart, rend).strftime("%Y-%m-%d").rename("day")).join(back)
    for k in ("economy", "business"):
        if k not in back:
            back[k] = float("nan")
    trips = round_trips(view, back, nights)


def best(col):
    if near[col].size:
        n = len(near[col])
        return f"€{near[col].iloc[0]:.0f}", (f"{pd.Timestamp(near[col].index[0]):%a %d %b}" +
                                             (f" · {n} days within {tol:.0%}" if tol and n > 1 else f" · {n} days at this price" if n > 1 else ""))
    return "—", "No AEGEAN flight found" if (seen.cabin == col).any() else "No data yet"


def best_trip(col):
    t = trips[trips.cabin == col]
    if t.empty:
        return "—", "Press Update prices to fetch the way back" if not len(pb) else "No return pair with flights both ways"
    r = t.iloc[0]
    return f"€{r.total:.0f}", f"{pd.Timestamp(r.depart):%a %d %b} → {pd.Timestamp(r.back):%a %d %b} · {r.nights} nights"


(ev, es), (bv, bs) = (best_trip("economy"), best_trip("business")) if nights else (best("economy"), best("business"))
kind = "return" if nights else ""
collected = bool(am.size)  # any award data for this route and range: no data is not the same as no seats
(eav, eas), (bav, bas) = best_award(view.econ_miles, collected), best_award(view.biz_miles, collected)
cash_col, award_col = st.columns(2, gap="medium")  # side by side on a desktop, stacked on a phone
with cash_col:
    st.markdown('<p class="group">Cash fares</p>', unsafe_allow_html=True)
    kpis([(f"Cheapest {kind} economy".replace("  ", " "), ev, es, ECONOMY), (f"Cheapest {kind} business".replace("  ", " "), bv, bs, BUSINESS)])
with award_col:
    st.markdown(f'<p class="group">Award miles with Miles+Bonus, {"per flight (a return uses two)" if nights else "one way"}</p>', unsafe_allow_html=True)
    award_rows = [("Lowest economy award", eav, eas, ECONOMY), ("Lowest business award", bav, bas, BUSINESS)]
    if view.value.notna().any():  # only when award prices were typed in by hand: they carry the taxes the collected miles lack
        award_rows.append(("Best miles value", f"€{view.value.max():.0f} / 1,000 mi",
                           f"{pd.Timestamp(view.value.idxmax()):%a %d %b} · cash minus taxes per 1,000 miles", MUTED))
    kpis(award_rows)
if nights:
    best_trips(trips[trips.cabin == "economy"], ECONOMY, "Cheap economy returns", tol)
    best_trips(trips[trips.cabin == "business"], BUSINESS, "Cheap business returns", tol)
else:
    best_days(near["economy"], ECONOMY, "Cheap economy days")
    best_days(near["business"], BUSINESS, "Cheap business days")

if len(seen) < 2 * len(days):
    st.caption(f"Checked {len(seen)} of {2 * len(days)} day and cabin combinations: {int(seen.price.notna().sum())} priced, "
               f"{int(seen.price.isna().sum())} with no flight, {2 * len(days) - len(seen)} not checked or failed. "
               "Press Update prices to check the rest.")
if note := schedule_note(seen, orig, dest):
    st.info(note, icon=":material/event_busy:")
st.caption(f"Saved results are kept {RETENTION_DAYS} days. ATH–FRA and ATH–ZRH (both ways, next 90 days) are fetched "
           "automatically every morning.")
t1, t2, t3, t4 = st.tabs(["Prices", "Award miles", "Table", "Trip cost"])
with t1:
    if view[["economy", "business"]].notna().any().any():
        st.markdown(f'<p class="card-title">{orig} → {dest}, cheap days ringed</p>' if nights else
                    '<p class="card-title">Daily AEGEAN fare, cheap days ringed</p>', unsafe_allow_html=True)
        st.plotly_chart(price_chart(view, {"economy": ("Economy", ECONOMY), "business": ("Business", BUSINESS)},
                                    near={c: list(near[c].index) for c in near}),
                        width="stretch", config={"displayModeBar": False})
    else:
        st.info("No AEGEAN fares on these days. Try other weekdays, a longer window, or turn off Nonstop flights only in Options."
                if len(p) else "No fares yet. Pick the route and dates above, then press **Update prices**.")
    if nights and back[["economy", "business"]].notna().any().any():
        bnear = {c: list(near_cheapest(back[c], tol).index) for c in ("economy", "business")}
        st.markdown(f'<p class="card-title">{dest} → {orig} (the way back), cheap days ringed</p>', unsafe_allow_html=True)
        st.plotly_chart(price_chart(back, {"economy": ("Economy", ECONOMY), "business": ("Business", BUSINESS)}, near=bnear),
                        width="stretch", config={"displayModeBar": False})
with t2:
    seen_aw = am.index.size if len(am) else 0
    if not seen_aw or not last_aw:
        st.info(f"No AEGEAN award miles collected yet for {orig} → {dest} in this range. They are collected daily on the owner's computer. "
                "No data here does not mean there are no seats.")
    else:
        st.caption(f"{'Nonstop' if nonstop else 'Any-stops'} AEGEAN-operated flights. {seen_aw} of {len(days)} days collected "
                   f"for {orig} → {dest}, last on {last_aw[:10]}.")
        if date.fromisoformat(last_aw[:10]) < today - timedelta(3):
            st.warning(f"These award miles were last collected on {last_aw[:10]}, so they may be out of date.")
        cal_cabin = st.radio("Cabin", ["economy", "business"], format_func=str.capitalize, horizontal=True, key="cal-cabin")
        cal_cash, cal_miles = ("economy", "econ_miles") if cal_cabin == "economy" else ("business", "biz_miles")
        if out[cal_miles].notna().any():
            st.markdown(CAL_STYLE + calendar_html(cal_cells(out[cal_cash], out[cal_miles], set(am.index), start, end)), unsafe_allow_html=True)
            seat_miles = out[cal_miles].dropna()
            price = (f"{seat_miles.min():,.0f} miles" if seat_miles.min() == seat_miles.max()
                     else f"{seat_miles.min():,.0f} to {seat_miles.max():,.0f} miles")
            st.caption(f"An award seat costs {price}, the same on every day that has one. What changes from day to day is whether a seat exists "
                       "and what cash costs, so the best days to spend miles are the darker green ones: a seat exists and cash is dear.")
            with st.expander("Show as a table"):
                st.dataframe(miles_table(view.econ_miles, view.biz_miles), hide_index=True, width="stretch")
        else:
            st.info("No award seats on these days. Try a longer window.")
    hist = os.path.join(ROOT, "data", "award_history.csv")
    xl = award_history_xlsx(hist, os.path.getmtime(hist)) if os.path.exists(hist) else None
    if xl:  # every daily snapshot, all four legs, as one sheet
        st.download_button("Download award history (Excel, all routes)", xl[0], "aegean_award_history.xlsx",
                           help=f"{xl[1]} daily snapshots, last {xl[2]}")
    if os.path.isdir(AWARD_PROFILE):  # only where the AEGEAN login lives: your own computer
        award_job()
    with st.expander("Enter award miles and taxes by hand"):
        st.caption("Type the miles and taxes you see after logging in to AEGEAN. They feed the 'Best miles value' figure, which compares "
                   "the cash fare, minus these taxes, with the miles.")
        ed = st.data_editor(days.join(a).reset_index(), hide_index=True, disabled=["day"], width="stretch",
                            column_config={"day": "Date", "miles": st.column_config.NumberColumn("Miles", format="%d"),
                                           "taxes": st.column_config.NumberColumn("Taxes €", format="€%.0f")},
                            key=f"{orig}{dest}{start}{end}")
        save_awards(db, ed[["day", "miles", "taxes"]].itertuples(index=False), orig, dest)
with t3:
    typed = out.miles.notna().any()  # the typed-in columns only appear when something was typed
    table = (out if typed else out.drop(columns=["miles", "taxes", "value"])).rename(columns={"economy": "Economy €", "business": "Business €", "miles": "Miles",
                                "taxes": "Taxes €", "value": "€ per 1,000 mi",
                                "econ_miles": "Economy award miles", "biz_miles": "Business award miles"})
    checked = set(zip(seen.day, seen.cabin))
    for cabin in ("economy", "business"):  # say why a price is missing: no flight, or never checked
        table[f"{cabin.capitalize()} status"] = ["priced" if pd.notna(out.loc[d, cabin]) else "no flight" if (d, cabin) in checked
                                                 else "not checked" for d in out.index]
    if nights:
        st.markdown('<p class="card-title">Round trips, cheapest first</p>', unsafe_allow_html=True)
        rt = trips.rename(columns={"cabin": "Cabin", "depart": "Depart", "back": "Return", "nights": "Nights",
                                   "outbound": "Outbound €", "return": "Return €", "total": "Total €"})
        st.dataframe(rt, width="stretch", hide_index=True)
        st.download_button("Download round trips", excel_bytes(rt), f"aegean_{orig}_{dest}_returns.xlsx")
        st.markdown(f'<p class="card-title">{orig} → {dest} by day</p>', unsafe_allow_html=True)
    st.dataframe(table, width="stretch")
    st.download_button("Download Excel", excel_bytes(table), f"aegean_{orig}_{dest}.xlsx")
with t4:
    if not nights:
        st.info("Set Trip to Return at the top to price a whole trip: the return flights plus the hotel for every night of the stay.")
    elif not (saved_hotels := hotel_options(db)):
        st.info("No one-night hotel prices are saved yet. Open Hotels and press Update prices, or wait for the daily run.")
    else:
        pick = st.selectbox("Hotel", range(len(saved_hotels)), key="trip-hotel", help="Only hotels with one-night prices saved are listed.",
                            format_func=lambda i: f"{saved_hotels[i]['filt'] or saved_hotels[i]['query']} · {saved_hotels[i]['days']} nights priced")
        trip_cabin = st.radio("Cabin", ["economy", "business"], format_func=str.capitalize, horizontal=True, key="trip-cabin")
        hotel = saved_hotels[pick]
        tc = trip_costs(trips[trips.cabin == trip_cabin], night_prices(db, hotel["query"], hotel["filt"], hotel["adults"], start, rend))
        if tc.empty:
            st.info("No trip has both flights and a hotel price for every night. Try other stay lengths, or update the hotel prices for these dates.")
        else:
            b = tc.iloc[0]
            st.markdown(f'<p class="card-title">Cheapest {trip_cabin} trip: €{b.total:.0f}</p>', unsafe_allow_html=True)
            st.caption(f"{pd.Timestamp(b.depart):%a %d %b} → {pd.Timestamp(b.back):%a %d %b} · {b.nights} nights · €{b.flights:.0f} flights + "
                       f"€{b.hotel:.0f} hotel")
            shown = tc.rename(columns={"depart": "Depart", "back": "Return", "nights": "Nights", "flights": "Flights €", "hotel": "Hotel €",
                                       "total": "Total €"})
            st.dataframe(shown.head(25), width="stretch", hide_index=True)
            st.download_button("Download all trips", excel_bytes(shown), f"aegean_{orig}_{dest}_trips.xlsx")
        st.caption(f"Hotel prices are for one night, added up night by night for {hotel['adults']} adults. Make sure this hotel is in {dest}.")

# What should I book? One answer per cabin for the trip type chosen at the top (one way or return): cash, miles or an Aegean Pass.
st.markdown('<p class="card-title">What should I book?</p>', unsafe_allow_html=True)
with st.expander("Assumptions: what a mile and each pass are worth"):
    per_k = st.number_input("My miles are worth (€ per 1,000)", min_value=1.0, max_value=50.0, value=10.0, step=0.5, key="mile_value",
                            help="Miles are recommended when they save more than this per 1,000, cash otherwise. Set it to what you would "
                                 "otherwise get for your miles.")
    pcols = st.columns(4)
    pass_prices = [pcols[i].number_input(f"{name} (€)", min_value=0.0, value=default, step=10.0, key=f"pass-price-{days}")
                   for i, (name, default, days) in enumerate(PASSES)]
    pass_flights = pcols[2].number_input("Flights in each pass", min_value=1, max_value=40, value=DEFAULT_FLIGHTS, key="pass-flights",
                                         help="The price per flight depends on how many flights the pass has. Check yours.")
    pass_taxes = pcols[3].number_input("Taxes per flight (€)", min_value=0.0, max_value=300.0, value=DEFAULT_TAXES, step=5.0, key="pass-taxes",
                                       help="Airport taxes and surcharges are paid on top of the pass, flight by flight. €30 is what AEGEAN's award "
                                            "response showed for a nonstop Athens to Frankfurt flight: use your own route's.")
    st.caption("A pass is only compared with trips you could book that many days ahead. Current prices: "
               "https://en.aegeanair.com/flight-deals/aegean-pass/")
empty = pd.Series(dtype=float)
bam, bamt, _ = load_award(db, dest, orig, nonstop, rstart, rend) if nights else (pd.DataFrame(), pd.DataFrame(), None)  # miles on the way back
day = lambda iso: f"{pd.Timestamp(iso):%a %d %b}"
cards = []
for name, colour, cash_col, miles_col, taxes_col in (("Economy", ECONOMY, "economy", "econ_miles", "econ_taxes"),
                                                     ("Business", BUSINESS, "business", "biz_miles", "biz_taxes")):
    ow = one_way_plan(view, cash_col, miles_col, taxes_col)
    ret = return_plan(trips[trips.cabin == cash_col], view[miles_col], bam.get(cash_col, empty), nights,
                      view[taxes_col], bamt.get(cash_col, empty)) if nights else None
    # the advice names a shortlist of days across the whole range, never one arbitrary day: the cheapest few for cash, and for miles (which cost
    # the same on every seat day) the days where a seat replaces the most cash
    if nights:  # a return trip: round-trip totals and the pairs of days that have miles both ways
        trips_cabin = trips[trips.cabin == cash_col]
        tc_ = trips_cabin.total
        cash_in = dict(min=float(tc_.min()), typical=float(tc_.median()), when=cheapest_pairs(trips_cabin, tol)) if len(tc_) else None
        mm = ret.get("miles")
        cash_of = {(t.depart, t.back): float(t.total) for t in trips_cabin.itertuples()}
        miles_in = dict(miles=mm[3], taxes=mm[5], when=best_miles_pairs(view[miles_col], bam.get(cash_col, empty), nights, cash_of, view[taxes_col],
                                                                       bamt.get(cash_col, empty))) if mm else None
    else:
        c_ = view[cash_col].dropna()
        cash_in = dict(min=float(c_.min()), typical=float(c_.median()), when=cheapest_days(view[cash_col], tol)) if len(c_) else None
        mm = ow.get("miles")
        miles_in = dict(miles=mm[1], taxes=mm[3], when=best_miles_days(view[cash_col], view[miles_col], view[taxes_col])) if mm else None
    pass_in, notes = [], []
    if cash_col == "economy":  # each pass against the dates shown above that could be booked that far ahead
        for (pname, _, days_ahead), price in zip(PASSES, pass_prices):
            pa = assess_pass(out["economy"], price, pass_flights, pass_taxes, days_ahead, today) if price else None
            shown = int(out["economy"].notna().sum())
            if pa and pa["days"] * 2 >= shown:  # most of the dates shown can be booked that far ahead
                pass_in.append(dict(name=pname, eff=pa["effective"], days=days_ahead, price=price, flights=pass_flights))
            elif price:
                notes.append(f"The {pname} is left out: only {pa['days'] if pa else 0} of the {shown} dates shown can be booked {days_ahead} or more "
                             "days ahead. Choose a longer range at the top to include it.")
    else:
        notes.append("The Aegean Pass is an Economy Flex fare, so it is not compared for Business.")
    cards.append((name, colour, adv_options(cash_in, miles_in, pass_in, per_k, 2 if nights else 1), notes))
st.markdown(ADV_STYLE, unsafe_allow_html=True)
for ui_col, (name, colour, opts, notes) in zip(st.columns(2, gap="medium"), cards):
    with ui_col:
        st.markdown(adv_card(name, colour, opts, "round trip" if nights else "flight", notes), unsafe_allow_html=True)
st.caption("Cash fares come from Google Flights and award miles from AEGEAN. "
           + ("Showing round trips for the stay chosen at the top. " if nights else "Showing one-way flights; set Trip to Return at the top for round trips. ")
           + f"Based on the {len(out)} days chosen at the top: choose 90 days there to consider everything saved.")
