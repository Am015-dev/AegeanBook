import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import booking
import gh
import skyexpress as sx
from collector import Airline, fetch_range, gaps
from db import RETENTION_DAYS, connect, save_awards, saved_flight_days
from advice import STYLE as ADV_STYLE, card_html as adv_card, options as adv_options
from best_days import best_miles_days, best_miles_pairs, cheapest_days, cheapest_pairs
from award_calendar import STYLE as CAL_STYLE, calendar_html, cells as cal_cells
from award_view import best_award, load_award, miles_table, one_way_plan, return_plan
from jobs import record, start_job, track
from pass_plan import DEFAULT_FLIGHTS, DEFAULT_TAXES, PASSES, assess as assess_pass
from trip_cost import hotel_options, night_prices, trip_costs
from plain import checked_sentence, nearly, options_filters
from layout import page_head
from ui import (BUSINESS, ECONOMY, MUTED, airport_picker, best_days, best_trips, excel_bytes, kpis, near_cheapest, on_weekdays,
                price_chart, round_trips, route, schedule_note, window_picker, github_progress, github_note)

if getattr(sx, "VERSION", 0) < 2:  # Streamlit Cloud keeps an older copy of a module across redeploys
    import importlib
    sx = importlib.reload(sx)
db = connect()
today = date.today()
STYLE = """<style>
.st-key-search [data-testid="stExpander"] { box-shadow:none; background:#F5F5F7; border-radius:10px; }
.st-key-search [data-testid="stExpander"] summary { padding:10px 14px; }
</style>"""
st.markdown(STYLE, unsafe_allow_html=True)
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


page_head("Flights", "Every day's fare on one route, both ways, in cash or award miles, with the Aegean Pass and the Excel file.")
with st.container(border=True, key="search"):
    c = st.columns([10, 1, 10], vertical_alignment="bottom")  # the swap button sits between the two boxes, level with them
    orig = airport_picker(c[0], "From", "ATH", "orig")
    c[1].button("", icon=":material/swap_horiz:", key="swap-airports", on_click=swap_airports, help="Swap From and To")
    dest = airport_picker(c[2], "To", "FRA", "dest")
    c = st.columns([1, 2, 1])
    trip = c[0].pills("Trip", ["One-way", "Return"], default="One-way", required=True, key="fl-trip",
                      format_func={"One-way": "One way", "Return": "Return"}.get)
    nights = []
    if trip == "Return":  # the Overview's control: any stay from 1 to 60 nights, every length in the range is compared
        lo, hi = c[1].slider("Nights", 1, 60, (2, 7), key="fl-nights", help="How long to stay, shortest to longest you would accept.")
        nights = list(range(lo, hi + 1))
    pay = c[2].pills("Pay with", ["cash", "miles"], default="cash", format_func={"cash": "Cash €", "miles": "Miles"}.get, required=True,
                     key="fl-pay", help="Miles adds the award seats collected for this route (Miles+Bonus) beside the cash fares.")
    start, end = window_picker(st, "fl", today, HORIZON)
    # Sky Express beside AEGEAN, economy only, where both airports are in its network (the toggle's state is kept per route): in the card
    # once its fares are saved for the route, under Options before that
    gq_route = sx.serves(orig, dest)
    gq_saved = gq_route and db.execute(f"SELECT 1 FROM {sx.TABLE} WHERE orig=? AND dest=? AND price IS NOT NULL LIMIT 1", (orig, dest)).fetchone()
    gq_help = "Sky Express's economy fares for the same days as a dashed line; Update prices fetches them too."
    fetch = st.button("Update prices", type="primary", width="stretch",
                      help="Fetches the fares for every day shown, both ways, and saves them. Days already saved are skipped.")
    gq = False
    if gq_saved:
        gq = st.toggle("Compare with Sky Express", key=f"fl-gq-{orig}{dest}", help=gq_help)
    elif gq_route:
        st.caption("Sky Express fares can be fetched too: Options → Compare with Sky Express")
    with st.expander("Options"):
        c = st.columns(2)
        nonstop = c[0].checkbox("Nonstop only", value=True, help="Direct flights only. Turn it off to include flights with a stop.")
        recheck = c[1].checkbox("Re-check days already saved", help="Fetch again the days that already have a saved price.")
        if gq_route and not gq_saved:
            gq = st.toggle("Compare with Sky Express", key=f"fl-gq-{orig}{dest}", help=gq_help)
        tol, weekdays = options_filters(st, "fl")
        st.caption(f"Fares are for one adult on AEGEAN, from Google Flights; a return is two one-way fares. Saved fares are kept "
                   f"{RETENTION_DAYS} days. ATH–FRA and ATH–ZRH (both ways, the next 90 days) are refreshed every morning.")

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

