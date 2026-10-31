"""Shared look: white cards on a grey group background, system font, Aegean blue for economy and amber for business (colours validated with
dataviz validate_palette.js). Controls are segmented: grey at rest, solid blue when selected."""
import csv
import html
import io
import os
from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ECONOMY, BUSINESS, INK, MUTED, GRID = "#2563C9", "#B8741A", "#1D1D1F", "#6E6E73", "#E5E5EA"
BOOKING = "#0E9F6E"
TIER_COLORS = {"cheap": "#2563C9", "average": "#7D8AA0", "high": "#C2410C"}  # diverging: blue / neutral gray / orange
BG, SURFACE, FILL = "#F5F5F7", "#FFFFFF", "#E8E8ED"  # white cards on the grey group background; FILL is a control at rest
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"  # the platform's face, the same one .streamlit/config.toml gives the widgets
SHADOW = "0 1px 2px rgba(0,0,0,.04), 0 6px 20px rgba(0,0,0,.05)"

CSS = f"""
<style>
html, body, [class*="css"], .stApp, .stApp button, .stApp input, .stApp textarea {{ font-family: {FONT}; font-variant-numeric: tabular-nums; -webkit-font-smoothing: antialiased; }}
.stApp {{ background: {BG}; color: {INK}; }}
.block-container {{ padding-top: 5.5rem; max-width: 1040px; }}
::selection {{ background: rgba(37,99,201,.22); }} input, textarea {{ caret-color: {ECONOMY}; }}
:focus-visible {{ outline: 2px solid {ECONOMY}; outline-offset: 2px; }}
* {{ scrollbar-width: thin; scrollbar-color: #C7C7CC transparent; }}
/* the top bar is the control layer: translucent, content scrolls under it */
[data-testid="stHeader"] {{ background: rgba(255,255,255,.72); -webkit-backdrop-filter: saturate(180%) blur(20px); backdrop-filter: saturate(180%) blur(20px); border-bottom: 1px solid rgba(0,0,0,.08); height: 64px; box-shadow: none; }}
[data-testid="stHeader"] img {{ height:32px !important; max-width:none; }}
[data-testid="stHeader"] a[data-testid="stTopNavLink"], [data-testid="stHeader"] [data-testid="stTopNavLink"] {{ border-radius:999px; padding:6px 14px; font-weight:500; }}
.scraped {{ display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin:-.6rem 0 1.25rem; }}
.scraped .bl {{ color:{MUTED}; font-size:.82rem; margin-right:4px; }}
.scraped .old {{ color:#9A5B00; }}
[data-testid="stSidebar"] {{ background: {INK}; }}
[data-testid="stSidebar"] * {{ color: #E8EEF8 !important; }}
h1, h2, h3 {{ font-family: {FONT} !important; letter-spacing: -0.02em; color: {INK}; font-weight: 650; }}
.route {{ display:flex; align-items:center; gap:16px; margin:2px 0 4px; }}
.route .code {{ font: 650 clamp(2.1rem,7vw,3.1rem)/1 {FONT}; letter-spacing:-0.035em; color:{INK}; }}
.route .line {{ flex:1; height:2px; position:relative; background:{GRID}; border-radius:2px; min-width:40px; }}
.route .line::after {{ content:''; position:absolute; right:1px; top:-2.5px; width:7px; height:7px; border-top:2px solid {MUTED}; border-right:2px solid {MUTED}; transform:rotate(45deg); }}
.sub {{ color:{MUTED}; margin:0 0 1.25rem; font-size:.95rem; max-width:72ch; text-wrap:balance; }}
/* the answers, as one grouped list: what, when, and the price trailing */
/* section headings: dark, and with more room above than below, so each binds to what follows it, not to what came before */
[data-testid="stMarkdownContainer"] p.group, .group {{ color:{INK}; font-size:1rem; font-weight:650; letter-spacing:-.01em; margin:1.1rem 0 .45rem 2px; }}
.fares {{ background:{SURFACE}; border-radius:14px; box-shadow:{SHADOW}; margin:0 0 1.25rem; overflow:hidden; }}
.fare {{ display:flex; align-items:center; justify-content:space-between; gap:16px; padding:14px 18px; }}
.fare + .fare {{ border-top:1px solid {GRID}; }}
.fare .what {{ display:flex; align-items:center; gap:10px; font-weight:600; font-size:.95rem; color:{INK}; }}
.fare .dot {{ width:9px; height:9px; border-radius:50%; flex:none; }}
.fare .when {{ color:{MUTED}; font-size:.85rem; margin:2px 0 0 19px; text-wrap:pretty; }}
.fare .price {{ font: 650 1.6rem/1 {FONT}; letter-spacing:-0.02em; color:{INK}; white-space:nowrap; }}
.fare .price.none {{ color:{MUTED}; font-weight:500; }}
.fare .price .unit {{ font-size:.85rem; font-weight:500; color:{MUTED}; letter-spacing:0; }}
[data-testid="stCaptionContainer"] {{ color:{MUTED} !important; opacity:1 !important; max-width:72ch; text-wrap:pretty; }}
.stMarkdown p {{ max-width:80ch; }}  /* a measure the eye can track back across; tables, charts and cards keep the full width */
.card-title {{ font:600 1.05rem/1.35 {FONT}; color:{INK}; margin:.4rem 0 0; max-width:72ch; text-wrap:balance; }}
/* tabs as a segmented control */
.stTabs [role="tablist"], .stTabs [data-baseweb="tab-list"] {{ gap:2px; background:{FILL}; padding:3px; border-radius:10px; width:fit-content; max-width:100%; border:0 !important; box-shadow:none !important; }}
.stTabs [role="tab"] {{ border-radius:8px; padding:6px 18px !important; height:auto; background:transparent; border:0 !important; box-shadow:none; }}
.stTabs [role="tab"]::after, .stTabs [role="tab"]::before, .stTabs [role="tablist"]::after, .stTabs [role="tablist"]::before, .stTabs .react-aria-SelectionIndicator {{ display:none !important; }}
.stTabs [aria-selected="true"] {{ background:{SURFACE}; box-shadow:0 1px 3px rgba(0,0,0,.12); }}
.stTabs [role="tab"] p {{ color:#55555A; font-weight:500; }}  /* 5.6:1 on the grey track; MUTED was 4.2:1 */
.stTabs [aria-selected="true"] p {{ color:{INK}; font-weight:600; }}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display:none; }}
.stButton > button, .stDownloadButton > button {{ border-radius:10px; font-weight:600; min-height:44px; }}
[data-testid="stDateInputField"] {{ border:0 !important; }}  /* plain field like the airport boxes, not a card */
[data-testid="stExpander"] {{ background:{SURFACE}; border:0; border-radius:14px; box-shadow:{SHADOW}; }}
.st-key-search {{ border:0 !important; background:{SURFACE}; border-radius:14px; box-shadow:{SHADOW}; padding:18px 20px; }}
.st-key-search [data-baseweb="select"] > div, .st-key-search [data-baseweb="input"], .st-key-search [data-baseweb="base-input"], .st-key-search [data-testid="stDateInput"] > div > div,
.st-key-search [data-testid="stTextInput"] > div > div, .st-key-search [data-testid="stNumberInput"] > div > div {{ background:{BG}; border-color:transparent; border-radius:10px; }}
.st-key-search input {{ background:transparent; }}
.best {{ display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin:.2rem 0 .6rem; }}
.best .dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; }}
.best .bl {{ color:{MUTED}; font-size:.82rem; margin-right:4px; }}
.chip {{ background:{SURFACE}; border:1px solid {GRID}; border-radius:999px; padding:4px 12px; font-size:.88rem; color:{INK}; }}
.chip b {{ font-weight:600; margin-right:4px; }}
.best .more {{ color:{MUTED}; font-size:.85rem; }}
/* pills: grey at rest, solid blue when selected, like a segmented control (the tinted default was 4.0:1 contrast) */
[data-testid="stButtonGroup"] button {{ border:0 !important; background:{FILL} !important; }}
[data-testid="stButtonGroup"] > div {{ flex-wrap:wrap !important; overflow:visible !important; }}  /* a pill row wraps, it never clips a label: the platform font's width varies by device */
[data-testid="stButtonGroup"] button[aria-pressed="true"], [data-testid="stButtonGroup"] button[aria-checked="true"] {{ background:{ECONOMY} !important; border-color:{ECONOMY} !important; }}
[data-testid="stButtonGroup"] button[aria-pressed="true"] *, [data-testid="stButtonGroup"] button[aria-checked="true"] * {{ color:#fff !important; }}
/* a column with nothing in it (Nights away on a one-way trip) must not leave a gap on phones */
[data-testid="stColumn"]:has(> [data-testid="stVerticalBlock"]:empty) {{ display:none; }}
@media (pointer: coarse) {{ [data-testid="stButtonGroup"] button, .chip {{ min-height:44px; }} .chip {{ display:inline-flex; align-items:center; }} }}
@media (max-width: 640px) {{ .block-container {{ padding-top: 5rem; }} [data-testid="stButtonGroup"] > div {{ flex-wrap:wrap !important; overflow:visible !important; }} .st-key-search {{ padding:14px; }} .st-key-search [data-testid="stHorizontalBlock"]:has(.st-key-swap-airports), .st-key-search [data-testid="stHorizontalBlock"]:has([data-testid="stPopover"]) {{ flex-wrap:nowrap !important; gap:8px; }} .st-key-search [data-testid="stHorizontalBlock"]:has(.st-key-swap-airports) > [data-testid="stColumn"], .st-key-search [data-testid="stHorizontalBlock"]:has([data-testid="stPopover"]) > [data-testid="stColumn"] {{ min-width:0 !important; width:auto !important; flex:1 1 0 !important; }} .st-key-search [data-testid="stColumn"]:has(.st-key-swap-airports) {{ flex:0 0 auto !important; }} .st-key-search [data-testid="stColumn"]:has([data-testid="stNumberInput"]) {{ min-width:0 !important; width:auto !important; flex:1 1 calc(50% - 8px) !important; }} .fare {{ padding:12px 14px; }} .fare .price {{ font-size:1.4rem; }} }}
</style>
"""


