import logging
import os
import urllib.parse
import re
import time
from datetime import date, datetime, timedelta

import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import importlib

import booking as links  # not "booking": the page's Booking.com checkbox has that name
import gh

if not hasattr(links, "hotel_url"):  # Streamlit Cloud can keep an older booking module loaded across a redeploy
    importlib.reload(links)
import hotels as _hotels  # noqa: E402
import xotelo as _xotelo  # noqa: E402

for _m, _names in ((_hotels, ("HOSTEL", "TAXED_SINCE", "basis", "checked_on", "sold_out", "small_room")),
                   (_xotelo, ("site_rates", "sites_text", "town", "key_for", "location", "site_url"))):
    if not all(hasattr(_m, n) for n in _names):  # an older copy kept across a redeploy (ImportError on 15 Nov)
        importlib.reload(_m)
from collector import gaps
from db import DAILY_HOTEL, HOTEL_SEARCHES, RETENTION_DAYS, connect, cutoff, saved_hotel_days
from fetch_hotel import LANDMARKS, NEARBY, PLACES, save_nearby, save_places
from html import escape

from hotels import SOLD_OUT, basis, best_value, checked_on, day_rows, fetch_range, km, matches, place_rows, plain, small_room, sold_out
from jobs import record, start_job, track
from par import parallel
from plain import checked_sentence, nearly, options_filters
from insights import FULL, weekday_chart, weekday_stats
from layout import page_head
from ui import (BOOKING, ECONOMY, FONT, INK, MUTED, TIER_COLORS, best_days, excel_bytes, heat_strip, kpis, near_cheapest, on_weekdays,
                price_chart, route, window_picker, github_progress, github_note)
import xotelo
from xotelo import booking_rate, heatmap_range, hotel_key, key_for, location, site_url

db = connect()
today = date.today()
STYLE = """<style>
.st-key-search [data-testid="stExpander"] { box-shadow:none; background:#F5F5F7; border-radius:10px; }
.st-key-search [data-testid="stExpander"] summary { padding:10px 14px; }
.hcard { background:#fff; border:1px solid #E5E5EA; border-radius:14px; padding:16px 20px; margin:4px 0 8px; }
.hcard h3 { margin:0 0 4px; font-size:1.25rem; line-height:1.3; }
.hcard .hmeta { color:#6E6E73; font-size:.9rem; }
.hcard .hstars { color:#F5A623; letter-spacing:1px; }
.hcard .hrow { display:flex; flex-wrap:wrap; gap:8px 32px; margin-top:12px; }
.hcard .hrow div { min-width:120px; }
.hcard .hrow b { display:block; font-size:1.35rem; }
.hcard .hrow span { color:#6E6E73; font-size:.8rem; }
</style>"""
st.markdown(STYLE, unsafe_allow_html=True)
HORIZON = 330  # Google Hotels prices about a year ahead
LIVE = 8  # hotels whose sites are asked live on one page view (the chosen one and the cheapest around it)


@st.cache_data(ttl=3600, show_spinner=False)
def live_rates(key, day, nights, adults):
    """Each site's rate for this hotel, check-in `day` (ISO), exactly `nights` and `adults`, from Xotelo (TripAdvisor's comparison)."""
    out = (date.fromisoformat(day) + timedelta(nights)).isoformat()
    return xotelo.site_rates(xotelo._get("rates", hotel_key=key, chk_in=day, chk_out=out, currency="EUR", adults=adults), nights)


@st.cache_data(ttl=86400, show_spinner=False)
def town_hotels(loc):
    """[(name, TripAdvisor key)] of a town's hotels, to find the key of each hotel Google lists there."""
    return xotelo.town(loc)


def stay_day_changed():
    """A check-in picked in the list of hotels around it: the per-site prices at the top follow it."""
    d = st.session_state.get("ho-stay-day")
    if d and d != "any":
        st.session_state["ho-site-day"] = date.fromisoformat(d)


def site_day_changed():
    """A check-in picked for the per-site prices: the list of hotels around it follows, when it has prices for that day."""
    d = st.session_state.get("ho-site-day")
    if d and d.isoformat() in st.session_state.get("ho-stay-options", ()):
        st.session_state["ho-stay-day"] = d.isoformat()


def asked(adults, nights, day):
    """The stay a price was asked for, in words: '2 adults, 3 nights from Fri 4 Dec 2026'."""
    return f"{int(adults)} adult{'s' * (adults > 1)}, {int(nights)} night{'s' * (nights > 1)} from {pd.Timestamp(day):%a %-d %b %Y}"

page_head("Hotels", "Every hotel in a city for your stay, or what a stay costs at your hotel and when to stay.")
# --- the hotels collected so far: pick one to open it with its saved dates, or download every price ------------------------------
saved = pd.read_sql("SELECT query, filt, nights, adults, COUNT(DISTINCT day) nights_saved, MIN(day) first, MAX(day) last, MAX(fetched) checked "
                    "FROM hotel WHERE price IS NOT NULL AND day >= ? GROUP BY query, filt, nights, adults ORDER BY checked DESC",
                    db, params=(today.isoformat(),))
keys = {}  # (query, filt) -> TripAdvisor key, from the files the searches were saved to (the database does not keep it)
for path in (db_path for db_path in (DAILY_HOTEL, HOTEL_SEARCHES, gh.data_file("data/daily_hotel.csv"), gh.data_file("data/hotel_searches.csv"))
             if db_path and os.path.exists(db_path)):
    for r in pd.read_csv(path, dtype=str, usecols=["query", "filt", "hkey"]).dropna(subset=["hkey"]).itertuples():
        keys.setdefault((r.query, r.filt), r.hkey)


# one entry per hotel however its name was typed (plain()), its stays (nights, adults) listed on it; picking it opens the newest
saved["who"] = [f"hotel {plain(f)}" if plain(f) else f"city {plain(q)}" for q, f in zip(saved["query"], saved.filt.fillna(""))]
entries = [g for _, g in saved.groupby("who", sort=False)]  # saved is newest first, so each entry's first row is its newest


def open_saved():
    """Fill the search with the hotel picked under Saved hotels, and the calendar with the check-in days it has prices for."""
    i = st.session_state.get("ho-saved")
    if i is None:
        return
    r = entries[i].iloc[0]
    st.session_state.update({"ho-query": r["query"], "ho-filt": r.filt, "ho-nights": int(r.nights), "ho-adults": int(r.adults),
                             "ho-mode": "hotel" if r.filt else "city", "ho-link": keys.get((r["query"], r.filt), ""),
                             "ho-when": (max(today, date.fromisoformat(r["first"])), date.fromisoformat(r["last"]))})


