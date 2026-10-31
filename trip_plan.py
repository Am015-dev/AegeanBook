"""Trip planner for the expert: the traveller's own choices (destination, month, cabin, cash or miles, one way or return, hotel, weekdays)
turned into a focused question plus only the data that answers it, with the candidate trips already worked out, so the AI explains
"when and why" instead of searching 90 days of four routes on its own. Pure functions; the form is on views/expert.py.
A new module on purpose: Streamlit Cloud can run a page against older, already loaded modules."""
import csv
import os
import unicodedata
from datetime import date, timedelta

import pandas as pd

from award_view import load_award
from insights import DAYS, cash_by_day
from trip_cost import night_prices

VERSION = 3  # pages reload this module when Streamlit Cloud still has an older copy loaded (it keeps modules across redeploys)
HOME = "ATH"
PAY = {"cash": "Cash", "miles": "Miles", "both": "Cash or miles"}
LEGS = ["out_cash", "out_miles", "out_taxes", "back_cash", "back_miles", "back_taxes"]  # each direction of a return, for mixed trips


def _plain(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


# ponytail: OurAirports names the town an airport is in, which for a few big ones is a suburb nobody books by; add more here as they turn up
CITY = {"ATH": "Athens", "MXP": "Milan", "LIN": "Milan", "BGY": "Milan", "CIA": "Rome", "BVA": "Paris", "ORY": "Paris", "EWR": "New York"}


def city(iata):
    """The airport's city (FRA -> Frankfurt am Main, ATH -> Athens, CDG -> Paris), or the code itself."""
    if iata in CITY:
        return CITY[iata]
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "airports.csv"), encoding="utf-8") as f:
        name = next((r["city"] for r in csv.DictReader(f) if r["iata"] == iata and r["city"]), iata)
    return name.split(" (")[0].split(",")[0]  # "Paris (Roissy-en-France, Val-d'Oise)" -> Paris, "London, Essex" -> London


def origins(db):
    """Airports with fares saved from them, the best covered first: any route searched on Flights or tracked by the daily run."""
    rows = db.execute("SELECT orig, COUNT(DISTINCT day) n FROM prices WHERE nonstop=1 AND price IS NOT NULL GROUP BY orig "
                      "ORDER BY n DESC, orig").fetchall()
    return [o for o, _ in rows]


def destinations(db, orig=HOME):
    """Airports with fares saved from `orig`, the best covered first."""
    rows = db.execute("SELECT dest, COUNT(DISTINCT day) n FROM prices WHERE orig=? AND nonstop=1 AND price IS NOT NULL GROUP BY dest "
                      "ORDER BY n DESC, dest", (orig,)).fetchall()
    return [d for d, _ in rows]


def months(today, last):
    """[(label, first day, last day)]: the next 30 days, then every calendar month up to `last` (the last day with fares)."""
    out = [("Next 30 days", today + timedelta(1), min(today + timedelta(30), last))]
    m = date(today.year, today.month, 1)
    while m <= last:
        nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        a, b = max(m, today + timedelta(1)), min(nxt - timedelta(1), last)
        if a <= b:
            out.append((f"{m:%B %Y}", a, b))
        m = nxt
    return out


def hotels(db, dest):
    """Hotels searched on the Hotels page that are in the destination's city and have one-night prices, the most recent search first:
    [{'query', 'filt', 'adults', 'name', 'days', 'last'}]. Matching is by the city's name in the search, so 'Steigenberger Frankfurt'
    counts for FRA and 'Baur au Lac Zürich' for ZRH."""
    want = _plain(city(dest)).split()[0]
    rows = db.execute("SELECT query, filt, adults, MAX(hotel), COUNT(DISTINCT day), MAX(fetched) FROM hotel WHERE nights=1 AND price IS NOT NULL "
                      "GROUP BY query, filt, adults ORDER BY MAX(fetched) DESC").fetchall()
    return [dict(query=q, filt=f, adults=a, name=n or f or q, days=d, last=l) for q, f, a, n, d, l in rows
            if want in _plain(f"{q} {f} {n}")]


