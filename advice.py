"""'What should I book?': one clear answer per cabin for the trip type chosen at the top (one way or return). Every option is put on the same
footing, euros per flight or per round trip: cash, miles (valued at what the user says a mile is worth, plus the award taxes) and the Aegean
Pass (its price over its flights, plus taxes). Two situations are answered, because that is how people choose: flexible dates (take the cheapest
day) and fixed dates (a typical fare). A pass is a commitment, so it is only recommended when it beats the best alternative by a clear margin.
A new module on purpose: Streamlit Cloud can run a new page against older, already loaded modules."""
import html as _html

MARGIN = 0.10  # a pass must be at least this much cheaper than the best non-pass option to be recommended
STYLE = """<style>
.ad{background:#fff;border-radius:14px;border:2px solid #0E7A53;box-shadow:0 1px 2px rgba(0,0,0,.04),0 6px 20px rgba(0,0,0,.05);padding:16px 18px;margin:0 0 1rem}
.ad-h{display:flex;align-items:center;gap:10px;font-weight:650;font-size:1.1rem;color:#1D1D1F;margin-bottom:6px}
.ad-h i{width:9px;height:9px;border-radius:50%;display:inline-block}
.ad-a{padding:8px 0;border-top:1px solid #E5E5EA}
.ad-a small{display:block;color:#6E6E73;font-size:.82rem;font-weight:600}
.ad-a b{font-size:1.2rem;color:#0E7A53}.ad-a span{display:block;color:#6E6E73;font-size:.88rem;margin-top:1px}
.ad table{width:100%;border-collapse:collapse;margin-top:6px;font-size:.85rem}
.ad th{color:#6E6E73;font-weight:600;text-align:right;padding:4px 0}.ad th:first-child,.ad td:first-child{text-align:left}
.ad td{padding:5px 0;border-top:1px solid #E5E5EA;text-align:right;color:#1D1D1F}.ad td.win{font-weight:700;color:#0E7A53}
.ad p{margin:8px 0 0;font-size:.8rem;color:#6E6E73}
</style>"""


def options(cash, miles, passes, per_k, legs):
    """The options as [{'name', 'kind', 'flexible', 'fixed', 'detail'}], costs in euros per flight (legs=1) or per round trip (legs=2).
    cash: {'min', 'typical', 'when'} or None; miles: {'miles', 'taxes', 'when'} or None; passes: [{'name', 'eff', 'days', 'price', 'flights'}].
    A mile costs `per_k` euros per 1,000, and the taxes of an award ticket are real money on top (missing taxes are said so, not guessed)."""
    opts = []
    if cash:
        opts.append(dict(name="Pay cash", kind="cash", flexible=cash["min"], fixed=cash["typical"], detail=cash["when"]))
    if miles:
        cost = miles["miles"] / 1000 * per_k + (miles["taxes"] or 0)
        taxes = f" + €{miles['taxes']:.0f} taxes" if miles["taxes"] else " (taxes not collected yet)"
        opts.append(dict(name="Use miles", kind="miles", flexible=cost, fixed=cost, detail=f"{miles['miles']:,.0f} miles{taxes}, {miles['when']}"))
    for p in passes:
        opts.append(dict(name=p["name"], kind="pass", flexible=legs * p["eff"], fixed=legs * p["eff"],
                         detail=f"€{p['price']:,.0f} for {p['flights']} flights, book {p['days']}+ days ahead"))
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


def card_html(name, colour, opts, unit, notes=()):
    """The card for one cabin. unit: 'flight' or 'round trip'. notes: extra plain sentences (for example a pass that does not apply)."""
    e = _html.escape
    head = f'<div class="ad"><div class="ad-h"><i style="background:{colour}"></i>{e(name)}</div>'
    if not opts:
        return head + '<p>No fares or award seats to compare yet.</p>' + "".join(f"<p>{e(n)}</p>" for n in notes) + "</div>"
    flex, fixed = winner(opts, "flexible"), winner(opts, "fixed")
    typical = next((o["fixed"] for o in opts if o["kind"] == "cash"), None)

    def answer(label, w, column):
        if w["kind"] == "cash":
            why = w["detail"] if column == "flexible" else f"a typical fare is €{w['fixed']:.0f}"
        else:
            why = w["detail"] + (f" · about €{typical - w[column]:.0f} less than a typical fare" if column == "fixed" and typical and w[column] < typical else "")
        return f'<div class="ad-a"><small>{label}</small><b>{e(w["name"])} · €{w[column]:.0f}</b><span>{e(why)}</span></div>'
    rows = "".join(f'<tr><td>{e(o["name"])}</td><td class="{"win" if o is flex else ""}">€{o["flexible"]:.0f}</td>'
                   f'<td class="{"win" if o is fixed else ""}">€{o["fixed"]:.0f}</td></tr>' for o in sorted(opts, key=lambda o: o["flexible"]))
    why_not = []  # the lowest number is not always the advice: say why
    for label, w, col in (("flexible", flex, "flexible"), ("fixed", fixed, "fixed")):
        cheapest = min(opts, key=lambda o: o[col])
        if cheapest is not w and cheapest["kind"] == "pass" and w[col] - cheapest[col] >= 1:
            why_not.append(f"The {cheapest['name']} is about €{w[col] - cheapest[col]:.0f} cheaper ({label} dates), but a pass is paid upfront and only "
                           "recommended when it is clearly cheaper.")
            break
    return (head + answer("If your dates are flexible", flex, "flexible") + answer("If your dates are fixed", fixed, "fixed")
            + f'<table><tr><th>Cost per {unit}</th><th>Flexible dates</th><th>Fixed dates</th></tr>{rows}</table>'
            + "".join(f"<p>{e(n)}</p>" for n in why_not + list(notes)) + "</div>")