def label(i):
    g = entries[i]
    r = g.iloc[0]
    stays = " or ".join(dict.fromkeys(f"{int(n)} night{'s' * (int(n) > 1)}, {int(a)} adult{'s' * (int(a) > 1)}" for n, a in zip(g.nights, g.adults)))
    return (f"{r.filt or 'Every hotel: ' + r['query']} · {r.nights_saved} check-in days saved, {pd.Timestamp(r['first']):%d %b}–"
            f"{pd.Timestamp(r['last']):%d %b} · {stays}")


def arrived_nights():
    """The stay the visitor came with: ?nights=2 (or 2-7) in the address, or the Overview's nights this session (the shortest); else 2."""
    got = st.query_params.get("nights") or (st.session_state.get("ov-url") or {}).get("nights") or ""
    m = re.match(r"(\d{1,2})", str(got))
    return min(14, max(1, int(m[1]))) if m else 2


if len(saved):
    c = st.columns([3, 1], vertical_alignment="bottom")
    c[0].selectbox("Saved hotels", range(len(entries)), index=None, format_func=label, key="ho-saved", on_change=open_saved,
                   placeholder=f"{len(entries)} hotel{'s' * (len(entries) > 1)} with prices saved: pick one to open it")
    every = pd.read_sql("SELECT day, query, filt, nights, adults, hotel, price, fetched FROM hotel WHERE price IS NOT NULL "
                        "ORDER BY query, filt, nights, adults, day, fetched", db).drop_duplicates(["day", "query", "filt", "nights", "adults"], keep="last")
    every = every.rename(columns={"day": "Check-in", "query": "Search", "filt": "Hotel name contains", "nights": "Nights", "adults": "Adults",
                                  "hotel": "Hotel", "price": "€ per night (Google Hotels)", "fetched": "Checked"}).set_index("Check-in")
    c[1].download_button("Download all (Excel)", excel_bytes(every), "hotels_collected.xlsx", width="stretch",
                         help="Every hotel price saved, one row per check-in day. The Database page has the full history, with every change.")

if not len(saved):
    st.info("No hotel prices saved yet: type your hotel and its city below, pick the dates and press Update prices.", icon=":material/hotel:")
with st.container(border=True, key="search"):
    mode = st.pills("Look for", ["city", "hotel"], default="hotel", required=True, key="ho-mode",
                    format_func={"city": "Every hotel in a city or area", "hotel": "One hotel"}.get)
    c = st.columns([3, 2, 1, 1] if mode == "hotel" else [5, 1, 1])
    query = c[0].text_input("Hotel or city", "Steigenberger Frankfurt", key="ho-query",
                            help="A hotel and its city, or only a city or area (Frankfurt), as you would type them into Google Hotels.")
    filt = c[1].text_input("Hotel name contains", "Frankfurter Hof", key="ho-filt",
                           help="Picks your hotel out of the search results: part of its name is enough. Empty: every hotel of the search."
                           ) if mode == "hotel" else ""
    nights = c[-2].number_input("Nights", 1, 14, arrived_nights(), key="ho-nights", help="How many nights each stay lasts.")
    adults = c[-1].number_input("Adults", 1, 6, 2, key="ho-adults", help="How many adults share the room.")
    city = not filt.strip()  # every hotel of the search: the list, map and filters below; no single-hotel analysis
    start, end = window_picker(st, "ho", today, HORIZON, "Check-in between")
    fetch = st.button("Update prices", type="primary", width="stretch",
                      help="Fetches the price of a stay starting on every check-in day shown, and saves them. Days already saved are skipped.")
    with st.expander("Options"):
        link = st.text_input("TripAdvisor link (optional)", "", key="ho-link", placeholder="Found automatically when it can be",
                             help="Not needed: Update prices looks the hotel up on TripAdvisor by its name and place. Paste the hotel's "
                                  "TripAdvisor address only if it is not found; it adds the cheap-day calendar and Booking.com prices.")
        c = st.columns(2)
        booking = c[0].checkbox("Booking.com prices for the cheap days", value=True,
                                help="Also fetch Booking.com's own price on the days TripAdvisor marks cheap.")
        recheck = c[1].checkbox("Re-check days already saved", help="Fetch again the days that already have a saved price.")
        tol, weekdays = options_filters(st, "ho", unit="night")
        st.caption(f"Google Hotels' lowest price across the sites it compares, plus Booking.com's own. Saved prices are kept "
                   f"{RETENTION_DAYS} days.")

MAX_DAYS = 186
saved_key = next((k for (q, f), k in keys.items() if plain(q) == plain(query) and plain(f) == plain(filt)), None)
hkey = "" if city else hotel_key(link) or saved_key or ""  # the key pasted, else the one a fetch found for this hotel before
if not query.strip():
    st.info("Type a city (Frankfurt) or a hotel and its city (Steigenberger Frankfurt) under Hotel or city.")
    st.stop()
if link.strip() and not hkey and not city:
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

route(f"Hotels: {query}" if city else filt.title(), None, f"{'' if city else query + ' · '}check-in {start:%a %d %b} – {end:%a %d %b %Y} · "
                                                           f"{nights} night{'s' * (nights > 1)} · {adults} adult{'s' * (adults > 1)}")
