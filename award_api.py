"""Parser for AEGEAN's award flight response (`fromCalendarPage.json`): one day's flights with, per flight, the miles and cash taxes of
each fare family. Pure functions, no network: the response comes from the logged-in page (see award_collector.py)."""
# fare family code -> cabin. AEGEAN's "Economy" and "Business" tiles show the cheapest family of the cabin ("from 9,032"), so a cabin's
# miles are the minimum over its families, not the top one's.
CABINS = {"ECONOMY": "economy", "FLEX": "economy", "COMFORTFLE": "economy", "BUSINESS": "business", "BUSINESSSA": "business"}


def _amount(price, *path):
    for k in path:
        price = (price or {}).get(k)
    return (price or {}).get("amount")


def parse_flights(resp, i=0):
    """[{flight: 'A3830', stops: 0, aegean: True, fares: {'economy': (miles, taxes_eur), 'business': ...}}] for one day's response; `i` is the
    direction of a round-trip answer. Only AEGEAN's own message 7124 makes an answer without fares a day with no seats: any other gap raises,
    so it is reported and asked again instead of being stored as "no seats". So does a fare family not in CABINS."""
    model = resp["bom"]["modelObject"]
    bound = model["availabilities"]["upsell"]["bounds"][i]
    if "recommendations" not in bound and "7124" not in {m.get("code") for m in model.get("messages") or [] if isinstance(m, dict)}:
        raise KeyError("recommendations")
    recos = bound.get("recommendations") or {}
    out = {}
    for f in bound.get("flights") or ():
        segs = f["segments"]
        ids = [s["flightIdentifier"] for s in segs]
        out[f["id"]] = dict(flight=" ".join(f"{i['marketingAirline']}{i['flightNumber']}" for i in ids), stops=len(segs) - 1,
                            aegean=all(i["marketingAirline"] == "A3" for i in ids), fares={})
    for assoc in (bound.get("associations") or {}).values():
        reco = recos.get(str(assoc["recoId"]))
        if not reco:
            continue
        price = reco["recommendationPrice"]["price"]
        miles, taxes = _amount(price, "totalPrice", "milesAmount"), _amount(price, "totalTaxes", "cashAmount")
        for b in assoc["boundAssociations"]:
            if not miles or b["flightId"] not in out or (b["fareFamily"] not in CABINS and not out[b["flightId"]]["aegean"]):
                continue
            cabin = CABINS[b["fareFamily"]]  # an unknown family on an AEGEAN flight raises: on a partner's it changes nothing we store
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


def trusted_best(resp, i, lowest):
    """The nonstop rows of direction `i` of a round-trip answer, or None when only a one-way call can tell. Tested live on 2026-11-02: seats and
    taxes match one-way, but a direction can come back priced too high (25,000 for a 16,935 seat) or with no fare list. `lowest` is the leg's
    {cabin: lowest nonstop miles}: nonstop award fares are flat per leg, so anything above it is that error. Connections really do cost more,
    so a round trip cannot tell them from the error: it stores none, and they would only cost a one-way call each for nothing."""
    if "recommendations" not in resp["bom"]["modelObject"]["availabilities"]["upsell"]["bounds"][i]:
        return None
    best = {k: v for k, v in best_with_taxes(parse_flights(resp, i)).items() if k[1] == 1}
    return None if any(v and v[0] > lowest.get(c, v[0]) for (c, _), v in best.items()) else best


def pair_days(outs, backs):
    """([(out, back or None)], unpaired backs): each outbound day shares a call with the first free return day on or after it, since a
    round trip cannot come back before it leaves."""
    backs, pairs = sorted(backs), []
    for out in sorted(outs):
        back = next((d for d in backs if d >= out), None)
        if back:
            backs.remove(back)
        pairs.append((out, back))
    return pairs, backs


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


def calendar_fares(resp, i=0):
    """{iso day: 'FLEX 9032 30.44', or '-' for no seats} from the calendar part of a response (`i`: the direction of a round-trip answer). A
    day's flight request carries the 7 days around it, but each holds only its cheapest fare of any cabin and Star Alliance airline: enough to see that a day has seats or changed,
    not what it costs. A day that is not in here is unknown, not empty."""
    cal = ((resp.get("bom") or {}).get("modelObject") or {}).get("availabilities", {}).get("calendar")
    out = {}
    for x in cal["bounds"][i]["calendarDates"] if cal else ():
        assoc = x.get("calendarAssociation") or {}
        if set(assoc) - {"@c", "recommendation"}:  # another shape (PRICING_TYPE=O is one): unreadable is not "no seats"
            raise ValueError(f"unknown calendar day: {sorted(assoc)}")
        reco = assoc.get("recommendation")
        day = datetime.fromtimestamp(x["date"] / 1000, timezone.utc).date().isoformat()
        out[day] = "-"
        if reco:
            price = reco["recommendationPrice"]["price"]
            out[day] = f"{reco['fareFamily']} {_amount(price, 'totalPrice', 'milesAmount')} {_amount(price, 'totalTaxes', 'cashAmount')}"
    return out


