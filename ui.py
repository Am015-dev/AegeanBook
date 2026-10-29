"""Shared look: Aegean-blue ink on cool paper, amber for premium. Colours validated with dataviz validate_palette.js."""
import csv
import html
import io
import os
from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ECONOMY, BUSINESS, INK, MUTED, GRID = "#2563C9", "#B8741A", "#0E1B2E", "#5B6B82", "#E3E8F0"
BOOKING = "#0E9F6E"
TIER_COLORS = {"cheap": "#2563C9", "average": "#7D8AA0", "high": "#C2410C"}  # diverging: blue / neutral gray / orange

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');
html, body, [class*="css"], .stApp {{ font-family: 'IBM Plex Sans', system-ui, sans-serif; font-variant-numeric: tabular-nums; }}
.stApp {{ background: #F5F7FB; }}
.block-container {{ padding-top: 2rem; max-width: 1080px; }}
[data-testid="stSidebar"] {{ background: {INK}; }}
[data-testid="stSidebar"] * {{ color: #E8EEF8 !important; }}
h1, h2, h3 {{ font-family: 'Bricolage Grotesque', sans-serif !important; letter-spacing: -0.02em; color: {INK}; }}
.route {{ display:flex; align-items:center; gap:18px; margin:4px 0 2px; }}
.route .code {{ font: 700 clamp(2.4rem,9vw,3.8rem)/1 'Bricolage Grotesque'; letter-spacing:-0.04em; color:{INK}; }}
.route .line {{ flex:1; height:0; position:relative; border-top:2px dashed {ECONOMY}; min-width:40px; }}
.route .line::after {{ content:'✈'; position:absolute; left:50%; top:-13px; transform:translateX(-50%); background:#F5F7FB; padding:0 8px; color:{ECONOMY}; font-size:1.2rem; }}
.sub {{ color:{MUTED}; margin:0 0 1.4rem; font-size:.95rem; text-wrap:balance; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin-bottom:1.2rem; }}
.kpi {{ background:#fff; border:1px solid {GRID}; border-radius:16px; padding:16px 18px; box-shadow:0 1px 2px rgba(14,27,46,.04); }}
.kpi .l {{ color:{MUTED}; font-size:.78rem; text-transform:uppercase; letter-spacing:.06em; display:flex; align-items:center; gap:8px; }}
.kpi .dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; }}
.kpi .v {{ font: 700 2rem/1.15 'Bricolage Grotesque'; color:{INK}; margin-top:6px; }}
.kpi .s {{ color:{MUTED}; font-size:.85rem; margin-top:2px; }}
[data-testid="stCaptionContainer"] {{ color:#4B5B72 !important; opacity:1 !important; text-wrap:pretty; }}
.card-title {{ font:600 1.05rem 'IBM Plex Sans'; color:{INK}; margin:.4rem 0 0; }}
.stTabs [data-baseweb="tab-list"] {{ gap:6px; }}
.stTabs [role="tab"] {{ border-radius:999px; padding:8px 20px !important; height:auto; background:#fff; border:1px solid {GRID}; }}
.stTabs [aria-selected="true"] {{ background:{INK}; }}
.stTabs [aria-selected="true"] p {{ color:#fff !important; }}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display:none; }}
.stButton > button, .stDownloadButton > button {{ border-radius:12px; font-weight:600; }}
[data-testid="stExpander"] {{ background:#fff; border:1px solid {GRID}; border-radius:16px; }}
.best {{ display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin:.2rem 0 .6rem; }}
.best .dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; }}
.best .bl {{ color:{MUTED}; font-size:.78rem; text-transform:uppercase; letter-spacing:.06em; margin-right:4px; }}
.chip {{ background:#fff; border:1px solid {GRID}; border-radius:999px; padding:4px 12px; font-size:.88rem; color:{INK}; }}
.chip b {{ font-weight:600; margin-right:4px; }}
.best .more {{ color:{MUTED}; font-size:.85rem; }}
@media (max-width: 640px) {{ .block-container {{ padding-top: 4rem; }} }}
@media (prefers-reduced-motion: no-preference) {{ .kpi {{ transition: transform .15s; }} .kpi:hover {{ transform: translateY(-2px); }} }}
</style>
"""


e = html.escape  # every text below can come from a visitor or from Google, and is inserted as HTML


def route(a, b, sub):
    """Big title; with `b` it becomes a route strip a ✈ b."""
    mid = f'<span class="line"></span><span class="code">{e(b)}</span>' if b else ""
    st.markdown(f'<div class="route"><span class="code">{e(a)}</span>{mid}</div><p class="sub">{e(sub)}</p>', unsafe_allow_html=True)


def kpis(items):
    """items: (label, value, sub, dot colour)."""
    st.markdown('<div class="kpis">' + "".join(
        f'<div class="kpi"><div class="l"><span class="dot" style="background:{c}"></span>{e(l)}</div>'
        f'<div class="v">{e(v)}</div><div class="s">{e(s)}</div></div>' for l, v, s, c in items) + "</div>",
        unsafe_allow_html=True)


def excel_bytes(df):
    """The table as an .xlsx file in memory (a shared file on disk would mix up downloads between visitors)."""
    buf = io.BytesIO()
    df.to_excel(buf)
    return buf.getvalue()


def price_chart(df, series, unit="€", near=None):
    """df indexed by ISO date string; series {column: (label, colour)}. Days in near[column] (the near-cheapest days) are
    ringed; the cheapest of them is also labelled. Without `near`, only the cheapest point is ringed."""
    x = pd.to_datetime(df.index)
    fig = go.Figure()
    for col, (label, colour) in series.items():
        y = df[col]
        fig.add_scatter(x=x, y=y, name=label, mode="lines+markers", connectgaps=False,
                        line=dict(color=colour, width=2), marker=dict(size=7, color=colour, line=dict(color="#fff", width=2)),
                        hovertemplate=f"{label}: {unit}%{{y:.0f}}<extra></extra>")
        if y.notna().any():
            i = y.idxmin()
            ring = [d for d in (near or {}).get(col, [i]) if d in y.index] or [i]
            fig.add_scatter(x=pd.to_datetime(ring), y=[y[d] for d in ring], mode="markers+text", showlegend=False, hoverinfo="skip",
                            marker=dict(size=15, color="rgba(0,0,0,0)", line=dict(color=colour, width=2.5)),
                            text=[f"{unit}{y[d]:.0f}" if d == i else "" for d in ring], textposition="bottom center",
                            textfont=dict(color=INK, size=13))
    fig.update_layout(height=380, margin=dict(l=28, r=8, t=40, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="IBM Plex Sans", color=MUTED), hovermode="x unified",
                      legend=dict(orientation="h", x=0, y=1.12, font=dict(color=INK)),
                      xaxis=dict(tickformat="%d %b", dtick=86400000 if len(df) <= 14 else None, showgrid=False, linecolor=GRID),
                      yaxis=dict(tickprefix=unit, automargin=True, gridcolor=GRID, zeroline=False, rangemode="tozero"))
    return fig


def heat_strip(tiers):
    """One bar per day coloured by cheap / average / high. tiers: {iso day: tier}."""
    s = pd.Series(tiers).sort_index()
    fig = go.Figure()
    for tier, colour in TIER_COLORS.items():
        d = s[s == tier]
        fig.add_bar(x=pd.to_datetime(d.index), y=[1] * len(d), name=tier.capitalize(), marker=dict(color=colour, line=dict(color="#F5F7FB", width=2)),
                    hovertemplate=f"%{{x|%a %d %b}}: {tier}<extra></extra>")
    fig.update_layout(height=130, margin=dict(l=8, r=8, t=30, b=0), barmode="stack", bargap=0, paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(family="IBM Plex Sans", color=MUTED),
                      legend=dict(orientation="h", x=0, y=1.35, font=dict(color=INK), traceorder="normal"),
                      xaxis=dict(tickformat="%d %b", dtick=86400000 * (1 if len(s) <= 14 else 7), showgrid=False),
                      yaxis=dict(visible=False))
    return fig


WINDOWS = {"15 days": 15, "30 days": 30, "60 days": 60, "90 days": 90}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@st.cache_data
def airports():
    """{IATA: label} for every airport the flight library knows, searchable by code, airport name, city or country.
    Built from OurAirports (public domain) by data/airports.csv."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "airports.csv"), encoding="utf-8") as f:
        return {r["iata"]: f"{r['iata']} · {r['name']}" + (f" — {r['city']}, {r['country']}" if r["city"] else f" — {r['country']}")
                for r in csv.DictReader(f) if r["size"] != "2"}  # only airports with scheduled flights: no airfields


def airport_picker(where, label, default, key):
    names = airports()
    codes = list(names)
    return where.selectbox(label, codes, index=codes.index(default), format_func=names.get, key=key,
                           help="Type a city, airport name or code, for example Athens, Bilbao or FRA.")


def window_picker(where, key, today, horizon):
    """Quick window starting tomorrow (15 / 30 / 60 / 90 days); Custom shows two dates. Returns (start, end)."""
    pick = where.pills("Dates", list(WINDOWS) + ["Custom"], default="30 days", required=True, key=f"{key}-win")
    last = today + timedelta(horizon)
    if pick == "Custom":
        c = where.columns(2)
        start = c[0].date_input("From", today + timedelta(1), min_value=today, max_value=last, key=f"{key}-from")
        end = c[1].date_input("To", min(start + timedelta(29), last), min_value=today, max_value=last, key=f"{key}-to")
        return start, end
    start = today + timedelta(1)
    return start, min(start + timedelta(WINDOWS[pick] - 1), last)


def smart_filters(where, key):
    """How close to the cheapest still counts as cheap, and which weekdays to consider. Returns (tolerance, weekdays)."""
    c = where.columns([2, 3])
    pct = c[0].pills("Cheap means within … of the cheapest", [0, 5, 10, 20, 30], default=10, required=True, key=f"{key}-tol",
                     format_func=lambda v: f"{v}%" if v else "Exact")
    days = c[1].pills("Days of the week", WEEKDAYS, selection_mode="multi", default=WEEKDAYS, key=f"{key}-days")
    return pct / 100, days or WEEKDAYS


def on_weekdays(df, days):
    """Rows of a date-indexed frame that fall on the chosen weekdays."""
    return df[pd.to_datetime(df.index).strftime("%a").isin(days)] if len(df) else df


def near_cheapest(s, tolerance):
    """Days priced within `tolerance` (0.1 = 10 %) of the cheapest, cheapest first. A route often has several such days."""
    s = s.dropna()
    return s[s <= s.min() * (1 + tolerance) + 1e-9].sort_values(kind="stable") if len(s) else s


def best_days(s, colour, label, limit=8):
    """Chips with the near-cheapest days, cheapest first."""
    if s.empty:
        return
    chips = "".join(f'<span class="chip"><b>{e(f"{pd.Timestamp(d):%a %d %b}")}</b>€{p:.0f}</span>' for d, p in s.head(limit).items())
    more = f'<span class="more">+{len(s) - limit} more</span>' if len(s) > limit else ""
    st.markdown(f'<div class="best"><span class="dot" style="background:{colour}"></span><span class="bl">{e(label)}</span>'
                f'{chips}{more}</div>', unsafe_allow_html=True)