e = html.escape  # every text below can come from a visitor or from Google, and is inserted as HTML


def route(a, b, sub):
    """Big title; with `b` it becomes a route strip a ✈ b."""
    mid = f'<span class="line"></span><span class="code">{e(b)}</span>' if b else ""
    st.markdown(f'<div class="route"><span class="code">{e(a)}</span>{mid}</div><p class="sub">{e(sub)}</p>', unsafe_allow_html=True)


def scraped(items):
    """Strip under the title: when each part of the search was last fetched. items: (label, `fetched` per day, days in range)."""
    def chip(label, f, n):
        if not len(f):
            return f'<span class="chip"><b>{e(label)}</b>not fetched yet</span>'
        new, old = pd.Timestamp(f.max()), pd.Timestamp(f.min())
        part = f" · {len(f)} of {n} days" if len(f) < n else f" · all {n} days"
        since = f' <span class="old">· oldest {old:%d %b %H:%M}</span>' if new - old > pd.Timedelta(hours=12) else ""
        return f'<span class="chip"><b>{e(label)}</b>{new:%a %d %b %H:%M}{part}{since}</span>'
    st.markdown('<div class="scraped"><span class="bl">Last fetched</span>' + "".join(chip(*i) for i in items) + "</div>",
                unsafe_allow_html=True)