def days_to_check(days, seen, fares, refresh=None):
    """Which of `days` (with seats) need a request: those whose calendar fare changed or that are new, plus the `refresh` checked longest
    ago, since a Business seat can go without the day's cheapest fare moving. `seen` is {day: (calendar fare, fetched)}; an empty stored
    fare is a row from before the calendar was stored, which counts as unchanged so the first run is not a full one."""
    changed, rest = [], []
    for d in days:
        (changed if d not in seen or seen[d][0] not in ("", fares.get(d, "")) else rest).append(d)
    return sorted(changed + sorted(rest, key=lambda d: seen[d][1])[:REFRESH if refresh is None else refresh])


# --- sending the request from inside the logged-in page --------------------------------------------------------------------------------
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode

PACE = (8.0, 11.0)  # seconds between requests, on top of AEGEAN's answer: 8-11 s held for about 400 calls on 2026-11-02; 4-6 s held for 40 calls; 2-3 s was blocked after 21, then 8-11 s after 9 more the same day: no pace is safe after a heavy day. Do not lower it
REFRESH = 10  # per leg and run: more means Business changes show up sooner, each one adds a call to the run

FETCH_JS = """async ({url, body}) => {
  const r = await fetch(url, {method: 'POST', credentials: 'same-origin',
                              headers: {'content-type': 'application/x-www-form-urlencoded;charset=UTF-8'}, body});
  return {status: r.status, ctype: r.headers.get('content-type') || '', text: await r.text()};
}"""


class Blocked(RuntimeError):
    """AEGEAN's bot protection refused a request: stop at once, retrying only makes it worse."""


def build_body(template, day, ticket, orig=None, dest=None, back=None):
    """The form body the page itself sent for one day, with the date and the page counter changed (the two fields that differ between the
    page's own day-to-day requests) and, when given, the route: one search serves every leg. `back` makes it a round trip with that return
    day, which the one-way session accepts: one call, both directions."""
    form = {k: v[0] for k, v in parse_qs(template, keep_blank_values=True).items()}
    data = json.loads(form["data"])
    stamp = lambda d: int(d.strftime("%Y%m%d0000")) if isinstance(data.get("B_DATE_1"), int) else d.strftime("%Y%m%d0000")  # keep the type the page itself used
    data["B_DATE_1"] = stamp(day)
    data["PAGE_TICKET"] = ticket if isinstance(data.get("PAGE_TICKET"), int) else str(ticket)
    if orig:
        data["B_LOCATION_1"], data["E_LOCATION_1"] = orig, dest
    if back:
        data.update(TRIP_TYPE="R", B_LOCATION_2=dest, E_LOCATION_2=orig, B_DATE_2=stamp(back))
    form["data"] = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return urlencode(form)


def template_day(template):
    """The date (yyyymmdd) the captured request asked for."""
    return str(json.loads(parse_qs(template, keep_blank_values=True)["data"][0])["B_DATE_1"])[:8]


def post_day(page, url, template, day, ticket, orig=None, dest=None, back=None):
    """One day's response, fetched from inside the page so the cookies and headers are the page's own. Anything that is not JSON
    (the bot check is an HTML page) stops the run."""
    r = page.evaluate(FETCH_JS, {"url": url, "body": build_body(template, day, ticket, orig, dest, back)})
    if r["status"] != 200 or "json" not in r["ctype"]:
        raise Blocked(f"AEGEAN answered HTTP {r['status']} ({r['ctype'] or 'no type'}) instead of data: a bot check or a throttle. Stopped; "
                      f"solve the check in the browser and wait some hours. It said: {' '.join(r['text'].split())[:300]}")
    data = json.loads(r["text"])
    codes = {m.get("code") for m in (data.get("bom", {}).get("modelObject") or {}).get("messages") or [] if isinstance(m, dict)}
    if codes & {"3010", "4004"}:  # the session is out of step: every further request would fail the same way
        raise Blocked(f"AEGEAN refused the request (error {sorted(codes)}). Stopped; run again.")
    return data
