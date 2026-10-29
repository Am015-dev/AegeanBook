"""How collected AEGEAN award miles are shown: the headline numbers, a chart, and one tidy table. Kept out of ui.py on purpose: Streamlit
Cloud can run a new page against an older, already loaded ui.py, and a new module is always imported fresh."""
import pandas as pd
import plotly.graph_objects as go

ECONOMY, BUSINESS, MUTED, INK, GRID = "#2563C9", "#B8741A", "#6E6E73", "#1D1D1F", "#E5E5EA"


def best_award(miles, collected):
    """(value, sub) for the lowest award miles of one cabin. miles: Series by iso day, NaN where the day has no award seats;
    collected: whether any award data exists for this route and range (no data is not the same as no seats)."""
    s = miles.dropna()
    if not len(s):
        return ("—", "No award seats on these days" if collected else "Not collected yet for this route")
    low = s.min()
    at = s[s == low]
    when = f"{pd.Timestamp(at.index[0]):%a %d %b}"
    if len(at) == len(s) and len(s) > 1:  # no need to say it twice
        return f"{int(low):,} miles", f"{when} · same on all {len(s)} days with seats"
    more = f" · {len(at)} days at this level" if len(at) > 1 else ""
    return f"{int(low):,} miles", f"{when}{more} · {len(s)} days with seats"


def miles_table(econ, biz):
    """One row per day that has award seats in at least one cabin; '—' where a cabin has none."""
    seats = pd.DataFrame({"economy": econ, "business": biz}).dropna(how="all")
    fmt = lambda v: f"{int(v):,}" if pd.notna(v) else "—"
    return pd.DataFrame({"Date": [f"{pd.Timestamp(d):%a %d %b}" for d in seats.index],
                         "Economy miles": seats.economy.map(fmt), "Business miles": seats.business.map(fmt)})


def miles_chart(econ, biz):
    """Lowest award miles per day; a day without seats is a gap, not a zero."""
    x = pd.to_datetime(econ.index)
    fig = go.Figure()
    for label, colour, y in (("Economy", ECONOMY, econ), ("Business", BUSINESS, biz)):
        fig.add_scatter(x=x, y=y, name=label, mode="lines+markers", connectgaps=False, line=dict(color=colour, width=2),
                        marker=dict(size=8, color=colour, line=dict(color="#fff", width=2)),
                        hovertemplate=f"{label}: %{{y:,.0f}} miles<extra></extra>")
    fig.update_layout(height=320, margin=dict(l=28, r=8, t=40, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(color=MUTED), hovermode="x unified", legend=dict(orientation="h", x=0, y=1.15, font=dict(color=INK)),
                      xaxis=dict(tickformat="%d %b", showgrid=False, linecolor=GRID),
                      yaxis=dict(ticksuffix=" mi", tickformat=",", automargin=True, gridcolor=GRID, zeroline=False, rangemode="tozero"))
    return fig