def kpis(items):
    """items: (label, value, sub, dot colour). One grouped list: what it is and when on the left, the price trailing."""
    st.markdown('<div class="fares">' + "".join(
        f'<div class="fare"><div><div class="what"><span class="dot" style="background:{c}"></span>{e(l)}</div>'
        f'<div class="when">{e(s)}</div></div><div class="price{" none" if v == "—" else ""}">{e(v[:-6]) + "<span class=unit> miles</span>" if v.endswith(" miles") else e(v)}</div></div>'
        for l, v, s, c in items) + "</div>",
        unsafe_allow_html=True)


def excel_bytes(df):
    """The table as an .xlsx file in memory (a shared file on disk would mix up downloads between visitors)."""
    buf = io.BytesIO()
    df.to_excel(buf)
    return buf.getvalue()


def price_chart(df, series, unit="€", near=None, extra=None):
    """df indexed by ISO date string; series {column: (label, colour)}. Days in near[column] (the near-cheapest days) are
    ringed; the cheapest of them is also labelled. Without `near`, only the cheapest point is ringed. extra: more {column: (label,
    colour)} drawn as dashed lines for comparison (another airline's fare), without rings or labels."""
    x = pd.to_datetime(df.index)
    fig = go.Figure()
    for col, (label, colour) in (extra or {}).items():
        if col in df and df[col].notna().any():
            fig.add_scatter(x=x, y=df[col], name=label, mode="lines+markers", connectgaps=False, line=dict(color=colour, width=2, dash="dash"),
                            marker=dict(size=5, color=colour), hovertemplate=f"{label}: {unit}%{{y:.0f}}<extra></extra>")
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
                      font=dict(family=FONT, color=MUTED), hovermode="x unified",
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
        fig.add_bar(x=pd.to_datetime(d.index), y=[1] * len(d), name=tier.capitalize(), marker=dict(color=colour, line=dict(color=BG, width=2)),
                    hovertemplate=f"%{{x|%a %d %b}}: {tier}<extra></extra>")
    fig.update_layout(height=130, margin=dict(l=8, r=8, t=30, b=0), barmode="stack", bargap=0, paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(family=FONT, color=MUTED),
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


def aegean_rows():
    """[{iata, city, country, seasonal, name}] for the airports AEGEAN flies to now or seasonally (data/aegean_airports.csv, refreshed by
    data/build_aegean_airports.py), the name from data/airports.csv."""
    root = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(root, "data", "airports.csv"), encoding="utf-8") as f:
        names = {r["iata"]: r["name"] for r in csv.DictReader(f)}
    with open(os.path.join(root, "data", "aegean_airports.csv"), encoding="utf-8") as f:
        return [dict(r, name=names.get(r["iata"], r["city"])) for r in csv.DictReader(f)]