# what every price is, said once here; tables and charts below only name it in short (UNIT)
UNIT = f"€ / night (room, {int(adults)} adult{'s' * (adults > 1)})"
st.caption(f"Every price on this page is {basis(adults)}: Google Hotels' lowest rate across the sites it compares"
           + ("" if city else ", or Booking.com's own where it says so") + "."
           + (f" For {int(nights)} nights it is the average night of a stay starting that day, so the stay costs {int(nights)} times it; "
              "a one-night booking usually costs more a night." if nights > 1 else ""))

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
            fresh = {r[0] for r in tdb.execute("SELECT day FROM booking WHERE hkey=? AND nights=? AND adults=? AND fetched>=?",
                                               (hkey, int(nights), int(adults), cutoff()))}
            todo = [d for d in days if d not in fetched_now and (recheck or d.isoformat() not in fresh)]
            j["total"] += len(todo)
            for d, price, err in parallel(todo, lambda d: booking_rate(hkey, d, int(nights), adults=int(adults)), workers=3):
                if j["cancel"]:
                    return False
                if not err:
                    tdb.execute("INSERT INTO booking VALUES(?,?,?,?,?,?)", (hkey, d.isoformat(), int(nights), price, now(), int(adults)))
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
        limited, places = False, {}  # places: every hotel the searches list, for the list and map below, as GitHub's fetch saves them
        for d, name, price, status, err in fetch_range(start, end, query, filt, nights, adults, skip=skip, places=places):  # 3) Google
            if j["cancel"]:
                break
            if status != "error":  # failed lookups are not saved, so the next run retries them
                tdb.execute("INSERT INTO hotel VALUES(?,?,?,?,?,?,?,?)",
                            (d.isoformat(), query, filt, nights, name, price, now(), int(adults)))
                tdb.commit()
            limited = limited or bool(err and "429" in err)
            record(j, status, err)
        if places:
            stamp = now()[:19]
            save_places(place_rows(places, query, int(nights), int(adults), stamp))
            save_nearby(day_rows(places, query, int(nights), int(adults), stamp))
        if j["cancel"]:
            return
        if limited and hkey:  # 4) Google Hotels refuses this server: Booking.com prices for every day instead
            j["fallback"] = ("Google Hotels is not answering this server right now, so Booking.com prices were "
                             "fetched for every night instead. Google's prices will be added once it answers again.")
            booking_days([start + timedelta(i) for i in range((end - start).days + 1)])

    # always from this server: from GitHub's servers Google ignores the country and answers with prices before taxes (checked 15 Nov:
    # €129 there against €165 here for the same room); what is saved here is lost on a restart, the price of correct numbers
    tok = None
    if total > 0 and tok:
        missing = gaps(start, end, {(d, "google") for d in skip}, cabins=("google",))
        if not missing:  # every check-in is saved; GitHub would search them all again for the calendar alone
            total = 0
        try:
            if missing:
                st.session_state[f"gh-{key}"] = (gh.fetch_hotel(tok, query, filt, hkey, nights, adults, missing, booking), time.time())
        except gh.GitHubError as err:  # the visitor sees the search start here instead (track below: "Fetching prices…"), no error code
            logging.warning("Hotel fetch on GitHub not started, searching from this server: %s", err)
            st.info("Fetching prices…", icon=":material/cloud_sync:")
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
    b = pd.read_sql("SELECT day, price FROM booking WHERE hkey=? AND nights=? AND adults=? AND day BETWEEN ? AND ? ORDER BY fetched", db,
                    params=(hkey, int(nights), int(adults), start.isoformat(), end.isoformat()))
    b = b.drop_duplicates("day", keep="last").set_index("day").price.dropna()
    s = s.join(b.rename("booking"), how="outer")

n = (end - start).days + 1
checked = checked_sentence([("Google Hotels", p.groupby("day").fetched.max(), n)] +
                           ([("Booking.com", pd.read_sql("SELECT day, MAX(fetched) f FROM booking WHERE hkey=? AND nights=? AND adults=? AND day BETWEEN ? AND ? "
                                                         "GROUP BY day", db, params=(hkey, int(nights), int(adults), start.isoformat(), end.isoformat())).f, n)]
                            if hkey else []), unit="check-in days")
if city:
    checked = None  # the hotel table holds only the cheapest hotel each day of a city search; the list below says when it was checked
if checked:
    st.caption(checked)
head = st.container()  # the chosen hotel's summary card, filled at the end once its place and rating are known
me, spot, ta = None, {}, []  # ta: the town's hotels with their TripAdvisor keys, once read below
# --- the hotels around it, read once: their place (data/hotel_places.csv) and price each check-in day (data/hotel_nearby.csv) ----------


def both_copies(repo_path, local, columns):
    """The repository's copy of a data file (what GitHub's fetches saved) and this server's own (what a search here saved), together;
    the readers below keep the newest row of each hotel and day."""
    files = dict.fromkeys(f for f in (gh.data_file(repo_path), str(local)) if f and os.path.exists(f))
    return pd.concat([pd.read_csv(f) for f in files], ignore_index=True) if files else pd.DataFrame(columns=columns)


pl = both_copies("data/hotel_places.csv", PLACES, [])
by_day = both_copies("data/hotel_nearby.csv", NEARBY, ["query", "nights", "adults", "day", "hotel", "price", "fetched"])
by_day = by_day[by_day.fetched.astype(str) >= _hotels.TAXED_SINCE]  # older Google prices were US-style, before taxes
# the searches this page reads: a hotel's own search (however its apostrophes were typed), or for a city every saved search that names
# it ("Frankfurt" reads "Steigenberger Frankfurt"'s hotels too: Google lists the hotels of the whole town for either)
same_search = (lambda q: matches(q, query)) if city else (lambda q: plain(q) == plain(query))
by_day = by_day[by_day["query"].map(same_search).astype(bool) & (by_day.adults == adults)
                & (by_day.day >= start.isoformat()) & (by_day.day <= end.isoformat()) & by_day.price.notna()]
stay_note = ""  # a city with prices saved only for another stay length: show those, said plainly, rather than an empty page
if city and len(by_day) and not (by_day.nights == nights).any():
    near_n = int(min(by_day.nights.unique(), key=lambda n: (abs(n - nights), n)))
    stay_note = (f"No prices saved yet for {int(nights)} night{'s' * (nights > 1)}; showing the prices saved for {near_n} "
                 f"night{'s' * (near_n > 1)} (a night's price, so the stay total below is approximate). Press Update prices for {int(nights)}.")
    by_day = by_day[by_day.nights == near_n]
else:
    by_day = by_day[by_day.nights == nights]
by_day = by_day.sort_values("fetched").drop_duplicates(["day", "hotel"], keep="last")
others = by_day[[city or not matches(n, filt) for n in by_day.hotel]] if len(by_day) else by_day
# the middle price a night of the hotels around it, each check-in day (none when no neighbour was saved for this stay)
area = others.groupby("day").price.median() if len(others) else pd.Series(dtype=float)
ts = lambda i: f"{pd.Timestamp(i):%a %d %b}"
s = on_weekdays(s, weekdays)  # cards, chips and chart only look at the chosen weekdays
has_booking = "booking" in s and s.booking.notna().any()
main = "price" if s.price.notna().any() else "booking" if has_booking else None  # Booking.com stands in when Google is blocked
# nights where only suites were left (a fair, an event): shown, but kept out of every average, comparison and the chart's scale
odd = {c: sold_out(s[c]) for c in ("price", "booking") if c in s}
suites = s[main][odd[main]] if main else pd.Series(dtype=float)  # this hotel's nights with only expensive rooms left
empty = city  # a city search has no hotel of its own to analyse: only the list and map below
if not empty and not main and not heat and not st.session_state.get(f"gh-{key}"):  # nothing saved: one sentence, Update prices is the button
    if len(p):
        st.info(f"No hotel called {filt} was found on these dates. Check the name, or try other dates.", icon=":material/hotel:")
    else:
        st.info(f"No prices saved yet for {filt} between {start:%a %d %b} and {end:%a %d %b}. Press Update prices to fetch them.",
                icon=":material/hotel:")
    empty = True  # the map below still shows where the hotel is and what is around it
