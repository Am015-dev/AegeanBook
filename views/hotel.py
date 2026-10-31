import time
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import gh
from collector import gaps
from db import RETENTION_DAYS, connect, cutoff, saved_hotel_days
from hotels import fetch_range
from jobs import record, start_job, track
from par import parallel
from plain import checked_sentence, nearly, options_filters
from ui import (BOOKING, ECONOMY, TIER_COLORS, best_days, excel_bytes, heat_strip, kpis, near_cheapest, on_weekdays,
                price_chart, route, window_picker, github_progress, github_note)
from xotelo import booking_rate, heatmap_range, hotel_key

db = connect()
today = date.today()
STYLE = """<style>
.st-key-search [data-testid="stExpander"] { box-shadow:none; background:#F5F5F7; border-radius:10px; }
.st-key-search [data-testid="stExpander"] summary { padding:10px 14px; }
</style>"""
st.markdown(STYLE, unsafe_allow_html=True)
HORIZON = 330  # Google Hotels prices about a year ahead

with st.container(border=True, key="search"):
    c = st.columns([3, 2, 1, 1])
    query = c[0].text_input("Hotel", "Steigenberger Frankfurt", help="The hotel and its city, as you would type them into Google Hotels.")
    filt = c[1].text_input("Hotel name contains", "Frankfurter Hof", help="Picks your hotel out of the search results: part of its name is enough.")
    nights = c[2].number_input("Nights", 1, 14, 1, help="How many nights each stay lasts.")
    adults = c[3].number_input("Adults", 1, 6, 2, help="How many adults share the room.")
    start, end = window_picker(st, "ho", today, HORIZON, "Check-in between")
    fetch = st.button("Update prices", type="primary", width="stretch",
                      help="Fetches the price of a stay starting on every check-in day shown, and saves them. Days already saved are skipped.")
    with st.expander("Options"):
        link = st.text_input("TripAdvisor link or key", "g187337-d202275",
                             help="Open the hotel on TripAdvisor and paste its address, or just the part like g187337-d202275. "
                                  "It adds the cheap-day calendar and Booking.com prices, and keeps prices coming when Google "
                                  "Hotels is unavailable. Leave empty for Google prices only.")
        c = st.columns(2)
        booking = c[0].checkbox("Booking.com prices for the cheap days", value=True,
                                help="Also fetch Booking.com's own price on the days TripAdvisor marks cheap.")
        recheck = c[1].checkbox("Re-check days already saved", help="Fetch again the days that already have a saved price.")
        tol, weekdays = options_filters(st, "ho", unit="night")
        st.caption(f"Google Hotels' lowest price across the sites it compares, plus Booking.com's own. Saved prices are kept "
                   f"{RETENTION_DAYS} days.")

MAX_DAYS = 186
hkey = hotel_key(link)
if not filt.strip():
    st.info("Type part of the hotel's name under Hotel name contains, for example Frankfurter Hof; without it any hotel in the search could match.")
    st.stop()
if link.strip() and not hkey:
    st.info("That TripAdvisor link has no hotel key: it should contain something like g187337-d202275. Clear the field under Options for Google prices only.")
    st.stop()
if end < start:
    st.error("The last check-in is before the first check-in. Swap the two dates.")
    st.stop()
if start < today or end > today + timedelta(HORIZON):
    st.error(f"Choose check-in dates between today and {today + timedelta(HORIZON):%d %b %Y}.")
    st.stop()
if (end - start).days >= MAX_DAYS:
    st.error(f"Choose a range of at most {MAX_DAYS} days.")
    st.stop()

route(filt.title(), None, f"{query} · check-in {start:%a %d %b} – {end:%a %d %b %Y} · {nights} night{'s' * (nights > 1)} · "
                         f"{adults} adult{'s' * (adults > 1)}")

