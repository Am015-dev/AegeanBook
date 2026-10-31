"""Overview: the day's answer across everything the daily run collects (four legs, two cabins, award miles, one hotel), as plain numbers,
plain sentences and one compact text block the virtual expert reads. Pure functions over the database; the page is views/overview.py.
A new module on purpose: Streamlit Cloud can run a new page against older, already loaded modules."""
from datetime import timedelta

import pandas as pd

from award_view import load_award
from insights import FULL, cash_by_day, routes, weekday_stats
from trip_cost import night_prices, trip_costs
from trip_plan import city, hotels as searched_hotels
from ui import round_trips

VERSION = 4  # pages reload this module when Streamlit Cloud still has an older copy loaded (it keeps modules across redeploys)

LEGS = [("ATH", "FRA"), ("FRA", "ATH"), ("ATH", "ZRH"), ("ZRH", "ATH")]
CABINS = ("economy", "business")
NIGHTS = (2, 3, 4, 7)
DAYS = 90


def legs(db, today, days=DAYS):
    """{(orig, dest): {cabin: Series of fares by iso day}} over the next `days` days: the daily run's LEGS first, then every other route with
    fares saved (searched on Flights), so a route like BIO -> ATH shows up as soon as its fares are in. Legs with no fare are left out.
    The pages pass trip_plan.HORIZON where a month fetched far ahead must show (the Overview's date grid, Explore)."""
    start, end = today + timedelta(1), today + timedelta(days)
    out = {}
    for o, d in LEGS + [r for r in routes(db, True) if r not in LEGS]:
        fares = {c: cash_by_day(db, o, d, c, True, start, end) for c in CABINS}
        if any(len(s) for s in fares.values()):
            out[(o, d)] = fares
    return out


def leg_summary(fares):
    """{cabin: {'min', 'day', 'typical', 'days'}} for one leg; a cabin with no fare is left out."""
    return {c: dict(min=float(s.min()), day=s.idxmin(), typical=float(s.median()), days=len(s)) for c, s in fares.items() if len(s)}


def best_trips(fares, dest_back, hotel_night, cabin="economy", nights=NIGHTS):
    """Cheapest whole trip (return flights + hotel every night) per stay length: {nights: row}, from ui.round_trips and trip_cost.trip_costs.
    Without hotel prices, flights only (hotel = None)."""
    frame = lambda f: pd.DataFrame({c: f[c] for c in CABINS if c in f})
    out, back = frame(fares), frame(dest_back)
    if cabin not in out or cabin not in back:
        return {}
    trips = round_trips(out.reindex(columns=list(CABINS)), back.reindex(columns=list(CABINS)), list(nights), cabins=(cabin,))
    res = {}
    for n in nights:
        t = trips[trips.nights == n]
        if t.empty:
            continue
        if hotel_night is not None and len(hotel_night):
            tc = trip_costs(t, hotel_night)
            if len(tc):
                res[n] = tc.iloc[0].to_dict()
                continue
        r = t.iloc[0]
        res[n] = dict(depart=r.depart, back=r.back, nights=n, flights=float(r.total), hotel=None, total=float(r.total))
    return res


def briefing(summary, trips, weekday):
    """At most five plain sentences anyone can act on: the best deal, the best-value Frankfurt trip, the cheapest weekday to leave Athens and a
    cheap business upgrade, in that order. summary: {(o, d): leg_summary}; trips: {nights: row}; weekday: {(o, d): weekday_stats frame} for economy."""
    say = []
    deals = [((o, d), s["economy"]) for (o, d), s in summary.items() if s.get("economy") and s["economy"]["typical"] > 0]
    if deals:
        (o, d), e = max(deals, key=lambda kv: 1 - kv[1]["min"] / kv[1]["typical"])
        if e["min"] <= e["typical"] * 0.7:
            say.append(f"Best deal: {o} → {d} economy at €{e['min']:,.0f} on {pd.Timestamp(e['day']):%a %d %b}, "
                       f"{1 - e['min'] / e['typical']:.0%} below its typical €{e['typical']:,.0f}.")
    if trips:
        n, r = min(trips.items(), key=lambda kv: kv[1]["total"] / kv[0])
        hotel = f" + €{r['hotel']:,.0f} hotel" if r.get("hotel") is not None else ""
        say.append(f"Best value in Frankfurt: {n} nights, {pd.Timestamp(r['depart']):%a %d %b} → {pd.Timestamp(r['back']):%a %d %b}, "
                   f"€{r['total']:,.0f} in all (€{r['flights']:,.0f} flights{hotel}), €{r['total'] / n:,.0f} a night.")
    for (o, d), w in weekday.items():
        if len(w) >= 5 and w["mean"].max() - w["mean"].min() >= 25:
            say.append(f"Leaving {city(o)} for {d}, {FULL[w['mean'].idxmin()]}s are cheapest: €{w['mean'].min():,.0f} on average, "
                       f"€{w['mean'].max() - w['mean'].min():,.0f} less than {FULL[w['mean'].idxmax()]}s.")
    for (o, d), s in summary.items():
        a, b = s.get("business"), s.get("economy")
        if a and b and a["min"] - b["min"] <= 120:
            say.append(f"{o} → {d}: business from €{a['min']:,.0f} is only €{a['min'] - b['min']:,.0f} more than the cheapest economy.")
    return say[:5]