if not empty:
    near = {c: near_cheapest(s[c], tol) for c in ("price", "booking") if c in s}
    name = s.hotel.dropna().iloc[0] if "hotel" in s and s.hotel.notna().any() else filt
    stay = f"{int(nights)} night{'s' * (nights > 1)}, {int(adults)} adult{'s' * (adults > 1)} in one room"
    st.markdown(f'<p class="group">{name}: what a stay of {stay} costs, by check-in day</p>', unsafe_allow_html=True)
    if main:
        col, n = s[main], len(near[main])
        low, high = near[main].index[0], col[~odd[main]].idxmax()
        where = "Google Hotels" if main == "price" else "Booking.com"
        also = f" · Booking.com €{s.booking[low]:.0f}" if main == "price" and has_booking and pd.notna(s.booking.get(low)) else ""
        whole = lambda v: f" · €{v * nights:,.0f} for the {int(nights)} nights" if nights > 1 else ""
        cards = [("Cheapest night" if nights == 1 else "Cheapest stay, a night", f"€{col.min():.0f}",
                  f"checking in {ts(low)}{whole(col.min())} ({where}){also}{nearly(n, tol).replace('days', 'check-in days')}",
                  ECONOMY if main == "price" else BOOKING),
                 ("Typical night", f"€{col.median():.0f}", f"the middle of the {col.notna().sum()} check-in days with a price", "#5B6B82"),
                 ("Dearest night", f"€{col[high]:.0f}", f"{ts(high)} ({where})" + (", not counting the nights with only expensive "
                                                                                  "rooms left" if len(suites) else ""), TIER_COLORS["high"])]
        if len(suites):
            cards.append(("Only expensive rooms left", f"{len(suites)} night{'s' * (len(suites) > 1)}",
                          ", ".join(f"{ts(d)} €{v:,.0f}" for d, v in suites.sort_index().items()) + ": likely a fair or an event",
                          TIER_COLORS["high"]))
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
        go_check = date.fromisoformat(low)
        st.link_button(f"Check it on Booking.com: {go_check:%a %-d %b}, {stay}", links.hotel_url(name, go_check, int(nights), int(adults)),
                       icon=":material/open_in_new:")
        best_days(near[main], ECONOMY if main == "price" else BOOKING,
                  "The cheapest nights are" if main == "price" else "The cheapest nights on Booking.com are")
        series = {"price": ("Google Hotels", ECONOMY)} if s.price.notna().any() else {}
        if has_booking:
            series["booking"] = ("Booking.com", BOOKING)
        chart = s.join(area.rename("area"), how="left") if len(area) else s
        flagged = {c: s[c][odd[c]] for c in series if odd[c].any()}
        st.markdown(f'<p class="card-title">The price each night ({UNIT}); the cheapest nights are circled'
                    + ("; the dashed line is the middle price of the hotels around it" if len(area) else "")
                    + ("; ▲ a night with only expensive rooms left, off the scale" if flagged else "") + "</p>", unsafe_allow_html=True)
        fig = price_chart(chart, series, near={c: list(near[c].index) for c in near},
                          extra={"area": ("Hotels around it (middle price)", MUTED)} if len(area) else None)
        if flagged:  # scale the chart to the normal nights, so a €3,000 suite night does not flatten them; mark those nights at the top
            normal = [chart[c].where(~odd[c]) for c in series] + ([chart["area"]] if "area" in chart else [])
            cap = 1.15 * max(v.max() for v in normal if v.notna().any())
            fig.update_yaxes(range=[0, cap])
            for c, v in flagged.items():
                fig.add_scatter(x=pd.to_datetime(v.index), y=[cap * 0.96] * len(v), mode="markers", name="Only expensive rooms left",
                                marker=dict(symbol="triangle-up", size=12, color=TIER_COLORS["high"]), showlegend=c == next(iter(flagged)),
                                customdata=v.values, hovertemplate=f"{series[c][0]}: €%{{customdata:,.0f}}, only expensive rooms left<extra></extra>")
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    if heat:
        st.markdown('<p class="card-title">Cheap, average and expensive nights, as TripAdvisor rates them</p>', unsafe_allow_html=True)
        st.plotly_chart(heat_strip(heat), width="stretch", config={"displayModeBar": False})

    # --- when to stay: what the saved nights say, each as one sentence -------------------------------------------------------------
    if main:
        col = pd.to_numeric(s[main], errors="coerce").dropna()
        col = col[~odd[main][col.index]]  # the nights with only expensive rooms left would swamp every average below
        wd = weekday_stats(col).dropna()
        notes = []
        if len(suites):
            notes.append(f"On {', '.join(ts(d) for d in suites.index[:4])}{' and more' if len(suites) > 4 else ''} only expensive rooms were left "
                         f"(more than {SOLD_OUT:g}× its usual €{s[main].median():.0f}): most likely a fair or an event in town. Those "
                         f"nights are left out of the averages here.")
        days = pd.to_datetime(col.index)
        wkend, wkday = col[days.dayofweek.isin([4, 5])], col[~days.dayofweek.isin([4, 5])]
        if len(wkend) >= 2 and len(wkday) >= 2:
            d = wkend.mean() - wkday.mean()
            notes.append(f"Friday and Saturday nights average €{wkend.mean():.0f}, €{abs(d):.0f} {'more' if d > 0 else 'less'} than "
                         f"Sunday to Thursday (€{wkday.mean():.0f}).")
        both = pd.concat([col.rename("me"), area.rename("area")], axis=1).dropna()
        if len(both) >= 3:
            ratio = both.me.mean() / both.area.mean()
            dearer = (others.groupby("hotel").price.mean() < both.me.mean()).sum()
            notes.append(f"On the same nights it costs €{both.me.mean():.0f} on average against €{both.area.mean():.0f} for the middle hotel "
                         f"around it ({ratio:.1f}×); {dearer} of the {others.hotel.nunique()} other hotels priced on these check-in days are cheaper on average.")
        if len(area) >= 7:
            busy = area[area > 1.4 * area.median()]
            if len(busy):
                notes.append(f"The whole area is dear on {', '.join(f'{pd.Timestamp(d):%a %-d %b}' for d in busy.index[:4])} (middle price "
                             f"€{busy.max():.0f} against €{area.median():.0f} usually): most likely a fair or an event in town.")
        if has_booking and main == "price":
            pair = s.loc[~odd["price"] & ~odd["booking"], ["price", "booking"]].dropna()
            if len(pair) >= 3:
                g = (pair.price < pair.booking).sum()
                notes.append(f"Google Hotels found a cheaper rate than Booking.com on {g} of {len(pair)} nights, by "
                             f"€{(pair.booking - pair.price).mean():.0f} a night on average.")
        if len(wd) >= 3 or notes:
            st.markdown('<p class="group">When to stay</p>', unsafe_allow_html=True)
            c = st.columns([1, 1.25], gap="medium")
            if len(wd) >= 3:
                cheap, dear = wd["mean"].idxmin(), wd["mean"].idxmax()
                with c[0]:
                    st.markdown(f'<p class="card-title">{FULL[cheap]} check-ins are the cheapest: €{wd.loc[cheap, "mean"]:.0f} a night on '
                                f'average, €{wd["mean"].max() - wd["mean"].min():.0f} less than {FULL[dear]}.</p>', unsafe_allow_html=True)
                    st.plotly_chart(weekday_chart(wd, ECONOMY).update_layout(height=220), width="stretch", config={"displayModeBar": False})
            with c[1 if len(wd) >= 3 else 0]:
                st.markdown("\n".join(f"- {n}" for n in notes))
        with st.expander("Every night, as a table, and the Excel file"):
            out = s.rename(columns={"hotel": "Hotel", "price": f"Google Hotels {UNIT}", "booking": f"Booking.com {UNIT}"})
            if len(area):
                out[f"Hotels around it, middle {UNIT}"] = area
            if heat:
                out["TripAdvisor says"] = pd.Series(heat)
            if any(o.any() for o in odd.values()):
                out["Note"] = pd.concat(odd.values(), axis=1).any(axis=1).map({True: "only expensive rooms left (likely a fair or event)", False: ""})
            st.dataframe(out, width="stretch")
            st.download_button("Download Excel", excel_bytes(out), "hotel_prices.xlsx")