key = f"hotel|{query}|{filt}|{hkey}|{nights}|{adults}|{start}|{end}"
if fetch:
    skip = set() if recheck else saved_hotel_days(db, query, filt, nights, adults, start, end)
    total = (end - start).days + 1 - len(skip) + (1 if hkey else 0)

    def work(j):
        tdb = connect()
        now = lambda: datetime.now().isoformat()
        fetched_now = set()

        def booking_days(days):
            """Booking.com price (via Xotelo) for each day not saved recently; returns False if the user pressed Stop."""
            fresh = {r[0] for r in tdb.execute("SELECT day FROM booking WHERE hkey=? AND nights=? AND fetched>=?",
                                               (hkey, int(nights), cutoff()))}
            todo = [d for d in days if d not in fetched_now and (recheck or d.isoformat() not in fresh)]
            j["total"] += len(todo)
            for d, price, err in parallel(todo, lambda d: booking_rate(hkey, d, int(nights)), workers=3):
                if j["cancel"]:
                    return False
                if not err:
                    tdb.execute("INSERT INTO booking VALUES(?,?,?,?,?)", (hkey, d.isoformat(), int(nights), price, now()))
                    tdb.commit()
                    fetched_now.add(d)
                record(j, "error" if err else "ok", err)
            return True

        if hkey:  # 1) cheap-day calendar: one quick call per month
            try:
                heat = {d: t for d, t in heatmap_range(hkey, start, end).items() if start.isoformat() <= d <= end.isoformat()}
                tdb.execute("DELETE FROM heat WHERE hkey=? AND day BETWEEN ? AND ?", (hkey, start.isoformat(), end.isoformat()))
                tdb.executemany("INSERT INTO heat VALUES(?,?,?,?)", [(hkey, d, t, now()) for d, t in heat.items()])
                tdb.commit()
                record(j, "ok")
            except Exception as e:
                heat = {}
                record(j, "error", f"Xotelo heatmap: {type(e).__name__}: {e}")
            if booking and not booking_days([date.fromisoformat(d) for d, t in sorted(heat.items()) if t == "cheap"]):
                return  # 2) Booking.com prices, cheap days only
        limited = False
        for d, name, price, status, err in fetch_range(start, end, query, filt, nights, adults, skip=skip):  # 3) Google, every day
            if j["cancel"]:
                return
            if status != "error":  # failed lookups are not saved, so the next run retries them
                tdb.execute("INSERT INTO hotel VALUES(?,?,?,?,?,?,?,?)",
                            (d.isoformat(), query, filt, nights, name, price, now(), int(adults)))
                tdb.commit()
            limited = limited or bool(err and "429" in err)
            record(j, status, err)
        if limited and hkey:  # 4) Google Hotels refuses this server: Booking.com prices for every day instead
            j["fallback"] = ("Google Hotels is not answering this server right now (HTTP 429), so Booking.com prices were "
                             "fetched for every night instead. Google's prices will be added once it answers again.")
            booking_days([start + timedelta(i) for i in range((end - start).days + 1)])

    tok = gh.token()
    if total > 0 and tok:  # Google rate-limits Streamlit Cloud's shared server, and what is saved there is lost on a restart
        missing = gaps(start, end, {(d, "google") for d in skip}, cabins=("google",))
        if not missing:  # every check-in is saved; GitHub would search them all again for the calendar alone
            total = 0
        try:
            if missing:
                st.session_state[f"gh-{key}"] = (gh.fetch_hotel(tok, query, filt, hkey, nights, adults, missing, booking), time.time())
        except gh.GitHubError as err:
            st.warning(f"{err} Searching from this server instead.", icon=":material/warning:")
            tok = None
    if total > 0 and not tok:
        start_job(key, total, work, "{n} dates show no listing for a hotel matching that name. Check the name filter.",
                  "with no listing", f"{filt} {start:%d %b}–{end:%d %b}")
    elif total <= 0:
        st.info("Every date in this range is already saved. Tick Re-check days already saved under Options to fetch again.")
track(key)
github_progress(key, "fetch-hotel.yml", "data/hotel_searches.csv")
github_note(key)

p = pd.read_sql("SELECT * FROM hotel WHERE query=? AND filt=? AND nights=? AND adults=? AND day BETWEEN ? AND ? ORDER BY fetched",
                db, params=(query, filt, int(nights), int(adults), start.isoformat(), end.isoformat()))
s = p.drop_duplicates("day", keep="last").set_index("day")[["hotel", "price"]].dropna(subset=["price"])
heat = {}
if hkey:
    heat = dict(db.execute("SELECT day, tier FROM heat WHERE hkey=? AND day BETWEEN ? AND ?", (hkey, start.isoformat(), end.isoformat())))
    b = pd.read_sql("SELECT day, price FROM booking WHERE hkey=? AND nights=? AND day BETWEEN ? AND ? ORDER BY fetched", db,
                    params=(hkey, int(nights), start.isoformat(), end.isoformat()))
    b = b.drop_duplicates("day", keep="last").set_index("day").price.dropna()
    s = s.join(b.rename("booking"), how="outer")

