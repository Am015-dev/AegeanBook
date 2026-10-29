"""Whole-trip cost: the return flights plus the hotel for every night of the stay, for each departure date and length of stay. Pure
functions over what the app already collects (round trips from ui.round_trips, one-night hotel prices from the `hotel` table)."""
import pandas as pd

COLUMNS = ["depart", "back", "nights", "flights", "hotel", "total"]


def hotel_options(db):
    """Hotels that have one-night prices saved: [{'query', 'filt', 'adults', 'days'}], the best covered first."""
    rows = db.execute("SELECT query, filt, adults, COUNT(DISTINCT day) FROM hotel WHERE nights=1 AND price IS NOT NULL "
                      "GROUP BY query, filt, adults ORDER BY COUNT(DISTINCT day) DESC").fetchall()
    return [dict(query=q, filt=f, adults=a, days=n) for q, f, a, n in rows]


def night_prices(db, query, filt, adults, start, end):
    """Price of one night by check-in day (iso), the newest saved price of each day; empty when there are none."""
    df = pd.read_sql("SELECT day, price FROM hotel WHERE query=? AND filt=? AND adults=? AND nights=1 AND price IS NOT NULL "
                     "AND day BETWEEN ? AND ? ORDER BY fetched", db, params=(query, filt, int(adults), start.isoformat(), end.isoformat()))
    return df.drop_duplicates("day", keep="last").set_index("day").price.sort_index() if len(df) else pd.Series(dtype=float)


def trip_costs(trips_cabin, night_price):
    """One row per round trip whose every night has a hotel price: flights + hotel = total, cheapest first. trips_cabin: one cabin's rows of
    ui.round_trips (depart, back, nights, total); night_price: Series by iso check-in day."""
    rows = []
    for t in trips_cabin.itertuples():
        stay = [(pd.Timestamp(t.depart) + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(int(t.nights))]
        prices = [night_price.get(d) for d in stay]
        if any(p is None or pd.isna(p) for p in prices):
            continue  # one night without a price would make the total a guess
        hotel = float(sum(prices))
        rows.append(dict(depart=t.depart, back=t.back, nights=int(t.nights), flights=float(t.total), hotel=hotel, total=float(t.total) + hotel))
    return pd.DataFrame(rows, columns=COLUMNS).sort_values("total", kind="stable").reset_index(drop=True)