# --- the hotels of the search for one stay: one set of filters drives the sentence, the map and the table, so all three agree ----------
RADIUS, CHEAPER = 3, "#0E7A53"  # km; the green of a cheaper stay elsewhere on the site
here = pl[pl["query"].map(same_search).astype(bool)] if len(pl) else pl
if len(here):
    same = here[(here.nights == nights) & (here.adults == adults)]
    # one row per hotel: the newest, from this stay length if it was searched, else from any (two spellings of a search overlap)
    here = (same if len(same) else here).sort_values("fetched").drop_duplicates("hotel", keep="last").copy()
    mine = here.iloc[0:0] if city else here[here.hotel.map(lambda n: matches(n, filt))]
    fresh = pd.to_numeric(s[main], errors="coerce").dropna() if main and not city else pd.Series(dtype=float)
    if len(mine) and len(fresh):  # its own freshest prices, the ones the cards above show, not the older snapshot of the hotels around
        here.loc[mine.index[0], ["low", "typical", "first", "last"]] = [round(fresh.min()), round(fresh.median()), fresh.index.min(), fresh.index.max()]
        mine = here.loc[mine.index]
    me = mine.iloc[0] if len(mine) else None
    if me is not None:
        here["km"] = [km(me.lat, me.lng, a, b) for a, b in zip(here.lat, here.lng)]
    marks = both_copies("data/hotel_landmarks.csv", LANDMARKS, ["query", "kind", "label", "name", "lat", "lng"])
    marks = marks[marks["query"].map(same_search).astype(bool)].drop_duplicates("kind")
    spot = {k: marks[marks.kind == k].iloc[0] for k in ("station", "centre") if (marks.kind == k).any()}
    middle = city and "centre" not in spot  # no town centre saved for this city: distances from the middle of its hotels instead
    if middle:
        spot["centre"] = pd.Series(dict(lat=here.lat.median(), lng=here.lng.median()))
    for k, m in spot.items():  # how far each hotel is from the main station and the town centre
        here[f"to_{k}"] = [km(m.lat, m.lng, a, b) for a, b in zip(here.lat, here.lng)]
    if me is not None:
        me = here.loc[mine.index[0]]
    centre_name = "the middle of its hotels" if middle else "the centre"
    # the distance filter: from your hotel, or for a city from its centre
    dist, dist_name = ("km", f"from {me.hotel.split(',')[0]}") if me is not None else ("to_centre", f"from {centre_name}") if "to_centre" in here else (None, "")

    st.markdown(f'<p class="group">{"Hotels" if city else "Hotels around it"} for your stay: {int(nights)} night{"s" * (nights > 1)}, '
                f'{int(adults)} adult{"s" * (adults > 1)}</p>', unsafe_allow_html=True)
    if stay_note:
        st.info(stay_note)
    pool = by_day
    if me is not None and len(fresh):  # the chosen hotel at its freshest prices, the same as the cards above, not the older snapshot
        pool = pd.concat([others, pd.DataFrame({"day": fresh.index, "hotel": me.hotel, "price": fresh.values})], ignore_index=True)
    stay = pool.iloc[0:0].assign(total=[], small=[])
    if not len(pool):
        st.caption(f"No hotel prices saved for {int(nights)} night{'s' * (nights > 1)} and {int(adults)} adult{'s' * (adults > 1)} on these "
                   "check-in days. Press Update prices with these settings to fetch them.")
    else:
        ANY = "any"
        c = st.columns([2, 1, 1], vertical_alignment="bottom") + st.columns([1, 1, 1], vertical_alignment="bottom")
        st.session_state["ho-stay-options"] = set(pool.day)  # what the per-site check-in at the top may set this to
        day = c[0].selectbox("Check-in", [ANY] + sorted(pool.day.unique()), key="ho-stay-day", on_change=stay_day_changed,
                             format_func=lambda d: "Any day: each hotel's cheapest check-in" if d == ANY else f"{pd.Timestamp(d):%a %-d %b %Y}")
        cap = c[1].number_input("Max € a night", min_value=0, value=None, step=10, key="ho-stay-max", placeholder="Any",
                                help=f"The most a night may cost ({UNIT}).")
        stars = c[3].pills("Stars at least", [0, 3, 4, 5], default=0, required=True, key="ho-stay-stars",
                           format_func=lambda v: f"{v}★" if v else "Any")
        rated = c[4].pills("Rating at least", [0, 4.0, 4.5], default=0, required=True, key="ho-stay-rating",
                           format_func=lambda v: "Any" if not v else f"{v:g}")
        within = c[5].pills(f"Within, {dist_name}" if dist else "Within", [1, 3, 10, 0], default=RADIUS, required=True, key="ho-stay-km",
                            format_func=lambda v: f"{v} km" if v else "Any", disabled=dist is None)
        stay = pool if day == ANY else pool[pool.day == day]
        stay = stay.sort_values("price").drop_duplicates("hotel")  # each hotel's cheapest check-in, or its price on that day
        stay = stay.merge(here.drop(columns=["query", "nights", "adults", "low", "typical", "days", "first", "last", "fetched"]), on="hotel", how="left")
        yours = stay.hotel == (me.hotel if me is not None else None)  # your hotel stays in, to compare with, whatever the filters
        ok = pd.Series(True, index=stay.index)
        if cap:
            ok &= stay.price <= cap
        if stars:
            ok &= pd.to_numeric(stay.stars, errors="coerce").fillna(0) >= stars
        if rated:
            ok &= stay.rating.fillna(0) >= rated
        if within and dist:
            ok &= stay[dist] <= within
        stay = stay[ok | yours]
        stay = stay.assign(total=stay.price * nights).sort_values("total")
        # no star class and under half the area's middle price that night: maybe a bed in a shared room; listed, never the headline
        stay["small"] = small_room(stay.stars, stay.price, stay.day.map(area), stay.rating, stay.reviews, stay.hotel) if len(area) else False
        stay["small"] &= stay.hotel != (me.hotel if me is not None else None)
        mine_name = me.hotel if me is not None else None
        # each site's own rate for the cheapest hotels, asked live for exactly this check-in, nights and adults (Google's is one number,
        # its cheapest site's, unnamed): TripAdvisor keys from the town's list, matched by name
        loc = location(hkey) or next((location(k) for (q, _), k in keys.items() if same_search(q) and location(k)), None)
        try:
            ta = town_hotels(loc) if loc else []
        except Exception as e:  # Xotelo down: Google's prices only, labelled so
            logging.warning("Xotelo town list for %s: %s", loc, e)
            ta = []
        stay["ta_key"] = [hkey if hkey and h == mine_name else key_for(h, ta) if ta else None for h in stay.hotel]
        top = stay[~stay.small | (stay.hotel == mine_name)].head(LIVE)
        ask = [(i, r.ta_key, r.day) for i, r in top.iterrows() if r.ta_key]
        got = {a[0]: rows for a, rows, err in parallel(ask, lambda a: live_rates(a[1], a[2], int(nights), int(adults)), workers=4, retries=0)
               if not err and rows}
        first = lambda i, f: got[i][0][f] if i in got else None
        stay["site_night"] = pd.to_numeric(pd.Series([first(i, "night") for i in stay.index], index=stay.index), errors="coerce")
        stay["site"] = [first(i, "site") for i in stay.index]
        stay["site_code"] = [first(i, "code") for i in stay.index]
        stay["booking_night"] = [next((x["night"] for x in got.get(i, []) if x["code"] == "BookingCom"), None) for i in stay.index]
        stay["sites"] = [xotelo.sites_text(got[i]) if i in got else "" for i in stay.index]
        stay["eff"] = stay.price * nights  # Google's price as a traveller in Greece sees it, taxes included; the sites' own are before taxes
        listed = pool.hotel[pool.hotel != mine_name].nunique()  # the other hotels with a price on these check-in days
        n_ok = int((stay.hotel != mine_name).sum())
        if not listed:  # only your hotel has prices for this stay: nothing to rank it against yet
            st.caption(f"No other hotel has a price for {int(nights)}-night stays, {int(adults)} adult{'s' * (adults > 1)}, on these check-in "
                       "days yet. Press Update prices with these settings to fetch the hotels around it.")
        elif not len(stay):
            st.caption(f"None of the {listed} hotels with a price on these check-in days matches those filters. Raise the price, lower the "
                       "stars or rating, or widen the distance.")
        else:
            fair = stay[~stay.small]
            cand = fair if len(fair) else stay
            best = cand.loc[cand.eff.idxmin()]
            mine_row = stay[stay.hotel == me.hotel] if me is not None else stay.iloc[0:0]
            where = lambda r: (f"(€{r.price:,.0f} a night with taxes, Google Hotels" + (f"; before taxes {r.site} reports €{r.site_night:,.0f}"
                               + (f", Booking.com €{r.booking_night:,.0f}" if pd.notna(r.booking_night) and r.site_code != "BookingCom" else "")
                               if pd.notna(r.site_night) else "") + ")")
            sentence = f"**Cheapest for your stay: {best.hotel}**, €{best.eff:,.0f} for {asked(adults, nights, best.day)} {where(best)}."
            if len(mine_row) and mine_row.iloc[0].hotel != best.hotel:
                y = mine_row.iloc[0]
                sentence += (f" That is €{y.eff - best.eff:,.0f} less than {y.hotel}: €{y.eff:,.0f} for {asked(adults, nights, y.day)} {where(y)}.")
            sentence += f" {n_ok} of the {listed} {'other ' if me is not None else ''}hotels with prices fit your filters."
            k = int(stay.small.sum())
            if k and len(fair):
                sentence += (f" Not counted: {k} cheaper listing{'s' * (k > 1)} that may be a bed in a shared room or a very small room "
                             "(hostels, and places without stars at under half the usual price), marked in the table.")
            st.markdown(sentence)
            c1, c2 = st.columns(2)
            b_day = pd.Timestamp(best.day).date()
            if pd.notna(best.site_night):
                url, shows = site_url(best.site_code, best.hotel, best.ta_key, b_day, int(nights), int(adults))
                c1.link_button(f"See €{best.site_night:,.0f} a night on {best.site}" if not shows.startswith("compare") else
                               f"Compare {best.site}'s €{best.site_night:,.0f} on TripAdvisor", url, icon=":material/open_in_new:", width="stretch")
            else:
                c1.link_button(f"Check {best.hotel} on Booking.com", links.hotel_url(best.hotel, b_day, int(nights), int(adults)),
                               icon=":material/open_in_new:", width="stretch")
            c2.link_button("See it on Google Hotels", "https://www.google.com/travel/search?q=" + urllib.parse.quote(f"{best.hotel} hotel"),
                           icon=":material/open_in_new:", width="stretch")
            st.caption(f"Main prices: Google Hotels as a traveller in Greece sees it, taxes included, for {int(adults)} adult{'s' * (adults > 1)} "
                       f"and {int(nights)} night{'s' * (nights > 1)}: its cheapest site, often a non-refundable rate. The sites' own figures "
                       "(from TripAdvisor) are before VAT and city tax, so they read lower than what the site charges at checkout.")
            if checked_on(others.fetched):
                st.caption(f"Prices of the {'hotels' if city else 'hotels around it'} checked {checked_on(others.fetched)}"
                           + (f"; {me.hotel}'s are its own freshest, checked {checked_on(p.groupby('day').fetched.max())}" if me is not None and len(fresh) else "")
                           + (f". No town centre saved for {query}: distances are from {centre_name}" if middle else "") + ".")

    # the map: exactly the hotels of the table (and yours), green where the stay is cheaper than at yours on the same check-in
    mine_by_day = (s[main] if main and not city else pd.Series(dtype=float)) * nights  # this hotel's own total, each check-in day
    stay["saves"] = [mine_by_day.get(d) - t if pd.notna(mine_by_day.get(d)) else None for d, t in zip(stay.day, stay.total)]
    rest = (stay[stay.hotel != (me.hotel if me is not None else None)].dropna(subset=["lat", "lng"]) if len(stay)
            else pd.DataFrame(columns=["hotel", "lat", "lng", "saves"]))

    def text(r):
        stars_ = f"{int(r.stars)}-star · " if pd.notna(r.get("stars")) else ""
        rating = f"{r.rating:.1f}/5 ({int(r.reviews):,} reviews)" if pd.notna(r.get("rating")) and pd.notna(r.get("reviews")) else ""
        price = (f"€{r.price:,.0f} a night, €{r.total:,.0f} for {int(nights)} night{'s' * (nights > 1)}, checking in {pd.Timestamp(r.day):%a %-d %b}"
                 if pd.notna(r.get("price")) else "no price for these dates")
        away = f"<br>{r.km:.1f} km away" if pd.notna(r.get("km")) and r.km > 0.05 else ""
        to = " · ".join(f"{r[f'to_{k}']:.1f} km to the {'main station' if k == 'station' else 'centre'}" for k in spot if pd.notna(r.get(f"to_{k}")))
        return f"<b>{r.hotel}</b><br>{stars_}{rating}<br>{price}{away}" + (f"<br>{to}" if to else "")

    fig = go.Figure()
    cheaper = rest.saves > 0 if len(rest) else pd.Series(dtype=bool)
    for rows, name, colour in ((rest[~cheaper], "Hotels in the list" if city else "Other hotels in the list", MUTED if me is not None else ECONOMY),
                               (rest[cheaper], f"Cheaper than {me.hotel if me is not None else 'it'}", CHEAPER)):
        if len(rows):
            fig.add_scattermap(lat=rows.lat, lon=rows.lng, mode="markers", name=name, marker=dict(size=11, color=colour, opacity=.85),
                               text=[text(r) for _, r in rows.iterrows()], hovertemplate="%{text}<extra></extra>")
    if me is not None:
        mine_now = stay[stay.hotel == me.hotel]
        r = mine_now.iloc[0].combine_first(me) if len(mine_now) else me
        fig.add_scattermap(lat=[me.lat], lon=[me.lng], mode="markers", name=me.hotel, marker=dict(size=20, color=ECONOMY),
                           text=[text(r)], hovertemplate="%{text}<extra></extra>")
    # the places a traveller finds their way by, labelled on the map itself; each label on its own side, because the centre and the
    # cathedral are often a few hundred metres apart and the map hides a label that overlaps another
    side = {"centre": "top left", "church": "bottom right", "station": "bottom center"}
    for i, (_, m) in enumerate(marks.iterrows()):
        fig.add_scattermap(lat=[m.lat], lon=[m.lng], mode="markers+text", name="Centre, station, church", legendgroup="marks",
                           showlegend=i == 0, text=[m.label], textposition=side.get(m.kind, "top center"), textfont=dict(size=13, color=INK),
                           marker=dict(size=13, color=INK), customdata=[m["name"]], hovertemplate="<b>%{text}</b><br>%{customdata}<extra></extra>")
    shown = pd.concat([rest[["lat", "lng"]], mine.head(1)[["lat", "lng"]], marks[["lat", "lng"]]])
    if len(shown):
        spread = max([km(shown.lat.mean(), shown.lng.mean(), a, b) for a, b in zip(shown.lat, shown.lng)] + [0.3])
        fig.update_layout(height=440, margin=dict(l=0, r=0, t=0, b=0), font=dict(family=FONT, color=INK), hoverlabel=dict(font_family=FONT),
                          legend=dict(orientation="h", x=0, y=1.0, yanchor="bottom", bgcolor="rgba(0,0,0,0)"),
                          map=dict(style="carto-positron", center=dict(lat=shown.lat.mean(), lon=shown.lng.mean()),
                                   zoom=max(8, min(15, 13.8 - math.log2(spread)))))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False, "scrollZoom": True})
        st.caption((f"The {len(rest)} hotel{'s' * (len(rest) != 1)} of the list below" if len(rest) else "No other hotel has a price for this stay")
                   + (f"; the big dot is {me.hotel}" if me is not None else "")
                   + (f". Green: a cheaper stay than at {me.hotel} checking in the same day" if me is not None and cheaper.any() else "")
                   + ". Hover a hotel for its price, rating and distances.")

    if len(stay):
        top = best_value(stay.rating.tolist(), stay.total.where(~stay.small).tolist(), stay.reviews.tolist())
        far = "km" if me is not None else "to_centre"  # one distance: from your hotel, or for a city from its centre
        out = stay.assign(**{"Check-in": stay.day.map(lambda d: f"{pd.Timestamp(d):%a %-d %b}"),
                             "Stars · rating": [" ".join(x for x in ("★" * int(v) if pd.notna(v) else "", f"{r:.1f}" if pd.notna(r) else "") if x)
                                                for v, r in zip(stay.stars, stay.rating)],
                             "Note": ["shared or small room?" if sm else "only suites left" if me is not None and h == me.hotel and d in suites.index
                                      else "Best value" if i == top else "" for i, (sm, h, d) in enumerate(zip(stay.small, stay.hotel, stay.day))]})
        total, night, sites = f"Google € for {int(nights)} night{'s' * (nights > 1)}", "Google € / night", "Sites, € a night"
        short = me.hotel.split(",")[0] if me is not None else "this hotel"
        km_col = f"km from {short}" if me is not None else "km to centre"
        out = out.rename(columns={"hotel": "Hotel", "price": night, "total": total, far: km_col, "saves": "You save", "sites": sites})
        has_sites = sites in out and (out[sites].fillna("") != "").any()
        cols = ["Hotel", "Check-in"] + ([sites] if has_sites else []) + [total] + ([night] if nights > 1 else []) + \
               (["You save"] if out["You save"].notna().any() else []) + \
               ["Stars · rating"] + ([km_col] if km_col in out and out[km_col].notna().any() else []) + (["Note"] if (out.Note != "").any() else [])
        # fixed widths that add up to the card, the name taking what is left: a wider table hid its last column behind a scroll
        # ponytail: sized for the desktop card (~880 px less a scrollbar); a phone still scrolls the table sideways
        px = {"Check-in": 90, sites: 210, total: 100, night: 80, "You save": 85, "Stars · rating": 110, km_col: 85, "Note": 150}
        rest_px = max(160, 800 - sum(px[c_] for c_ in cols if c_ in px))
        conf = {c_: st.column_config.NumberColumn(format="€%d", width=px[c_], help=UNIT if c_ == night else None)
                for c_ in cols if c_.startswith("Google €") or c_ == "You save"}
        conf |= {km_col: st.column_config.NumberColumn(format="%.1f", width=px[km_col]), "Hotel": st.column_config.TextColumn(width=rest_px),
                 "Check-in": st.column_config.TextColumn(width=px["Check-in"]),
                 sites: st.column_config.TextColumn(width=px[sites], help="Each site's own rate a night for these adults, nights and check-in"),
                 "Stars · rating": st.column_config.TextColumn(width=px["Stars · rating"], help="Google's star class and guest rating out of 5"),
                 "Note": st.column_config.TextColumn(width=px["Note"])}
        st.dataframe(out[cols], hide_index=True, width="stretch", height=min(36 * len(out) + 38, 400), column_config=conf)
        st.caption((f"{sites}: each site's own rate for {int(adults)} adult{'s' * (adults > 1)}, {int(nights)} night{'s' * (nights > 1)} from the "
                    f"check-in shown, before city tax, asked live for the {len(got)} cheapest hotels found on TripAdvisor. " if has_sites else "")
                   + f"Google €: Google Hotels' lowest, {basis(adults)}, site not named. "
                   + ("Best value: the highest guest rating per euro of the stay, among hotels rated 4.0 or more by at least 20 guests. "
                    if top is not None else "")
                   + (f"You save: how much less the stay costs than at {short} checking in the same day (negative: it costs more). "
                      if out["You save"].notna().any() else "")
                   + "Shared or small room: no star class and under half the middle price of the hotels around it that night, so it is "
                     "not picked as cheapest or best value.")

