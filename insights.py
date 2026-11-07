"""Insights: what the saved fares say about WHEN to fly and WHEN to book, each with one plain sentence. Pure functions over the fares the app
already keeps (the `prices` table for the next 90 days, data/history_prices.csv for how a fare moved). A new module on purpose: Streamlit Cloud
can run a new page against older, already loaded modules."""
import pandas as pd
import plotly.graph_objects as go

ECONOMY, BUSINESS, MUTED, INK, GRID, GOOD = "#2563C9", "#B8741A", "#6E6E73", "#1D1D1F", "#E5E5EA", "#0E7A53"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
FULL = dict(zip(DAYS, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]))


def routes(db, nonstop):
    """[(orig, dest)] with saved fares, the best covered first."""
    rows = db.execute("SELECT orig, dest, COUNT(DISTINCT day) FROM prices WHERE nonstop=? AND price IS NOT NULL GROUP BY orig, dest "
                      "ORDER BY COUNT(DISTINCT day) DESC, orig, dest", (int(nonstop),)).fetchall()
    return [(o, d) for o, d, _ in rows]


def cash_by_day(db, orig, dest, cabin, nonstop, start, end):
    """The newest saved fare of each day (iso day -> euros); empty when there are none."""
    df = pd.read_sql("SELECT day, price FROM prices WHERE orig=? AND dest=? AND cabin=? AND nonstop=? AND price IS NOT NULL AND day BETWEEN ? AND ? "
                     "ORDER BY fetched", db, params=(orig, dest, cabin, int(nonstop), start.isoformat(), end.isoformat()))
    return df.drop_duplicates("day", keep="last").set_index("day").price.sort_index() if len(df) else pd.Series(dtype=float)


def weekday_stats(cash):
    """Average fare by weekday of departure: DataFrame indexed Mon..Sun with mean and days (weekdays seen on fewer than 2 days are left out)."""
    if cash.empty:
        return pd.DataFrame(columns=["mean", "days"])
    g = cash.groupby(pd.to_datetime(cash.index).strftime("%a")).agg(["mean", "count"]).rename(columns={"count": "days"})
    return g[g.days >= 2].reindex([d for d in DAYS if d in g.index])


def weekday_takeaway(stats, label):
    """One sentence: the cheapest weekday and by how much it beats the dearest, or that there is no real pattern."""
    if len(stats) < 3:
        return "Not enough days saved yet to compare weekdays."
    cheap, dear = stats["mean"].idxmin(), stats["mean"].idxmax()
    gap, spread = stats["mean"].max() - stats["mean"].min(), (stats["mean"].max() - stats["mean"].min()) / stats["mean"].mean()
    if spread < 0.12:
        return f"No strong weekday pattern on {label}: every weekday averages within {spread:.0%} of the others."
    return f"{FULL[cheap]} is the cheapest day to fly {label}: €{stats.loc[cheap, 'mean']:.0f} on average, €{gap:.0f} less than {FULL[dear]}."


def ahead_stats(cash, today):
    """Median fare by how many weeks ahead the flight is (week 1 = the next 7 days): DataFrame indexed by week with median and days (weeks
    with fewer than 3 days are left out)."""
    if cash.empty:
        return pd.DataFrame(columns=["median", "days"])
    week = ((pd.to_datetime(cash.index).date - pd.Series([today] * len(cash)).values).astype("timedelta64[D]").astype(int) - 1) // 7 + 1
    g = cash.groupby(week).agg(["median", "count"]).rename(columns={"count": "days"})
    return g[g.days >= 3]


def ahead_takeaway(stats, label):
    """One sentence: how much dearer the next two weeks are than the rest, or that there is no last-minute premium."""
    early, later = stats[stats.index <= 2], stats[stats.index > 2]
    if early.empty or len(later) < 2:
        return "Not enough weeks saved yet to say how far ahead to book."
    e, l = float((early["median"] * early.days).sum() / early.days.sum()), float(later["median"].median())
    if e >= 1.3 * l:
        return (f"Flights in the next 2 weeks cost more on {label} (€{e:.0f}) than later on (about €{l:.0f}): if your dates are flexible, "
                "fly later, and book well before the last two weeks.")
    if e <= 0.8 * l:
        return f"Flights in the next 2 weeks are cheaper than later on {label}: €{e:.0f} against about €{l:.0f}."
    return f"No big last-minute premium on {label}: the next 2 weeks cost about €{e:.0f}, later fares about €{l:.0f}."