stay = (f"return, {min(nights)}–{max(nights)} nights" if len(nights) > 1 else f"return, {nights[0]} nights") if nights else "one way"
route(orig, dest, f"{start:%a %d %b} – {end:%a %d %b %Y} · {(end - start).days + 1} days · {stay} · AEGEAN")

# both directions, always: Bilbao -> Athens also fetches Athens -> Bilbao (the way back of a return, or the same days for a one-way)
legs = [(orig, dest, start, end), (dest, orig, rstart, rend) if nights else (dest, orig, start, end)]
key = f"flights|{orig}|{dest}|{start}|{end}|{nonstop}|{rend}"
if fetch:
    skips = [set() if recheck else saved_flight_days(db, o, d, nonstop, a, b) for o, d, a, b in legs]
    total = sum(2 * ((b - a).days + 1) for _, _, a, b in legs) - sum(map(len, skips))
    # Sky Express, economy only, the same days (nonstop: its fares are kept nonstop-only): (iso day, "economy") pairs already saved
    gq_skips = [{(s, "economy") for s in sx.saved_days(db, o, d, a, b)} if gq and nonstop and not recheck else set() for o, d, a, b in legs]
    gq_total = sum((b - a).days + 1 for _, _, a, b in legs) - sum(map(len, gq_skips)) if gq and nonstop else 0

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
        for (o, d_, a, b), skip in zip(legs, gq_skips) if gq_total else []:  # then Sky Express, into its own table
            for d, cabin, price, source, status, err in fetch_range(a, b, o, d_, cabins=("economy",), skip=skip, nonstop=True, airline=Airline.GQ):
                if j["cancel"]:
                    return
                if status != "error":
                    tdb.execute(f"INSERT INTO {sx.TABLE} VALUES(?,?,?,?,?,?,?,?)",
                                (d.isoformat(), o, d_, cabin, price, source, datetime.now().isoformat(), 1))
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
    if gq_total > 0 and tok:  # Sky Express as its own run: the workflow fetches one airline per dispatch, one job per leg
        try:
            missing = [(o, d, x, y) for (o, d, a, b), skip in zip(legs, gq_skips) for x, y in gaps(a, b, skip, ("economy",))]
            st.session_state[f"gh-{key}|GQ"] = (gh.fetch(tok, missing, True, airline="GQ"), time.time())
        except gh.GitHubError as err:
            st.warning(f"{err} Sky Express's fares were not fetched.", icon=":material/warning:")
    if total + gq_total > 0 and not tok:
        start_job(key, total + gq_total, work, "{n} day and cabin combinations have no " + ("AEGEAN or Sky Express" if gq_total else "AEGEAN")
                  + " flight. The airline may not fly this route on those days, or only in some seasons. Google occasionally misses a day: "
                  "tick Re-check days already saved (Options) if you expect more.", "with no flight",
                  f"{orig}–{dest} {start:%d %b}–{end:%d %b}" + (" return" if nights else ""))
    elif total + gq_total <= 0:
        st.info("Every day in this range is already saved. Tick Re-check days already saved under Options to fetch again.")
track(key)

github_progress(key, "fetch-route.yml", "data/routes.csv")
github_note(key)
if gq:
    github_progress(f"{key}|GQ", "fetch-route.yml", "data/skyexpress.csv", "Sky Express fares")
    github_note(f"{key}|GQ")

checked = checked_sentence([(f"{o} → {d_}", pd.Series([r[0] for r in db.execute(
    "SELECT MAX(fetched) FROM prices WHERE orig=? AND dest=? AND nonstop=? AND day BETWEEN ? AND ? GROUP BY day",
    (o, d_, int(nonstop), a.isoformat(), b.isoformat()))], dtype=object), (b - a).days + 1) for o, d_, a, b in legs], both_ways=True)
if checked:
    st.caption(checked)
q, args = "WHERE orig=? AND dest=? AND day BETWEEN ? AND ?", (orig, dest, start.isoformat(), end.isoformat())
p = pd.read_sql("SELECT * FROM prices WHERE orig=? AND dest=? AND nonstop=? AND day BETWEEN ? AND ? ORDER BY fetched",
                db, params=(orig, dest, int(nonstop), start.isoformat(), end.isoformat()))
seen = p.drop_duplicates(["day", "cabin"], keep="last")  # one row per day and cabin: priced, or checked with no flight
if not len(p) and not st.session_state.get(f"gh-{key}"):  # nothing saved: one sentence, and Update prices above is the one button
    st.info(f"No prices saved yet for {orig} → {dest} between {start:%a %d %b} and {end:%a %d %b}. Press Update prices to fetch them, "
            "both ways.", icon=":material/flight_takeoff:")
    st.stop()
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
extra = None  # Sky Express economy, a dashed line on the charts when the toggle is on
if gq:
    out = out.join(sx.cash_by_day(db, orig, dest, start, end).rename("skyexpress"))
    extra = {"skyexpress": (f"{sx.NAME} economy", sx.COLOUR)}
