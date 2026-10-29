"""Parser for AEGEAN's award flight response (`fromCalendarPage.json`): one day's flights with, per flight, the miles and cash taxes of
each fare family. Pure functions, no network: the response comes from the logged-in page (see award_collector.py)."""
# fare family code -> cabin. AEGEAN's "Economy" and "Business" tiles show the cheapest family of the cabin ("from 9,032"), so a cabin's
# miles are the minimum over its families, not the top one's.
CABINS = {"ECONOMY": "economy", "FLEX": "economy", "COMFORTFLE": "economy", "BUSINESS": "business", "BUSINESSSA": "business"}


def _amount(price, *path):
    for k in path:
        price = (price or {}).get(k)
    return (price or {}).get("amount")


def parse_flights(resp):
    """[{flight: 'A3830', stops: 0, aegean: True, fares: {'economy': (miles, taxes_eur), 'business': ...}}] for one day's response."""
    bound = resp["bom"]["modelObject"]["availabilities"]["upsell"]["bounds"][0]
    recos = bound["recommendations"]
    out = {}
    for f in bound["flights"]:
        segs = f["segments"]
        ids = [s["flightIdentifier"] for s in segs]
        out[f["id"]] = dict(flight=" ".join(f"{i['marketingAirline']}{i['flightNumber']}" for i in ids), stops=len(segs) - 1,
                            aegean=all(i["marketingAirline"] == "A3" for i in ids), fares={})
    for assoc in bound["associations"].values():
        reco = recos.get(str(assoc["recoId"]))
        if not reco:
            continue
        price = reco["recommendationPrice"]["price"]
        miles, taxes = _amount(price, "totalPrice", "milesAmount"), _amount(price, "totalTaxes", "cashAmount")
        for b in assoc["boundAssociations"]:
            cabin = CABINS.get(b["fareFamily"])
            if cabin and miles and b["flightId"] in out:
                fares = out[b["flightId"]]["fares"]
                if cabin not in fares or miles < fares[cabin][0]:
                    fares[cabin] = (miles, taxes)
    return list(out.values())


def best_with_taxes(flights):
    """Like best_by_cabin, but each value is (miles, cash taxes in EUR) of the cheapest-miles fare, or None: the taxes are what the award
    ticket costs on top of the miles."""
    best = {(c, n): None for c in ("economy", "business") for n in (0, 1)}
    for f in flights:
        if not f["aegean"]:
            continue
        for cabin, (miles, taxes) in f["fares"].items():
            for n in ((0, 1) if f["stops"] == 0 else (0,)):
                if miles and (best[(cabin, n)] is None or miles < best[(cabin, n)][0]):
                    best[(cabin, n)] = (int(miles), taxes)
    return best


def best_by_cabin(flights):
    """{(cabin, nonstop): cheapest miles} over AEGEAN-only flights, in the shape award_collector.save_csv rows are built from;
    nonstop=1 counts nonstop flights only, nonstop=0 any all-AEGEAN itinerary. None when no seat."""
    best = {(c, n): None for c in ("economy", "business") for n in (0, 1)}
    for f in flights:
        if not f["aegean"]:
            continue
        for cabin, (miles, _) in f["fares"].items():
            for n in ((0, 1) if f["stops"] == 0 else (0,)):
                if miles and (best[(cabin, n)] is None or miles < best[(cabin, n)]):
                    best[(cabin, n)] = int(miles)
    return best


# --- sending the request from inside the logged-in page --------------------------------------------------------------------------------
import json
from urllib.parse import parse_qs, urlencode

PACE = (25.0, 45.0)  # seconds between requests: AEGEAN's limiter challenged a person after 4 requests in 11 s

FETCH_JS = """async ({url, body}) => {
  const r = await fetch(url, {method: 'POST', credentials: 'same-origin',
                              headers: {'content-type': 'application/x-www-form-urlencoded;charset=UTF-8'}, body});
  return {status: r.status, ctype: r.headers.get('content-type') || '', text: await r.text()};
}"""


class Blocked(RuntimeError):
    """AEGEAN's bot protection refused a request: stop at once, retrying only makes it worse."""


def build_body(template, day, ticket):
    """The form body the page itself sent for one day, with only the date and the page counter changed (the two fields that differ
    between the page's own day-to-day requests)."""
    form = {k: v[0] for k, v in parse_qs(template, keep_blank_values=True).items()}
    data = json.loads(form["data"])
    stamp = day.strftime("%Y%m%d") + "0000"
    data["B_DATE_1"] = int(stamp) if isinstance(data.get("B_DATE_1"), int) else stamp  # keep the type the page itself used
    data["PAGE_TICKET"] = ticket if isinstance(data.get("PAGE_TICKET"), int) else str(ticket)
    form["data"] = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return urlencode(form)


def template_day(template):
    """The date (yyyymmdd) the captured request asked for."""
    return str(json.loads(parse_qs(template, keep_blank_values=True)["data"][0])["B_DATE_1"])[:8]


def post_day(page, url, template, day, ticket):
    """One day's response, fetched from inside the page so the cookies and headers are the page's own. Anything that is not JSON
    (the bot check is an HTML page) stops the run."""
    r = page.evaluate(FETCH_JS, {"url": url, "body": build_body(template, day, ticket)})
    if r["status"] != 200 or "json" not in r["ctype"]:
        raise Blocked(f"AEGEAN answered HTTP {r['status']} ({r['ctype'] or 'no type'}) instead of data: a bot check or a throttle. Stopped; "
                      "solve the check in the browser and wait some hours.")
    return json.loads(r["text"])