def destinations(db, orig, cabin, nonstop, start, end):
    """Cheapest and typical fare to every destination from `orig`: DataFrame dest, cheapest, typical, days (needs the saved fares)."""
    df = pd.read_sql("SELECT dest, day, price FROM prices WHERE orig=? AND cabin=? AND nonstop=? AND price IS NOT NULL AND day BETWEEN ? AND ? ORDER BY fetched",
                     db, params=(orig, cabin, int(nonstop), start.isoformat(), end.isoformat())).drop_duplicates(["dest", "day"], keep="last")
    if df.empty:
        return pd.DataFrame(columns=["dest", "cheapest", "typical", "days"])
    g = df.groupby("dest").price.agg(cheapest="min", typical="median", days="count").reset_index()
    return g.sort_values("typical").reset_index(drop=True)


def destination_takeaway(g, orig):
    if len(g) < 2:
        return ""
    a, b = g.iloc[0], g.iloc[1]
    return f"From {orig}, a typical fare to {a.dest} is €{a.typical:.0f}, €{b.typical - a.typical:.0f} less than to {b.dest} (€{b.typical:.0f})."


def moves(hist, orig, dest, cabin):
    """From data/history_prices.csv: {iso day: its recorded fares as a DataFrame of fetched and price} for the days whose fare was recorded at
    least twice, i.e. that moved. hist is the history file as a DataFrame (empty when there is none)."""
    if hist.empty:
        return {}
    h = hist[(hist.orig == orig) & (hist.dest == dest) & (hist.cabin == cabin) & hist.price.notna()].sort_values("fetched")
    return {d: g[["fetched", "price"]] for d, g in h.groupby("day") if len(g) >= 2}


def history_sentence(frame, day_label):
    first, last = frame.iloc[0], frame.iloc[-1]
    diff = last.price - first.price
    way = "up" if diff > 0 else "down"
    return (f"The fare for {day_label} went {way}: €{first.price:.0f} on {first.fetched[:10]} → €{last.price:.0f} on {last.fetched[:10]} "
            f"({'+' if diff > 0 else '−'}€{abs(diff):.0f}).")


def _layout(fig, h=280):
    fig.update_layout(height=h, margin=dict(l=28, r=8, t=16, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color=MUTED),
                      showlegend=False, xaxis=dict(showgrid=False, linecolor=GRID), yaxis=dict(tickprefix="€", gridcolor=GRID, zeroline=False, rangemode="tozero"))
    return fig


def weekday_chart(stats, colour):
    best = stats["mean"].idxmin()
    fig = go.Figure(go.Bar(x=list(stats.index), y=stats["mean"], marker_color=[GOOD if d == best else colour for d in stats.index],
                           text=[f"€{v:.0f}" for v in stats["mean"]], textposition="outside", cliponaxis=False,
                           hovertemplate="%{x}: €%{y:.0f} on average<extra></extra>"))
    return _layout(fig)


def ahead_chart(stats, colour):
    fig = go.Figure(go.Bar(x=[f"{w}" for w in stats.index], y=stats["median"], marker_color=colour, text=[f"€{v:.0f}" for v in stats["median"]],
                           textposition="outside", cliponaxis=False, hovertemplate="Week %{x}: €%{y:.0f} typical<extra></extra>"))
    fig.update_xaxes(title_text="Weeks from today")
    return _layout(fig)


def destination_chart(g):
    fig = go.Figure()
    fig.add_bar(x=g.dest, y=g.cheapest, name="Cheapest", marker_color=GOOD, text=[f"€{v:.0f}" for v in g.cheapest], textposition="outside", cliponaxis=False)
    fig.add_bar(x=g.dest, y=g.typical, name="Typical", marker_color=ECONOMY, text=[f"€{v:.0f}" for v in g.typical], textposition="outside", cliponaxis=False)
    fig.update_layout(barmode="group", showlegend=True, legend=dict(orientation="h", x=0, y=1.15, font=dict(color=INK)))
    return _layout(fig).update_layout(showlegend=True)


def history_chart(frame, colour):
    fig = go.Figure(go.Scatter(x=pd.to_datetime(frame.fetched), y=frame.price, mode="lines+markers", line=dict(shape="hv", width=2, color=colour),
                               marker=dict(size=8, color=colour), hovertemplate="€%{y:.0f}<extra></extra>"))
    return _layout(fig)