# ponytail: collected miles stay in their own columns. Filling `miles` with them would make the 'best miles value' metric use zero
# taxes (collected rows have none) and overstate it, so that metric stays typed-awards-only.
out["value"] = ((out.business - out.taxes.fillna(0)) / out.miles.where(out.miles > 0) * 1000).round(1)


view = on_weekdays(out, weekdays)  # the cards, chips and chart only look at the chosen weekdays (of departure)
cabins = ("economy", "business") + (("skyexpress",) if gq and "skyexpress" in view else ())  # Sky Express as a third line of the cards
near = {c: near_cheapest(view[c], tol) for c in cabins}
if nights:  # the way back, and every departure + stay combination
    pb = pd.read_sql("SELECT * FROM prices WHERE orig=? AND dest=? AND nonstop=? AND day BETWEEN ? AND ? ORDER BY fetched",
                     db, params=(dest, orig, int(nonstop), rstart.isoformat(), rend.isoformat()))
    back = pb.drop_duplicates(["day", "cabin"], keep="last").pivot(index="day", columns="cabin", values="price") if len(pb) else pd.DataFrame()
    back = pd.DataFrame(index=pd.date_range(rstart, rend).strftime("%Y-%m-%d").rename("day")).join(back)
    for k in ("economy", "business"):
        if k not in back:
            back[k] = float("nan")
    if gq:
        back = back.join(sx.cash_by_day(db, dest, orig, rstart, rend).rename("skyexpress"))
    trips = round_trips(view, back, nights, tuple(c for c in cabins if c in back))


def best(col):
    if near[col].size:
        n = len(near[col])
        return f"€{near[col].iloc[0]:.0f}", f"{pd.Timestamp(near[col].index[0]):%a %d %b}{nearly(n, tol)}"
    if col == "skyexpress":
        return "—", "No Sky Express fare saved for these days yet"
    return "—", "No AEGEAN flight found" if (seen.cabin == col).any() else "No data yet"


def best_trip(col):
    t = trips[trips.cabin == col]
    if t.empty:
        return "—", "Press Update prices to fetch the way back" if not len(pb) else "No pair of days with a flight both ways"
    r = t.iloc[0]
    return f"€{r.total:.0f}", f"{pd.Timestamp(r.depart):%a %d %b} → {pd.Timestamp(r.back):%a %d %b} · {r.nights} nights"


(ev, es), (bv, bs) = (best_trip("economy"), best_trip("business")) if nights else (best("economy"), best("business"))
kind = "return" if nights else ""
collected = bool(am.size)  # any award data for this route and range: no data is not the same as no seats
(eav, eas), (bav, bas) = best_award(view.econ_miles, collected), best_award(view.biz_miles, collected)
award_col = None  # the award miles only when asked for (Pay with: Miles at the top), beside the cash fares on a desktop, under them on a phone
if pay == "miles":
    cash_col, award_col = st.columns(2, gap="medium")
else:
    cash_col = st.container()
with cash_col:
    st.markdown('<p class="group">Cash fares</p>', unsafe_allow_html=True)
    rows = [(f"Cheapest {kind} economy".replace("  ", " "), ev, es, ECONOMY), (f"Cheapest {kind} business".replace("  ", " "), bv, bs, BUSINESS)]
    if "skyexpress" in cabins:  # the same line for Sky Express, so the three read side by side
        gv, gs = best_trip("skyexpress") if nights else best("skyexpress")
        rows.append((f"Cheapest {kind} Sky Express (economy)".replace("  ", " "), gv, gs, sx.COLOUR))
        a3 = min((x for x in (ev, bv) if x != "—"), default="—", key=lambda v: float(v[1:].replace(",", "")))
        if gv != "—" and a3 != "—":
            saving = float(a3[1:].replace(",", "")) - float(gv[1:].replace(",", ""))
            rows.append(("Cheapest of the two" if saving else "Same price", f"{'Sky Express' if saving > 0 else 'AEGEAN'}" if saving else "—",
                         f"€{abs(saving):,.0f} less than {'AEGEAN' if saving > 0 else 'Sky Express'}" if saving else "AEGEAN and Sky Express cost the same", MUTED))
    kpis(rows)
if award_col:
    with award_col:
        st.markdown(f'<p class="group">Award miles with Miles+Bonus, {"per flight (a return uses two)" if nights else "one way"}</p>', unsafe_allow_html=True)
        st.caption("Miles+Bonus award seats, collected on the owner's computer; taxes not included.")
        award_rows = [("Lowest economy award", eav, eas, ECONOMY), ("Lowest business award", bav, bas, BUSINESS)]
        if view.value.notna().any():  # only when award prices were typed in by hand: they carry the taxes the collected miles lack
            award_rows.append(("Best miles value", f"€{view.value.max():.0f} / 1,000 mi",
                               f"{pd.Timestamp(view.value.idxmax()):%a %d %b} · cash minus taxes per 1,000 miles", MUTED))
        kpis(award_rows)
