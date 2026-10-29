"""Database: the permanent history kept in the repo, every cash fare, award miles value and hotel price ever recorded. The app's own cache
only keeps a few days; these files (data/history_*.csv, data/award_history.csv) are never trimmed."""
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui import BUSINESS, ECONOMY, GRID, INK, MUTED, excel_bytes, kpis

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
COLOURS = {"economy": ECONOMY, "business": BUSINESS, "google": ECONOMY, "booking": "#0E9F6E"}
SETS = {
    "Cash fares": dict(file="history_prices.csv", value="price", unit="€", key=["day", "orig", "dest", "cabin", "nonstop"], series="cabin",
                       route=lambda d: d["orig"] + " → " + d["dest"], day="Departure day", what="AEGEAN fare, from Google Flights"),
    "Award miles": dict(file="award_history.csv", value="miles", unit=" miles", key=["day", "orig", "dest", "cabin", "nonstop"], series="cabin",
                        route=lambda d: d["orig"] + " → " + d["dest"], day="Departure day", what="AEGEAN award miles, lowest of the day"),
    "Hotels": dict(file="history_hotel.csv", value="price", unit="€", key=["source", "day", "query", "filt", "nights", "adults"], series="source",
                   route=lambda d: d["query"].astype(str) + " · " + d["filt"].astype(str), day="Check-in day", what="Price per night"),
}


@st.cache_data(ttl=600, show_spinner=False)
def load(file, mtime):
    """One history file as a frame with only the changes in it (older files also hold repeated snapshots)."""
    path = os.path.join(DATA, file)
    if not os.path.exists(path) or not os.path.getsize(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def changes_only(df, key, value):
    df = df.sort_values("fetched", kind="stable")
    g = df.groupby(key, dropna=False)
    first = g.cumcount() == 0
    prev = g[value].shift()
    same = (df[value] == prev) | (df[value].isna() & prev.isna())
    return df[first | ~same]


def overview():
    items = []
    for name, cfg in SETS.items():
        path = os.path.join(DATA, cfg["file"])
        df = load(cfg["file"], os.path.getmtime(path) if os.path.exists(path) else 0)
        if df.empty:
            items.append((name, "—", "No history yet", MUTED))
            continue
        seen = pd.to_datetime(df.fetched)
        n = seen.dt.date.nunique()
        items.append((name, f"{len(df):,} rows", f"{seen.min():%d %b %Y} to {seen.max():%d %b %Y} · {n} day{'s' if n != 1 else ''} recorded",
                      ECONOMY if name != "Award miles" else BUSINESS))
    kpis(items)


def section(name, cfg):
    path = os.path.join(DATA, cfg["file"])
    df = load(cfg["file"], os.path.getmtime(path) if os.path.exists(path) else 0)
    if df.empty:
        st.info("No history yet. It grows with every daily run and nothing in it is ever deleted.")
        return
    df = changes_only(df, cfg["key"], cfg["value"]).assign(route=lambda d: cfg["route"](d), series=lambda d: d[cfg["series"]].astype(str))
    c = st.columns([2, 2, 1])
    route = c[0].selectbox("Route" if name != "Hotels" else "Hotel", sorted(df.route.unique()), key=f"db-route-{name}")
    kinds = sorted(df.series.unique())
    picked = c[1].multiselect("Cabin" if cfg["series"] == "cabin" else "Source", kinds, default=kinds, key=f"db-series-{name}")
    view = df[(df.route == route) & df.series.isin(picked)]
    if "nonstop" in view and view.nonstop.nunique() > 1:
        only = c[2].toggle("Nonstop only", value=True, key=f"db-nonstop-{name}")
        view = view[view.nonstop == 1] if only else view
    if view.empty:
        st.info("Nothing recorded for this selection.")
        return
    st.caption(f"{cfg['what']}. A row is stored when the value changes, so a value that stays the same is one row. "
               f"{len(view):,} rows for this selection.")
    days = sorted(view.day.unique())
    day = st.selectbox(cfg["day"], days, key=f"db-day-{name}", help="See how the value for one day moved between the days it was recorded.")
    fig = go.Figure()
    for s, g in view[view.day == day].groupby("series"):
        g = g.sort_values("fetched")
        colour = COLOURS.get(s, ECONOMY)
        fig.add_scatter(x=pd.to_datetime(g.fetched), y=g[cfg["value"]], name=s.capitalize(), mode="lines+markers",
                        line=dict(shape="hv", width=2, color=colour), marker=dict(size=8, color=colour),
                        hovertemplate=f"{s.capitalize()}: {'€' if cfg['unit'] == '€' else ''}%{{y:,.0f}}{'' if cfg['unit'] == '€' else ' miles'}<extra></extra>")
    fig.update_layout(height=300, margin=dict(l=28, r=8, t=30, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color=MUTED),
                      hovermode="x unified", legend=dict(orientation="h", x=0, y=1.15, font=dict(color=INK)), xaxis=dict(showgrid=False, linecolor=GRID),
                      yaxis=dict(tickprefix="€" if cfg["unit"] == "€" else "", ticksuffix="" if cfg["unit"] == "€" else " mi", tickformat=",",
                                 gridcolor=GRID, zeroline=False))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    table = view.sort_values("fetched", ascending=False).rename(columns={"fetched": "Recorded", "day": cfg["day"], "route": "Route",
                                                                           "series": "Cabin" if cfg["series"] == "cabin" else "Source",
                                                                           cfg["value"]: "Price €" if cfg["unit"] == "€" else "Miles"})
    show = ["Recorded", cfg["day"], "Route", "Cabin" if cfg["series"] == "cabin" else "Source", "Price €" if cfg["unit"] == "€" else "Miles"]
    st.dataframe(table[show].head(1000), hide_index=True, width="stretch")
    if len(table) > 1000:
        st.caption("The newest 1,000 rows are shown; the download has all of them.")
    st.download_button("Download this selection (Excel)", excel_bytes(table[show].reset_index(drop=True)), f"aegean_{name.lower().replace(' ', '_')}.xlsx",
                       key=f"db-dl-{name}")


st.markdown('<div class="route"><span class="code">Database</span></div>'
            '<p class="sub">Every price and miles value recorded so far, kept in GitHub. Nothing is deleted after 5 days.</p>', unsafe_allow_html=True)
overview()
for tab, (name, cfg) in zip(st.tabs(list(SETS)), SETS.items()):
    with tab:
        section(name, cfg)