n = (end - start).days + 1
checked = checked_sentence([("Google Hotels", p.groupby("day").fetched.max(), n)] +
                           ([("Booking.com", pd.read_sql("SELECT day, MAX(fetched) f FROM booking WHERE hkey=? AND nights=? AND day BETWEEN ? AND ? "
                                                         "GROUP BY day", db, params=(hkey, int(nights), start.isoformat(), end.isoformat())).f, n)]
                            if hkey else []), unit="nights")
if checked:
    st.caption(checked)
ts = lambda i: f"{pd.Timestamp(i):%a %d %b}"
s = on_weekdays(s, weekdays)  # cards, chips and chart only look at the chosen weekdays
has_booking = "booking" in s and s.booking.notna().any()
main = "price" if s.price.notna().any() else "booking" if has_booking else None  # Booking.com stands in when Google is blocked
if not main and not heat and not st.session_state.get(f"gh-{key}"):  # nothing saved: one sentence, and Update prices above is the one button
    if len(p):
        st.info(f"No hotel called {filt} was found on these dates. Check the name, or try other dates.", icon=":material/hotel:")
    else:
        st.info(f"No prices saved yet for {filt} between {start:%a %d %b} and {end:%a %d %b}. Press Update prices to fetch them.",
                icon=":material/hotel:")
    st.stop()
near = {c: near_cheapest(s[c], tol) for c in ("price", "booking") if c in s}
if main:
    col, n = s[main], len(near[main])
    low, high = near[main].index[0], col.idxmax()
    where = "Google Hotels" if main == "price" else "Booking.com"
    also = f" · Booking.com €{s.booking[low]:.0f}" if main == "price" and has_booking and pd.notna(s.booking.get(low)) else ""
    cards = [("Cheapest night", f"€{col.min():.0f}", f"{ts(low)} ({where}){also}{nearly(n, tol).replace('days', 'nights')}",
              ECONOMY if main == "price" else BOOKING),
             ("Average night", f"€{col.mean():.0f}", f"across the {col.notna().sum()} nights checked", "#5B6B82"),
             ("Dearest night", f"€{col.max():.0f}", f"{ts(high)} ({where})", TIER_COLORS["high"])]
    if main == "price" and has_booking and s.booking.idxmin() != low:  # Booking.com's own cheapest night, when it is another night
        bl = s.booking.idxmin()
        cards.append(("Cheapest night on Booking.com", f"€{s.booking.min():.0f}", ts(bl), BOOKING))
else:
    cards = []
if heat:
    n = sum(t == "cheap" for t in heat.values())
    cards.append(("Cheap nights, says TripAdvisor", str(n), f"of the {len(heat)} nights in this range", TIER_COLORS["cheap"]))
kpis(cards)
if main:
    best_days(near[main], ECONOMY if main == "price" else BOOKING,
              "The cheapest nights are" if main == "price" else "The cheapest nights on Booking.com are")

if heat:
    st.markdown('<p class="card-title">Cheap, average and expensive nights, as TripAdvisor rates them</p>', unsafe_allow_html=True)
    st.plotly_chart(heat_strip(heat), width="stretch", config={"displayModeBar": False})
if main:
    st.markdown('<p class="card-title">The price each night; the cheapest nights are circled</p>', unsafe_allow_html=True)
    series = {"price": ("Google Hotels", ECONOMY)} if s.price.notna().any() else {}
    if has_booking:
        series["booking"] = ("Booking.com", BOOKING)
    st.plotly_chart(price_chart(s, series, near={c: list(near[c].index) for c in near}), width="stretch",
                    config={"displayModeBar": False})
    with st.expander("Table and export"):
        out = s.rename(columns={"hotel": "Hotel", "price": "Google Hotels € per night", "booking": "Booking.com € per night"})
        if heat:
            out["TripAdvisor says"] = pd.Series(heat)
        st.dataframe(out, width="stretch")
        st.download_button("Download Excel", excel_bytes(out), "hotel_prices.xlsx")
