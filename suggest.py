"""'Our suggestion': the one clear answer at the end of the booking strategy. Every option is put on the same footing, euros per flight (or per
round trip): cash, miles (valued at what the user says a mile is worth, plus the taxes), and the Aegean Pass (price over its flights plus
taxes). Two situations are answered, because that is how people choose: flexible dates (take the cheapest day) and fixed dates (a typical
fare). A pass is a commitment, so it is only recommended when it beats the best alternative by a clear margin. A new module on purpose: Streamlit
Cloud can run a new page against older, already loaded modules."""
import html as _html

MARGIN = 0.10  # a pass must be at least this much cheaper than the best non-pass option to be recommended
STYLE = """<style>
.sg{background:#fff;border-radius:14px;border:2px solid #0E7A53;box-shadow:0 1px 2px rgba(0,0,0,.04),0 6px 20px rgba(0,0,0,.05);padding:16px 18px;margin:0 0 1rem}
.sg-h{display:flex;align-items:center;gap:10px;font-weight:650;font-size:1.05rem;color:#1D1D1F;margin-bottom:8px}
.sg-h i{width:9px;height:9px;border-radius:50%;display:inline-block}
.sg-a{padding:8px 0;border-top:1px solid #E5E5EA}
.sg-a small{display:block;color:#6E6E73;font-size:.82rem;font-weight:600;margin-bottom:2px}
.sg-a b{font-size:1.15rem;color:#0E7A53}.sg-a span{display:block;color:#1D1D1F;font-size:.9rem;margin-top:2px}
.sg table{width:100%;border-collapse:collapse;margin-top:8px;font-size:.85rem}
.sg th{color:#6E6E73;font-weight:600;text-align:right;padding:4px 0}.sg th:first-child,.sg td:first-child{text-align:left}
.sg td{padding:5px 0;border-top:1px solid #E5E5EA;text-align:right;color:#1D1D1F}.sg td.win{font-weight:700;color:#0E7A53}
.sg p{margin:8px 0 0;font-size:.8rem;color:#6E6E73}
</style>"""


def options(cash, miles, passes, per_k, legs):
    """The options as [{'name', 'kind', 'flexible', 'fixed', 'detail'}], costs in euros per flight (legs=1) or per round trip (legs=2).
    cash: {'min', 'typical', 'when'} or None; miles: {'miles', 'taxes', 'when'} or None; passes: [{'name', 'eff', 'days', 'price', 'flights',
    'break_even'}]. A mile costs `per_k` euros per 1,000, and the taxes of an award ticket are real money on top."""
    opts = []
    if cash:
        opts.append(dict(name="Pay cash", kind="cash", flexible=cash["min"], fixed=cash["typical"],
                         detail=f"€{cash['min']:.0f} on {cash['when']}, a typical fare is €{cash['typical']:.0f}"))
    if miles:
        cost = miles["miles"] / 1000 * per_k + (miles["taxes"] or 0)
        taxes = f" + €{miles['taxes']:.0f} taxes" if miles["taxes"] else " (award taxes not collected yet, so not included)"
        opts.append(dict(name="Use miles", kind="miles", flexible=cost, fixed=cost,
                         detail=f"{miles['miles']:,.0f} miles{taxes} on {miles['when']}, worth about €{cost:.0f} at your €{per_k:g} per 1,000"))
    for p in passes:
        cost = legs * p["eff"]
        need = f", and it beats a typical fare once you use {p['break_even']} of its {p['flights']} flights" if p["break_even"] else ""
        opts.append(dict(name=p["name"], kind="pass", flexible=cost, fixed=cost,
                         detail=f"€{p['eff']:.0f} per flight with taxes (€{p['price']:,.0f} for {p['flights']} flights, book {p['days']}+ days ahead{need})"))
    return opts


def winner(opts, column):
    """The cheapest option for one situation; a pass only wins by a clear margin over the best alternative, because it is a commitment."""
    plain = [o for o in opts if o["kind"] != "pass" and o[column] is not None]
    passes = [o for o in opts if o["kind"] == "pass" and o[column] is not None]
    best_plain = min(plain, key=lambda o: o[column]) if plain else None
    best_pass = min(passes, key=lambda o: o[column]) if passes else None
    if best_pass and (best_plain is None or best_pass[column] < best_plain[column] * (1 - MARGIN)):
        return best_pass
    return best_plain or best_pass


def card_html(name, colour, opts, unit):
    """The card for one cabin. unit: 'flight' or 'round trip'."""
    e = _html.escape
    if not opts:
        return f'<div class="sg"><div class="sg-h"><i style="background:{colour}"></i>{e(name)}: our suggestion</div><p>No fares or award seats to compare yet.</p></div>'
    flex, fixed = winner(opts, "flexible"), winner(opts, "fixed")
    typical = next((o["fixed"] for o in opts if o["kind"] == "cash"), None)

    def answer(label, w, column):
        extra = ""
        if typical is not None and w["kind"] != "cash" and w[column] < typical and column == "fixed":
            extra = f" That is about €{typical - w[column]:.0f} less than a typical fare."
        cheapest = min(opts, key=lambda o: o[column])
        if cheapest is not w and cheapest["kind"] == "pass" and w[column] - cheapest[column] >= 1:  # why the lowest number is not the advice
            extra += f" The {cheapest['name']} would be about €{w[column] - cheapest[column]:.0f} cheaper, but it is a commitment: it has to be clearly cheaper."
        return f'<div class="sg-a"><small>{label}</small><b>{e(w["name"])} · €{w[column]:.0f} per {unit}</b><span>{e(w["detail"])}.{extra}</span></div>'
    rows = "".join(f'<tr><td>{e(o["name"])}</td><td class="{"win" if o is flex else ""}">€{o["flexible"]:.0f}</td>'
                   f'<td class="{"win" if o is fixed else ""}">€{o["fixed"]:.0f}</td></tr>' for o in sorted(opts, key=lambda o: o["flexible"]))
    notes = []
    if any(o["kind"] == "pass" for o in opts):
        notes.append("A pass is paid upfront and only counts if you really fly its flights; it is recommended only when it is clearly cheaper.")
    if any(o["kind"] == "miles" for o in opts):
        notes.append("Miles are valued at the amount you set above, so changing it can change the answer.")
    return (f'<div class="sg"><div class="sg-h"><i style="background:{colour}"></i>{e(name)}: our suggestion</div>'
            + answer("If your dates are flexible", flex, "flexible") + answer("If your dates are fixed", fixed, "fixed")
            + f'<table><tr><th>Cost per {unit}</th><th>Flexible dates</th><th>Fixed dates</th></tr>{rows}</table>'
            + "".join(f"<p>{e(n)}</p>" for n in notes) + "</div>")