pick = trips[trips.cabin == "economy"] if nights else near["economy"]  # the cheapest economy fare above, ready to book
if len(pick):
    dep, ret = (date.fromisoformat(pick.iloc[0].depart), date.fromisoformat(pick.iloc[0].back)) if nights else (date.fromisoformat(pick.index[0]), None)
    st.link_button("Book on aegeanair.com", booking.aegean_url(orig, dest, dep, ret), type="primary", icon=":material/airplane_ticket:",
                   help=f"AEGEAN's own search, filled in for {dep:%a %d %b}" + (f" to {ret:%a %d %b}" if ret else "") + ", one adult.")
if nights:
    best_trips(trips[trips.cabin == "economy"], ECONOMY, "The cheapest economy returns are", tol)
    best_trips(trips[trips.cabin == "business"], BUSINESS, "The cheapest business returns are", tol)
    if "skyexpress" in cabins:
        best_trips(trips[trips.cabin == "skyexpress"], sx.COLOUR, "The cheapest Sky Express returns are", tol)
else:
    best_days(near["economy"], ECONOMY, "Economy is cheapest on")
    best_days(near["business"], BUSINESS, "Business is cheapest on")
    if "skyexpress" in cabins:
        best_days(near["skyexpress"], sx.COLOUR, "Sky Express is cheapest on")

days_checked = seen.day.nunique()
if days_checked < len(days):
    st.caption(f"Prices are saved for {days_checked} of the {len(days)} days; press Update prices for the other {len(days) - days_checked}.")
if note := schedule_note(seen, orig, dest, nonstop):
    st.info(note, icon=":material/event_busy:")
# only the tabs that apply: the award seats when paying with miles, the trip cost for a return once a hotel has prices saved
saved_hotels = hotel_options(db) if nights else []
names = ["Price chart"] + (["Award seats"] if pay == "miles" else []) + ["Day by day"] + (["Trip cost"] if saved_hotels else [])
tabs = dict(zip(names, st.tabs(names)))
with tabs["Price chart"]:
    if view[["economy", "business"]].notna().any().any():
        st.markdown(f'<p class="card-title">{orig} → {dest}: the fare each day, the cheapest days are circled</p>' if nights else
                    '<p class="card-title">The AEGEAN fare each day; the cheapest days are circled</p>', unsafe_allow_html=True)
        st.plotly_chart(price_chart(view, {"economy": ("Economy", ECONOMY), "business": ("Business", BUSINESS)},
                                    near={c: list(near[c].index) for c in near}, extra=extra),
                        width="stretch", config={"displayModeBar": False})
        if gq and "skyexpress" in view and view.skyexpress.notna().any():
            i = view.skyexpress.idxmin()
            st.caption(f"The dashed line is Sky Express economy: cheapest €{view.skyexpress.min():,.0f} on {pd.Timestamp(i):%a %d %b}, "
                       f"with a fare on {int(view.skyexpress.notna().sum())} days.")
        elif gq:
            st.caption("No Sky Express fares saved for these days yet: Update prices fetches them with AEGEAN's.")
    else:
        st.info("No AEGEAN fares on these days. Try other weekdays, a longer window, or turn off Nonstop only under Options.")
    if nights and back[["economy", "business"]].notna().any().any():
        bnear = {c: list(near_cheapest(back[c], tol).index) for c in ("economy", "business")}
        st.markdown(f'<p class="card-title">{dest} → {orig}, the way back: the fare each day, the cheapest days are circled</p>', unsafe_allow_html=True)
        st.plotly_chart(price_chart(back, {"economy": ("Economy", ECONOMY), "business": ("Business", BUSINESS)}, near=bnear, extra=extra),
                        width="stretch", config={"displayModeBar": False})
if pay == "miles":
    with tabs["Award seats"]:
        seen_aw = am.index.size if len(am) else 0
        if not seen_aw or not last_aw:
            st.info(f"No award seats collected yet for {orig} → {dest} on these days. No data here does not mean there are no seats.")
        else:
            old = ", so they may be out of date" if date.fromisoformat(last_aw[:10]) < today - timedelta(3) else ""
            st.caption(f"Award seats checked on {seen_aw} of the {len(days)} days for {orig} → {dest}, last on "
                       f"{pd.Timestamp(last_aw[:10]):%a %d %b}{old}. {'Nonstop' if nonstop else 'Any'} AEGEAN flights.")
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
                               help=f"The award seats seen on each of {xl[1]} days of collecting, the last on {xl[2]}.")
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
with tabs["Day by day"]:
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
if saved_hotels:
    with tabs["Trip cost"]:
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