@st.cache_data
def aegean_airports():
    """{IATA: label} for AEGEAN's destinations only, searchable by code, airport name, city or country; seasonal ones say so.
    A route AEGEAN does not fly is the slowest search of all (no calendar, every day searched one by one), so the pickers offer no other."""
    return {r["iata"]: f"{r['iata']} · {r['name']} — {r['city']}, {r['country']}" + (" (seasonal)" if r["seasonal"] == "1" else "")
            for r in aegean_rows()}


def airport_picker(where, label, default, key):
    names = aegean_airports()
    codes = list(names)
    return where.selectbox(label, codes, index=codes.index(default), format_func=names.get, key=key,
                           help="Type a city, airport name or code, for example Athens, Bilbao or FRA. Only AEGEAN's destinations are listed.")


def window_picker(where, key, today, horizon):
    """A start date (tomorrow by default) plus 15 / 30 / 60 / 90 days; Custom asks for the end date. Returns (start, end)."""
    last = today + timedelta(horizon)
    c = where.columns([1, 2])
    start = c[0].date_input("Starting", today + timedelta(1), min_value=today, max_value=last, key=f"{key}-from",
                            format="DD/MM/YYYY")
    pick = c[1].pills("For the next", list(WINDOWS) + ["Custom"], default="30 days", required=True, key=f"{key}-win")
    if pick == "Custom":
        end = c[0].date_input("Until", min(start + timedelta(29), last), min_value=start, max_value=last, key=f"{key}-to",
                              format="DD/MM/YYYY")
        return start, end
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


def round_trips(out, back, nights, cabins=("economy", "business")):
    """Every departure day in `out` paired with the return day `n` nights later in `back` (both priced), cheapest first.
    out / back: frames indexed by iso day with one price column per cabin. Aegean prices one-way fares, so a return is the sum."""
    rows = []
    for d in out.index:
        for n in nights:
            r = (pd.Timestamp(d) + pd.Timedelta(days=n)).strftime("%Y-%m-%d")
            if r not in back.index:
                continue
            for c in cabins:
                o, b = out.at[d, c], back.at[r, c]
                if pd.notna(o) and pd.notna(b):
                    rows.append((c, d, r, n, o, b, o + b))
    t = pd.DataFrame(rows, columns=["cabin", "depart", "back", "nights", "outbound", "return", "total"])
    return t.sort_values(["total", "depart"], kind="stable").reset_index(drop=True)


def best_trips(t, colour, label, tolerance, limit=6):
    """Chips with the round trips within `tolerance` of the cheapest total."""
    if t.empty:
        return
    t = t[t.total <= t.total.min() * (1 + tolerance) + 1e-9]
    chips = "".join(f'<span class="chip"><b>{e(f"{pd.Timestamp(r.depart):%a %d %b} → {pd.Timestamp(r.back):%a %d %b}")}</b>'
                    f'€{r.total:.0f}</span>' for r in t.head(limit).itertuples())
    more = f'<span class="more">+{len(t) - limit} more</span>' if len(t) > limit else ""
    st.markdown(f'<div class="best"><span class="dot" style="background:{colour}"></span><span class="bl">{e(label)}</span>'
                f'{chips}{more}</div>', unsafe_allow_html=True)


