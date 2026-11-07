import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from db import RETENTION_DAYS, connect
from layout import page_head
from ui import ECONOMY, FONT, GRID, MUTED, TIER_COLORS, kpis

db = connect()
page_head("Statistics", f"Every price update of the last 30 days, and what is saved now (prices are kept {RETENTION_DAYS} days).")

runs = pd.read_sql("SELECT * FROM runs ORDER BY started DESC", db)
if runs.empty:
    st.info("No updates yet. Press **Update prices** on Flights or Hotels and the numbers appear here.")
    st.stop()

answered = runs.ok + runs["empty"]
lookups = runs.ok + runs["empty"] + runs.failed
rate = answered.sum() / max(1, lookups.sum())
names = {"flights": "Flights searches", "hotel": "Hotel searches", "daily": "Morning runs"}
by_page = [f"{names.get(page, 'Other searches')} {n}" for page, n in runs.page.map(lambda x: x if x in names else "other").value_counts()
           .reindex(["flights", "hotel", "daily", "other"]).dropna().astype(int).items()]  # only kinds that happened, none as "Flights 0"
timed = runs.seconds[runs.seconds > 0]  # morning runs record no duration, and a 0 is "not measured", never "0 seconds"
items = [("Updates", f"{len(runs)}", " · ".join(by_page), ECONOMY),
         ("Lookups", f"{int(lookups.sum()):,}", f"{int(runs.ok.sum()):,} priced · {int(runs['empty'].sum()):,} nothing found", MUTED),
         ("Answered", f"{rate:.0%}", f"{int(runs.failed.sum()):,} failed (Google or Xotelo did not answer)",
          ECONOMY if rate >= 0.9 else TIER_COLORS["high"])]
if len(timed):
    items.append(("Typical search time", (f"{timed.median():.0f} s" if timed.median() >= 0.5 else "under 1 s"),
                  (f"slowest {timed.max():.0f} s" if timed.max() >= 0.5 else "slowest under 1 s") + " (searches started on this site)", MUTED))
kpis(items)

st.markdown('<p class="card-title">Each update: priced, nothing found, failed</p>', unsafe_allow_html=True)
r = runs.sort_values("started")
fig = go.Figure()
for col, name, colour in (("ok", "Priced", ECONOMY), ("empty", "Nothing found", "#7D8AA0"), ("failed", "Failed", TIER_COLORS["high"])):
    fig.add_bar(x=pd.to_datetime(r.started).dt.strftime("%d %b %H:%M:%S"), y=r[col], name=name, marker=dict(color=colour, line=dict(color="#F5F7FB", width=1)),
                customdata=r.label, hovertemplate=f"%{{customdata}}<br>{name}: %{{y}}<extra></extra>")
fig.update_layout(barmode="stack", height=280, margin=dict(l=8, r=8, t=30, b=0), paper_bgcolor="rgba(0,0,0,0)",
                  plot_bgcolor="rgba(0,0,0,0)", font=dict(family=FONT, color=MUTED),
                  legend=dict(orientation="h", x=0, y=1.15, traceorder="normal"), yaxis=dict(gridcolor=GRID, title=None),
                  xaxis=dict(showgrid=False, type="category"), bargap=0.5 if len(r) > 3 else 0.85)
st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

errors = runs[runs.error.notna()]
if len(errors):
    st.markdown('<p class="card-title">Most common problems</p>', unsafe_allow_html=True)
    st.dataframe(errors.error.str.slice(0, 120).value_counts().rename("updates").to_frame(), width="stretch")

st.markdown('<p class="card-title">Saved flight prices by route</p>', unsafe_allow_html=True)
p = pd.read_sql("SELECT * FROM prices", db)
if p.empty:
    st.caption("No flight prices saved right now.")
else:
    p = p.sort_values("fetched").drop_duplicates(["day", "orig", "dest", "cabin", "nonstop"], keep="last")
    g = p.groupby(["orig", "dest", "cabin"])
    by_route = pd.DataFrame({
        "Days checked": g.size(),
        "With a flight": g.price.count(),
        "From the calendar": g.airlines.apply(lambda s: int((s == "calendar").sum())),
        "From day search": g.airlines.apply(lambda s: int((s == "day search").sum())),
        "Cheapest €": g.price.min(), "Median €": g.price.median(), "Dearest €": g.price.max(),
        "Last update": g.fetched.max().str.slice(0, 16).str.replace("T", " "),
    }).reset_index().rename(columns={"orig": "From", "dest": "To", "cabin": "Cabin"})
    st.dataframe(by_route, width="stretch", hide_index=True)
    st.caption("From the calendar = Google's price calendar (one request covers many days); from day search = checked one day at "
               "a time because the calendar left that day out.")

st.markdown('<p class="card-title">Saved hotel prices</p>', unsafe_allow_html=True)
h = pd.read_sql("SELECT * FROM hotel", db)
b = pd.read_sql("SELECT * FROM booking", db)
rows = []
if len(h):
    h = h.sort_values("fetched").drop_duplicates(["day", "query", "filt", "nights", "adults"], keep="last")
    for (q, f), x in h.groupby(["query", "filt"]):
        rows.append(("Google Hotels", f"{q} · {f}", len(x), int(x.price.count()), x.price.min(), x.price.median(), x.price.max()))
if len(b):
    b = b.sort_values("fetched").drop_duplicates(["hkey", "day", "nights"], keep="last")
    for k, x in b.groupby("hkey"):
        rows.append(("Booking.com (Xotelo)", k, len(x), int(x.price.count()), x.price.min(), x.price.median(), x.price.max()))
if rows:
    st.dataframe(pd.DataFrame(rows, columns=["Source", "Hotel", "Nights checked", "With a price", "Cheapest €", "Median €",
                                             "Dearest €"]), width="stretch", hide_index=True)
else:
    st.caption("No hotel prices saved right now.")

with st.expander("All updates"):
    st.dataframe(runs.rename(columns={"page": "Page", "label": "Search", "started": "Started", "seconds": "Seconds",
                                      "total": "Planned", "ok": "Priced", "empty": "Nothing found", "failed": "Failed",
                                      "error": "First problem"}), width="stretch", hide_index=True)
