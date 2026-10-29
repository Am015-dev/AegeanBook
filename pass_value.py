"""Is an Aegean Pass worth it? The pass is a block of one-way flights bought at a fixed price; the airport taxes and surcharges are paid on
top, flight by flight, and each flight has to be booked a minimum number of days ahead (0, 7 or 30 depending on the pass). This compares its
effective cost per flight with the cash fares the app has collected, for the days that could really be booked with it. The price is entered
by the user (it depends on the pass and changes), never assumed here."""
import math

import pandas as pd
import plotly.graph_objects as go

ECONOMY, MUTED, INK, GRID = "#2563C9", "#6E6E73", "#1D1D1F", "#E5E5EA"


def effective_cost(price, flights, uses, taxes):
    """Euros per flight: the pass price spread over the flights you really use (at most the flights in the pass), plus the taxes."""
    return price / max(1, min(uses, flights)) + taxes


def assess(cash, price, flights, uses, taxes, min_days, today):
    """How the pass compares with paying cash. cash: Series of the cheapest fare by iso day. Only days at least `min_days` ahead count,
    because the pass cannot be booked closer to departure. Returns None when no such day has a fare, otherwise a dict."""
    eligible = cash.dropna()
    eligible = eligible[[pd.Timestamp(d).date() >= today + pd.Timedelta(days=min_days) for d in eligible.index]]
    if eligible.empty:
        return None
    eff = effective_cost(price, flights, uses, taxes)
    typical = float(eligible.median())
    cheaper = int((eligible > eff).sum())
    # fewest flights that make the pass beat the typical fare: price / u + taxes <= typical
    need = math.ceil(price / (typical - taxes)) if typical > taxes else None
    return dict(effective=eff, days=len(eligible), cheapest=float(eligible.min()), typical=typical, pass_cheaper=cheaper,
                share=cheaper / len(eligible), break_even_uses=need if need is not None and need <= flights else None, eligible=eligible)


def verdict(a):
    """(css class, one plain sentence) from an assessment."""
    if a["effective"] <= a["cheapest"]:
        return "miles", "<b>Worth it on price.</b> The pass costs less per flight than even the cheapest cash fare."
    if a["share"] >= 0.5:
        return "miles", "<b>Likely worth it.</b> The pass is cheaper than cash on most days, and it also buys flexibility."
    if a["share"] >= 0.2:
        return "cash", "<b>Only on the dear days.</b> Cash is cheaper most of the time; the pass wins when fares are high."
    return "cash", "<b>Not worth it on price alone.</b> Cash is cheaper on most days; the pass only buys flexibility."


def chart(a):
    """The cash fare on each bookable day against the pass's effective cost per flight."""
    s = a["eligible"]
    fig = go.Figure()
    fig.add_scatter(x=pd.to_datetime(s.index), y=s.values, mode="lines+markers", name="Cash fare", line=dict(color=ECONOMY, width=2),
                    marker=dict(size=7, color=ECONOMY, line=dict(color="#fff", width=2)), hovertemplate="Cash: €%{y:.0f}<extra></extra>")
    fig.add_hline(y=a["effective"], line=dict(color="#0E7A53", width=2, dash="dash"),
                  annotation_text=f"Pass: €{a['effective']:.0f} per flight", annotation_position="top left", annotation_font_color="#0E7A53")
    fig.update_layout(height=300, margin=dict(l=28, r=8, t=30, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color=MUTED),
                      hovermode="x unified", showlegend=False, xaxis=dict(tickformat="%d %b", showgrid=False, linecolor=GRID),
                      yaxis=dict(tickprefix="€", gridcolor=GRID, zeroline=False, rangemode="tozero"))
    return fig


def card_html(a, price, flights, uses, taxes, min_days, ret=None):
    """The comparison as one card in the page's plan style. ret: (return pairs where the pass is cheaper, return pairs compared), or None."""
    row = lambda k, v: f'<div class="plan-r"><span>{k}</span><span>{v}</span></div>'
    cls, says = verdict(a)
    used = max(1, min(uses, flights))
    when = "the same day" if min_days == 0 else f"{min_days} days ahead"
    out = [f'<div class="plan"><div class="plan-h"><i style="background:#0E7A53"></i>Aegean Pass, booked {when}</div>',
           row("Effective cost per flight", f"<b>€{a['effective']:.0f}</b> · €{price:,.0f} ÷ {used} flights + €{taxes:.0f} taxes"),
           row(f"Cash fares ({a['days']} bookable days)", f"cheapest €{a['cheapest']:.0f} · typical €{a['typical']:.0f}"),
           row("Pass is cheaper than cash on", f"<b>{a['pass_cheaper']} of {a['days']} days</b> ({a['share']:.0%})")]
    if ret:
        out.append(row("Round trips (2 flights)", f"<b>{ret[0]} of {ret[1]}</b> cheaper with the pass"))
    out.append(row("Beats the typical fare from", f"<b>{a['break_even_uses']} flights</b> used" if a["break_even_uses"] else
                   f"more flights than this pass has ({flights})"))
    out.append(f'<p class="plan-v {cls}">{says}</p>')
    out.append('<p class="plan-v" style="color:#6E6E73">Not priced here: free changes, seat selection, a carry-on bag, Miles+Bonus at the Flex '
               'level, and a free Business upgrade on request when a seat is available.</p></div>')
    return "".join(out)