def no_service_note(seen, orig, dest, nonstop=True):
    """When every checked day has no flight: say so, and name AEGEAN's airports in the destination's city (London City -> Heathrow, Gatwick)."""
    from trip_plan import city  # ponytail: imported here, not at the top: trip_plan pulls in the award and insight modules
    days = pd.to_datetime(sorted(seen.day.unique()))
    when = f"{days[0]:%a %d %b}" if len(days) == 1 else f"{days[0]:%a %d %b} – {days[-1]:%a %d %b}"
    note = f"AEGEAN has no {'nonstop ' if nonstop else ''}flights {orig} → {dest} on the days checked ({when})."
    want = city(dest).lower()
    near = sorted((r for r in aegean_rows() if r["city"].lower() == want and r["iata"] != dest), key=lambda r: r["seasonal"])  # year-round first
    names = [f"{r['name'].replace(' Airport', '')} ({r['iata']}{', seasonal' if r['seasonal'] == '1' else ''})" for r in near]
    return note + (f" Try {' or '.join(names)}." if names else "")


def schedule_note(seen, orig, dest, nonstop=True):
    """One sentence on when AEGEAN flies a route, from the days checked: the weekdays it flies when not every day, and the last flight when
    the days checked after it are more than twice the route's usual gap (seasonal routes such as ATH-BIO, Tuesdays and Fridays, none 21 Nov to 29 Mar). None when the
    checked days show no such pattern. seen: one row per day and cabin, price NaN where the day was checked and has no flight."""
    if not len(seen):
        return None
    flies = seen.groupby("day").price.max()  # a day flies if either cabin has a fare
    if flies.notna().sum() == 0:
        return no_service_note(seen, orig, dest, nonstop)
    if len(flies) < 14:
        return None
    when = pd.to_datetime(flies.index)
    flown, last = when[flies.notna().values], when[flies.notna().values].max()
    parts = []
    quiet = {d for d in range(7) if d not in set(flown.weekday) and (when[(when <= last)].weekday == d).sum() >= 2}
    if quiet and len(quiet) < 7:
        names = [["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"][d] for d in sorted(set(flown.weekday))]
        parts.append(f"only on {', '.join(names[:-1]) + ' and ' + names[-1] if len(names) > 1 else names[0]}")
    gaps = pd.Series(flown.sort_values()).diff().dt.days.dropna()
    if (when.max() - last).days >= max(7, 2 * (gaps.max() if len(gaps) else 0)):  # twice the route's usual gap: it has stopped
        parts.append(f"not after {last:%a %d %b} in the days checked (search later dates to see when it flies again)")
    return f"AEGEAN flies {orig} → {dest} " + ", and ".join(parts) + "." if parts else None


@st.fragment(run_every=8)
def github_progress(key, workflow, path, what="prices"):
    """While GitHub fetches a search (st.session_state[f"gh-{key}"] = (request, started)): say so, and reload the page when the
    results are in. path: the data file the fetch writes, read again from GitHub when it is done."""
    import time
    import gh
    pending = st.session_state.get(f"gh-{key}")
    if not pending:
        return
    request, since = pending
    waited = int(time.time() - since)
    try:
        r = gh.run(gh.token(), request, workflow)
    except gh.GitHubError as err:
        st.error(str(err))
        return
    if r and r["status"] == "completed":
        del st.session_state[f"gh-{key}"]
        gh.forget(path)  # what was found is saved even if part of the search failed
        if r["conclusion"] != "success":
            st.session_state[f"gh-note-{key}"] = (f"Part of the search on GitHub did not finish ({r['conclusion']}): what was found is "
                                                  f"saved, press Update prices to fetch the rest. Its log: {r['url']}")
        st.rerun()
    elif waited > 1500:
        del st.session_state[f"gh-{key}"]
        st.error("The search on GitHub is taking too long. Press Update prices to try again." + (f" Its log: {r['url']}" if r else ""))
    else:
        st.info(f"Searching on GitHub's servers (Google limits this app's own server): {waited // 60}:{waited % 60:02d} so far, "
                f"usually 1–3 minutes. The {what} appear here when it is done.", icon=":material/cloud_sync:")


def github_note(key):
    """The note github_progress left when part of a search failed, once."""
    if note := st.session_state.pop(f"gh-note-{key}", None):
        st.warning(note, icon=":material/warning:")