# --- summary card of the chosen hotel (top of the page) ----------------------------------------------------------------------------
if not city:  # a city search has no hotel of its own to sum up
    with head:
        h_name = me.hotel if me is not None else (s.hotel.dropna().iloc[0] if "hotel" in s and s.hotel.notna().any() else filt.title())
        meta = []
        if me is not None and pd.notna(me.stars):
            meta.append(f'<span class="hstars">{"★" * int(me.stars)}</span> {int(me.stars)}-star')
        if me is not None and pd.notna(me.rating):
            meta.append(f"{me.rating:.1f}/5 guest rating" + (f" ({int(me.reviews):,} reviews)" if pd.notna(me.reviews) else ""))
        if me is not None and pd.notna(me.get("to_centre")):
            meta.append(f"{me.to_centre:.1f} km to the centre")
        if main:
            col = pd.to_numeric(s[main], errors="coerce").dropna()
            h_day, h_price = col.idxmin(), col.min()
            src = "Google's lowest, site unknown" if main == "price" else "Booking.com"
            facts = (f'<div class="hrow"><div><b>€{h_price:.0f}</b><span>a night, {src}, {escape(asked(adults, nights, h_day))}</span></div>'
                     f'<div><b>€{h_price * nights:,.0f}</b><span>that stay of {int(nights)} night{"s" * (nights > 1)}, before city tax</span></div></div>')
        else:
            facts = '<div class="hmeta" style="margin-top:8px">No prices saved for these dates yet: press Update prices.</div>'
        st.markdown(f'<div class="hcard"><h3>{escape(h_name)}</h3><div class="hmeta">{" · ".join(meta) or escape(query)}</div>{facts}</div>',
                    unsafe_allow_html=True)
        # every site's price for exactly this stay, asked live: what the traveller can check on each site
        site_key = hkey or (key_for(h_name, ta) if ta else None)
        picked = st.session_state.get("ho-stay-day")
        default = date.fromisoformat(picked if picked and picked != "any" else h_day if main else start.isoformat())
        sig = f"{query}|{filt}|{nights}|{adults}|{start}|{end}"  # another hotel or stay: start again from its cheapest check-in
        if st.session_state.get("ho-site-sig") != sig or not st.session_state.get("ho-site-day"):
            st.session_state["ho-site-sig"], st.session_state["ho-site-day"] = sig, default
        if site_key:
            chk = st.date_input("Check-in for the prices per site", min_value=today, max_value=today + timedelta(HORIZON),
                                key="ho-site-day", on_change=site_day_changed, format="DD/MM/YYYY",
                                help="Each site's price is asked live for this check-in, with the nights and adults above.")
            st.markdown(f'<p class="group">Prices for {escape(asked(adults, nights, chk))}</p>', unsafe_allow_html=True)
            try:
                rows = live_rates(site_key, chk.isoformat(), int(nights), int(adults))
            except Exception as e:
                rows = None
                logging.warning("Xotelo rates for %s: %s", site_key, e)
                st.caption(f"Each site's price could not be fetched right now ({type(e).__name__}): reload the page in a minute.")
            if rows == []:
                st.info(f"No site lists {h_name} for {asked(adults, nights, chk)}: it may be full. Try another check-in.")
            elif rows:
                # a plain table: a few rows, each with a link that opens the same price on the site
                lines = [f"| Site | € a night | € for {int(nights)} night{'s' * (nights > 1)} | See it |", "|---|---:|---:|---|"]
                for r in rows:
                    url, shows = site_url(r["code"], h_name, site_key, chk, int(nights), int(adults))
                    lines.append(f"| {r['site']} | €{r['night']:,.0f} | €{r['total']:,.0f} | [{shows}]({url}) |")
                st.markdown("\n".join(lines))
                taxed = [f"{r['site']} €{r['tax']:,.0f}" for r in rows if r.get("tax")]
                st.caption(f"The room rate each site reports to TripAdvisor for {asked(adults, nights, chk)}, checked within the hour; "
                           "before VAT and city tax, which every site adds at checkout, so the site's final price is higher (and higher again for a "
                           "different room or a refundable rate)." + (f" City tax reported: {', '.join(taxed)}." if taxed else ""))
        elif main:
            st.link_button(f"Check it on Booking.com: {asked(adults, nights, h_day)}",
                           links.hotel_url(h_name, date.fromisoformat(h_day), int(nights), int(adults)), icon=":material/open_in_new:")