def _day(d):
    return f"{pd.Timestamp(d):%a %d %b}"


def _leg(db, o, d, cabin, start, end):
    """DataFrame by iso day: cash, miles, taxes for one leg and cabin (NaN where missing)."""
    miles, taxes, _ = load_award(db, o, d, True, start, end)
    pick = lambda f: f[cabin] if cabin in f else pd.Series(dtype=float)
    df = pd.concat({"cash": cash_by_day(db, o, d, cabin, True, start, end), "miles": pick(miles), "taxes": pick(taxes)}, axis=1)
    return df.sort_index().astype(float)


def candidates(db, dest, start, end, cabin, nights, weekdays, hotel, pay="both", orig=HOME):
    """The trips to choose from, cheapest first, as a DataFrame. One way: one row per departure day. Return: one row per departure day
    with the flight back `nights` later, plus the hotel for every night when a hotel is chosen. Columns: depart, back, cash (flights),
    miles, taxes, hotel, total_cash (flights + hotel), value_per_1000 (euros of cash fare saved per 1,000 miles, after award taxes).
    Paying with miles sorts by miles, otherwise by total cash. Returns also keep each direction's cash, miles and taxes, so a trip can
    use miles one way and cash the other (award seats rarely line up both ways)."""
    out = _leg(db, orig, dest, cabin, start, end)
    out = out[[DAYS[pd.Timestamp(d).weekday()] in weekdays for d in out.index]] if weekdays else out
    if not nights:
        t = out.assign(depart=out.index, back=None, hotel=float("nan"))
    else:
        back = _leg(db, dest, orig, cabin, start + timedelta(nights), end + timedelta(nights))
        night = night_prices(db, hotel["query"], hotel["filt"], hotel["adults"], start, end + timedelta(nights)) if hotel else pd.Series(dtype=float)
        rows = []
        for d, r in out.iterrows():
            b = (pd.Timestamp(d) + pd.Timedelta(days=nights)).strftime("%Y-%m-%d")
            if b not in back.index:
                continue
            k = back.loc[b]
            stay = [(pd.Timestamp(d) + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(nights)]
            h = sum(night[s] for s in stay) if hotel and all(s in night.index for s in stay) else float("nan")
            rows.append(dict(depart=d, back=b, cash=r.cash + k.cash, miles=r.miles + k.miles, taxes=r.taxes + k.taxes, hotel=h,
                             out_cash=r.cash, out_miles=r.miles, out_taxes=r.taxes, back_cash=k.cash, back_miles=k.miles, back_taxes=k.taxes))
        t = pd.DataFrame(rows, columns=["depart", "back", "cash", "miles", "taxes", "hotel"] + LEGS)
    if t.empty:
        return t
    t["total_cash"] = t.cash + t.hotel.fillna(0)
    t["value_per_1000"] = ((t.cash - t.taxes.fillna(0)) / t.miles * 1000).round(2)
    t = t[["depart", "back", "cash", "miles", "taxes", "hotel", "total_cash", "value_per_1000"] + [c for c in LEGS if c in t]]
    return t.sort_values(["miles", "depart"] if pay == "miles" else ["total_cash", "depart"]).reset_index(drop=True)


def question(dest, label, cabin, pay, nights, weekdays, hotel, orig=HOME):
    """The traveller's choices as the question they would ask."""
    trip = f"a {nights}-night return trip" if nights else "a one-way flight"
    days = f", leaving on a {'/'.join(weekdays)}" if weekdays and len(weekdays) < 7 else ""
    stay = f", staying at {hotel['name']} ({hotel['adults']} adults)" if hotel else ""
    how = {"cash": "paying cash", "miles": "paying with miles", "both": "with cash or miles, whichever is better"}[pay]
    return (f"{label}: I want {trip} from {city(orig)} ({orig}) to {city(dest)} ({dest}) in {cabin}{days}{stay}, {how}. "
            "When is the best time to fly, and why?")


def facts(t, dest, label, start, end, cabin, pay, nights, hotel, today, orig=HOME):
    """Only the data that answers the question, as text: the rules of the comparison, then every candidate trip as CSV."""
    if t.empty:
        return ""
    lines = [f"Today: {today:%a %d %b %Y}. Prices in EUR for one adult, AEGEAN nonstop, from Google Flights (cash) and AEGEAN Miles+Bonus "
             f"(award miles plus award taxes). Period: {label}, departures {_day(start)} to {_day(end)}. Cabin: {cabin}. "
             f"Trip: {f'return, {nights} nights' if nights else 'one way'}, {city(orig)} ({orig}) to {city(dest)} ({dest})."]
    if hotel:
        lines.append(f"Hotel: {hotel['name']}, one-night prices from Google Hotels for {hotel['adults']} adults, added up night by night.")
    lines.append(f"Payment the traveller asked about: {PAY[pay]}. value_per_1000 = euros of cash fare saved per 1,000 miles after award "
                 "taxes; above about €10 per 1,000 miles is a good use of miles. Empty cells mean not collected or no seats"
                 + ("; award taxes are not collected yet, so value_per_1000 leaves them out: say so when recommending miles."
                    if t.taxes.isna().all() else "."))
    lines.append(f"{len(t)} candidate trips, {'fewest miles' if pay == 'miles' else 'cheapest total cash'} first.")
    if t.total_cash.notna().any():
        c = t.loc[t.total_cash.idxmin()]
        lines.append(f"Cheapest in cash: depart {c.depart}, €{c.total_cash:,.0f}.")
    if t.miles.notna().any():
        best = t.loc[t.value_per_1000.idxmax()] if t.value_per_1000.notna().any() else None
        low = t.loc[t.miles.idxmin()]
        lines.append(f"Fewest miles: depart {low.depart}, {low.miles:,.0f} miles"
                     + (f"; best value for miles: depart {best.depart}, €{best.value_per_1000:.2f} per 1,000 miles." if best is not None else "."))
    if pay != "cash" and nights:
        lines += mixed(t)
    cols = ["depart", "back", "cash", "miles", "taxes", "hotel", "total_cash", "value_per_1000"] + [c for c in LEGS if c in t]
    lines.append("\n" + ",".join(cols))
    for r in t.itertuples():
        lines.append(",".join("" if v is None or (isinstance(v, float) and pd.isna(v)) else
                              (f"{v:.2f}" if c == "value_per_1000" else f"{v:.0f}" if isinstance(v, float) else str(v))
                              for c, v in zip(cols, (getattr(r, c) for c in cols))))
    return "\n".join(lines)


def mixed(t, top=3):
    """Sentences for the best trips that use miles one way and cash the other: the least cash paid first (the other leg plus the hotel),
    then the most euros of cash fare saved per 1,000 miles."""
    out = []
    for leg, other, name in (("out", "back", "Miles out, cash back"), ("back", "out", "Cash out, miles back")):
        m = t.dropna(subset=[f"{leg}_miles", f"{leg}_cash", f"{other}_cash"])
        if not len(m):
            out.append(f"{name}: no trip in this period has award seats on that leg.")
            continue
        value = (m[f"{leg}_cash"] - m[f"{leg}_taxes"].fillna(0)) / m[f"{leg}_miles"] * 1000
        paid = m[f"{other}_cash"] + m.hotel.fillna(0)
        for i in pd.DataFrame({"paid": paid, "value": -value}).sort_values(["paid", "value"]).index[:top]:
            r = m.loc[i]
            hotel = f" + €{r.hotel:,.0f} hotel" if pd.notna(r.hotel) else ""
            out.append(f"{name}: depart {r.depart}, back {r.back}: {r[f'{leg}_miles']:,.0f} miles replace a €{r[f'{leg}_cash']:,.0f} fare "
                       f"(€{value[i]:.2f} per 1,000 miles); you pay €{r[f'{other}_cash']:,.0f} in cash the other way{hotel}.")
    return out