def facts(summary, fares, trips, hotel_night, awards, today):
    """Everything the expert may use, as compact text: per-leg summaries, then every day's fares and miles as CSV-like lines. Small enough
    (~10k tokens for 90 days) that nothing has to be left out."""
    lines = [f"Today: {today:%a %d %b %Y}. All prices in EUR, one adult, AEGEAN nonstop, one way, from Google Flights. "
             "Award miles are AEGEAN Miles+Bonus, one way."
             + (f" Hotel in Frankfurt: {hotel_night.name}, one night (Google Hotels)." if hotel_night is not None and len(hotel_night) else "")]
    for (o, d), s in summary.items():
        for c, v in s.items():
            lines.append(f"{o}-{d} {c}: cheapest €{v['min']:.0f} on {v['day']}, typical (median) €{v['typical']:.0f}, {v['days']} days priced")
    for n, r in sorted(trips.items()):
        lines.append(f"Best ATH-FRA-ATH economy trip of {n} nights: depart {r['depart']}, back {r['back']}, flights €{r['flights']:.0f}, "
                     f"hotel {'€%.0f' % r['hotel'] if r.get('hotel') is not None else 'unknown'}, total €{r['total']:.0f}")
    lines.append("\nday,leg,economy_eur,business_eur,economy_miles,business_miles")
    for (o, d), f in fares.items():
        miles = awards.get((o, d), pd.DataFrame())
        for day in sorted(set(f["economy"].index) | set(f["business"].index)):
            row = [f[c].get(day) for c in CABINS] + [miles[c].get(day) if c in miles else None for c in CABINS]
            lines.append(",".join([day, f"{o}-{d}"] + ["" if v is None or pd.isna(v) else f"{v:.0f}" for v in row]))
    if hotel_night is not None and len(hotel_night):
        lines.append("\ncheck_in,hotel_one_night_eur")
        lines += [f"{day},{p:.0f}" for day, p in hotel_night.items()]
    return "\n".join(lines)


def collect(db, today):
    """Everything the Overview shows: (fares, summary, trips, weekday, hotel_night, awards)."""
    fares = legs(db, today)
    summary = {k: leg_summary(v) for k, v in fares.items()}
    weekday = {k: weekday_stats(v["economy"]) for k, v in fares.items() if len(v["economy"])}
    hotels = searched_hotels(db, "FRA")  # the Frankfurt hotel searched most recently on the Hotels page, not a fixed default
    hotel_night = None
    if hotels:
        h = hotels[0]
        hotel_night = night_prices(db, h["query"], h["filt"], h["adults"], today, today + timedelta(DAYS + 14))
        hotel_night.name = f"{h['name']} ({h['adults']} adults)"  # carried with the prices, so pages can say which hotel
    trips = best_trips(fares[("ATH", "FRA")], fares[("FRA", "ATH")], hotel_night) if ("ATH", "FRA") in fares and ("FRA", "ATH") in fares else {}
    awards = {}
    for o, d in fares:
        miles, _, _ = load_award(db, o, d, True, today + timedelta(1), today + timedelta(DAYS))
        if len(miles):
            awards[(o, d)] = miles
    return fares, summary, trips, weekday, hotel_night, awards
