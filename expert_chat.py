"""Virtual expert: the Overview's answer for one search, explained. facts() works the recommended trip out with the very functions the
Overview's boarding pass uses (deals.best_trip, deals.trip_context, price_insights, Sky Express, the hotel), with the same filters, so the two
pages can never disagree; sentences() says it in three plain sentences; an AI model writes the same three sentences in its own words (the
briefing) and answers follow-up questions in a chat that remembers the conversation, from those facts and nothing else.
Two providers, picked by which key is in the Streamlit secrets (or the environment):
- GEMINI_API_KEY: Google Gemini on its free tier (no card; create a key at aistudio.google.com). Used first, because it costs nothing.
- ANTHROPIC_API_KEY: Claude Haiku 5.5, about a tenth of a cent a question.
With neither, or when the model is busy or out of quota, the page shows sentences() instead, so it never shows nothing. The app is public,
so a daily cap stops strangers from using up the key.
A new module name on purpose (it replaces expert.py and ai_expert.py): Streamlit Cloud keeps already loaded modules across redeploys."""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import deals
import price_insights as pi
import skyexpress as sx
from award_view import load_award
from insights import DAYS, cash_by_day
from trip_cost import night_prices
from trip_plan import city

VERSION = 4  # pages reload this module when Streamlit Cloud still has an older copy loaded (it keeps modules across redeploys)
CLAUDE = "claude-haiku-5-5"
GEMINI = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]  # the second answers when the first is busy or out of free quota
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
MAX_PER_DAY = 60  # ponytail: in-process counter, resets when Cloud restarts the app; the provider's own limits are the hard cap
TURNS = 10  # messages of the conversation sent back each time; older ones are dropped to keep requests small
NIGHTS = [2, 3, 4, 5, 6, 7]  # the stays the Overview's pass and date grid consider
PAY = {"cash": "Cash €", "miles": "Miles"}  # the Overview's words
GOOD_MILES = 10  # euros of cash fare (after award taxes) that 1,000 miles should replace for an award to be called a good use
SYSTEM = """You are a calm, practical travel-price analyst for one traveller who flies AEGEAN.
The fares, award miles and hotel prices this app has collected are in <data> below. The traveller asks questions; follow-ups refer to
earlier answers in the conversation.

Rules:
- <data> names one recommended trip: the same one the app's Overview page shows. Explain it; do not propose a different trip unless the
  traveller asks for different constraints (other dates, nights, weekdays, cabin or way of paying).
- Use only the data given. Quote dates (weekday + day + month) and euro amounts from it. Never invent a price, a route or a date.
- When asked about a route, cabin or dates that are not in the data, say which fares you do have and where to get the rest, in one sentence
  (for example "I only have fares up to 29 January; search that month on the Overview"). Do not guess.
- Lead with the answer in one or two sentences, then at most five short bullets of evidence. No headings, no tables.
- When miles come up, say "N miles plus taxes instead of €X" for the same dates; never compute euros per 1,000 miles.
- Prices change daily; recommend checking the price on aegeanair.com before booking."""
BRIEFING = ("Write today's briefing for this search as exactly three plain sentences in one paragraph, no bullets, labels or headings: "
            "(1) the recommended trip with its dates, nights and price; (2) why, from the usual range, the weekday and how far ahead it is; "
            "(3) the one alternative <data> names (Sky Express when it is cheaper, the cash fare when paying with miles, otherwise the "
            "next-cheapest week). Use only numbers that are in <data>. No introduction and no closing line.")

CARD = ("<style>.st-key-brief{background:#fff;border:0 !important;border-left:4px solid #2563C9 !important;border-radius:14px;"
        "box-shadow:0 1px 2px rgba(0,0,0,.04),0 6px 20px rgba(0,0,0,.05);padding:16px 20px}.st-key-brief ul{margin-bottom:.2rem}</style>")

_used = {}


# --- the facts: the Overview's numbers for one search ----------------------------------------------------------------------------------
def facts(db, orig, dest, cabin, pay, nights, wdays, period, today=None, hotel=None, hist=None, oneway=False):
    """Everything the briefing and the chat may say about one search, as one dict, from the functions the Overview's pass uses with the
    same filters: `best` is deals.best_trip (depart, back, nights, price; None without a fare), `ctx` deals.trip_context (low, typical,
    days), `insight` price_insights.trip (cash only), `skyexpress` its best trip when it has fares (economy cash only), `hotel` (name,
    adults, price) for the pass's nights when `hotel` (a trip_plan.hotels entry) has every night priced, `other` the cheapest trip leaving
    in another week (cash), and for miles `award` (the cash fare and award taxes of the same dates) and `cash` (the cheapest cash trip).
    `nights` and `wdays` empty mean no constraint, as on the Overview; `period` is (first, last departure day); `hist` is the price history
    (price_insights.history() when None). `oneway`: price no way back, whatever is saved."""
    today = today or date.today()
    start, end = period
    stays, wdays = sorted(nights) or NIGHTS, list(wdays) or DAYS
    far = end + timedelta(max(stays))  # the way back can be up to the longest stay after the last departure
    cash = lambda o, d, a, b: cash_by_day(db, o, d, cabin, True, a, b)
    if pay == "cash":
        out, back = cash(orig, dest, start, end), cash(dest, orig, start, far)
    else:
        m_out, t_out, _ = load_award(db, orig, dest, True, start, end)
        m_back, t_back, _ = load_award(db, dest, orig, True, start, far)
        pick = lambda frame: frame[cabin] if cabin in frame else pd.Series(dtype=float)
        out, back = pick(m_out), pick(m_back)
    if oneway:
        back = None
    best = deals.best_trip(out, back, start, end, stays, wdays)
    f = dict(orig=orig, dest=dest, cabin=cabin, pay=pay, nights=stays, wdays=wdays, start=start, end=end, today=today, best=best,
             ctx=deals.trip_context(out, back, start, end), insight=None, skyexpress=None, hotel=None, other=None, award=None, cash=None)
    if best is None:
        return f
    ret = best["back"] is not None
    if pay == "cash":
        hist = pi.history() if hist is None else hist
        f["insight"] = pi.trip(hist, [(orig, dest)] + ([(dest, orig)] if ret else []), cabin, best["price"], today)
        if cabin == "economy" and sx.serves(orig, dest):
            gq_out = sx.cash_by_day(db, orig, dest, start, end)
            if len(gq_out):
                f["skyexpress"] = deals.best_trip(gq_out, sx.cash_by_day(db, dest, orig, start, far) if ret else None, start, end, stays, wdays)
        if ret and hotel:
            night = night_prices(db, hotel["query"], hotel["filt"], hotel["adults"], best["depart"], best["back"])
            stay = [(best["depart"] + timedelta(i)).isoformat() for i in range(best["nights"])]
            if all(s in night.index for s in stay):
                f["hotel"] = dict(name=hotel["name"], adults=hotel["adults"], price=float(sum(night[s] for s in stay)))
        f["other"] = _other_week(out, back, best, start, end, stays, wdays)
    else:
        c_out, c_back = cash(orig, dest, start, end), cash(dest, orig, start, far)
        days = [(best["depart"], c_out, pick(t_out))] + ([(best["back"], c_back, pick(t_back))] if ret else [])
        money = [c.get(d.isoformat()) for d, c, _ in days]
        taxes = [t.get(d.isoformat()) for d, _, t in days]
        f["award"] = dict(cash=float(sum(money)) if all(v is not None and pd.notna(v) for v in money) else None,
                          taxes=float(sum(taxes)) if all(v is not None and pd.notna(v) for v in taxes) else None)
        f["cash"] = deals.best_trip(c_out, c_back, start, end, stays, wdays)
    return f


def _other_week(out, back, best, start, end, stays, wdays):
    """The cheapest trip with the same filters leaving in another week (Mon–Sun) than `best`, or None: the alternative when the dates
    do not suit. deals.best_trip over the window before that week and the window after it."""
    monday = best["depart"] - timedelta(best["depart"].weekday())
    found = [deals.best_trip(out, back, a, b, stays, wdays) for a, b in ((start, monday - timedelta(1)), (monday + timedelta(7), end)) if a <= b]
    found = [t for t in found if t]
    return min(found, key=lambda t: t["price"]) if found else None


def _when(d, today):
    """Monday 18 January, with the year when it is not this year."""
    return f"{d:%A} {d.day} {d:%B}" + (f" {d.year}" if d.year != today.year else "")


def _money(f, v):
    return f"€{v:,.0f}" if f["pay"] == "cash" else f"{v:,.0f} miles"


def _dates(f, t):
    """"leave Monday 18 January, back Thursday 21 January, 3 nights" (or "leaving ..." one way) for a best_trip answer."""
    if t["back"] is None:
        return f"leaving {_when(t['depart'], f['today'])}"
    return f"leave {_when(t['depart'], f['today'])}, back {_when(t['back'], f['today'])}, {t['nights']} night{'s' if t['nights'] != 1 else ''}"


def sentences(f):
    """The briefing as three plain sentences built from facts() alone, no model: (a) the recommended trip with dates, nights and price,
    (b) why (the usual range or the typical price, the weekday, how far ahead), (c) the alternative that matters (Sky Express when cheaper,
    the cash fare when paying with miles, otherwise the next-cheapest week). One sentence when there is no fare for the filters."""
    best, kind = f["best"], "fares" if f["pay"] == "cash" else "award seats"
    where = f"{city(f['orig'])} to {city(f['dest'])}"
    if best is None:
        return [f"No {f['cabin']} {kind} are saved from {where} for these days and nights. Try other weekdays or nights, or search the route "
                "on the Flights page."]
    ret = best["back"] is not None
    trip = f"{f['cabin']} {'return' if ret else 'one-way'}"
    price = _money(f, best["price"])
    if f["pay"] == "miles":
        award = f["award"] or {}
        price += f" plus €{award['taxes']:,.0f} taxes" if award.get("taxes") is not None else " plus taxes"
        price += f" instead of €{award['cash']:,.0f} in cash" if award.get("cash") is not None else ""
    hotel = f" ({f['hotel']['name']} for those nights adds €{f['hotel']['price']:,.0f}, so €{best['price'] + f['hotel']['price']:,.0f} in all)" if f["hotel"] else ""
    first = f"The cheapest {trip} from {where} with these filters is {price}: {_dates(f, best)}{hotel}."
    ahead = (best["depart"] - f["today"]).days
    ins, ctx = f["insight"], f["ctx"]
    if ins and ins.get("level"):
        why = (f"That is a {ins['level']} fare for this route, which usually costs €{ins['low']:,.0f}–€{ins['high']:,.0f} "
               f"{'return' if ret else 'one way'}")
    elif ctx and abs(best["price"] - ctx["typical"]) <= ctx["typical"] * 0.05:
        why = f"Prices are flat in this period: a typical {'trip' if ret else 'day'} costs {_money(f, ctx['typical'])}"
    elif ctx:
        why = f"A typical {'trip' if ret else 'day'} in this period costs {_money(f, ctx['typical'])}, so this saves {_money(f, ctx['typical'] - best['price'])}"
    else:
        why = "It is the lowest price saved for this period"
    second = f"{why}; it leaves on a {best['depart']:%A}, {ahead} day{'s' if ahead != 1 else ''} from today."
    gq, other, cash = f["skyexpress"], f["other"], f["cash"]
    if f["pay"] == "miles":
        award = f["award"] or {}
        if cash is None:
            third = "No cash fares are saved for these filters to compare the miles with."
        else:
            worth = (award.get("cash") is not None and (award["cash"] - (award.get("taxes") or 0)) / best["price"] * 1000 >= GOOD_MILES)
            third = (f"Paying cash, the cheapest trip with these filters is €{cash['price']:,.0f} ({_dates(f, cash)}), "
                     + ("so the miles are a good use here." if worth else "which is the better deal: keep the miles."))
    elif gq is not None and gq["price"] < best["price"]:
        third = (f"Sky Express is cheaper: €{gq['price']:,.0f} ({_dates(f, gq)}, economy), €{best['price'] - gq['price']:,.0f} less than AEGEAN.")
    elif other is not None:
        third = f"If those dates do not suit, the next-cheapest week is {_money(f, other['price'])}: {_dates(f, other)}."
    else:
        third = "No trip in another week is saved for these filters."
    return [first, second, third]


def text(f):
    """facts() as the <data> block the model reads: the search, the recommended trip, and every number the three sentences may quote."""
    best = f["best"]
    stays = f"{f['nights'][0]}–{f['nights'][-1]} nights" if len(f["nights"]) > 1 else f"{f['nights'][0]} nights"
    days = "any day" if len(f["wdays"]) == 7 else "/".join(d for d in DAYS if d in f["wdays"])
    lines = [f"Today: {f['today']:%a %d %b %Y}. Search: {city(f['orig'])} ({f['orig']}) to {city(f['dest'])} ({f['dest']}), {f['cabin']}, "
             f"paying with {PAY[f['pay']].replace(' €', '')}, departures {f['start']:%a %d %b %Y} to {f['end']:%a %d %b %Y}, return of {stays} "
             f"when fares are saved both ways, leaving {days}. One adult, AEGEAN nonstop; cash fares from Google Flights, award miles from "
             "AEGEAN Miles+Bonus (plus taxes).",
             f"Fares are saved up to {f['end']:%a %d %b %Y}; nothing after that is known."]
    if best is None:
        lines.append("Recommended trip: none, no fare is saved for these filters.")
        return "\n".join(lines)
    lines.append(f"Recommended trip (the one the Overview's boarding pass shows): {_dates(f, best)}, {_money(f, best['price'])}.")
    if f["ctx"]:
        lines.append(f"Trips in this period with these filters: typical {_money(f, f['ctx']['typical'])} (half the trips cost less), lowest "
                     f"{_money(f, f['ctx']['low'])}, over {f['ctx']['days']} departure days.")
    ins = f["insight"]
    if ins and ins.get("level"):
        lines.append(f"Fare verdict: {ins['level']}; this route usually costs €{ins['low']:,.0f}–€{ins['high']:,.0f} for such a trip "
                     f"(from {ins['snapshots']} days of price history since {ins['since']:%d %b}).")
    elif ins and ins.get("since"):
        lines.append(f"Fare verdict: none yet, the route is tracked since {ins['since']:%d %b} ({ins['snapshots']} days of prices).")
    if f["hotel"]:
        lines.append(f"Hotel: {f['hotel']['name']} ({f['hotel']['adults']} adults) for those nights €{f['hotel']['price']:,.0f}, "
                     f"so €{best['price'] + f['hotel']['price']:,.0f} for the whole trip.")
    if f["skyexpress"] is not None:
        gq = f["skyexpress"]
        lines.append(f"Sky Express (economy): cheapest {_dates(f, gq)}, €{gq['price']:,.0f}, "
                     f"{'cheaper' if gq['price'] < best['price'] else 'dearer'} than AEGEAN by €{abs(best['price'] - gq['price']):,.0f}.")
    if f["other"] is not None:
        lines.append(f"Next-cheapest trip in another week: {_dates(f, f['other'])}, {_money(f, f['other']['price'])}.")
    if f["award"]:
        a = f["award"]
        lines.append("Same dates in cash: " + (f"€{a['cash']:,.0f}" if a["cash"] is not None else "not saved")
                     + "; award taxes: " + (f"€{a['taxes']:,.0f}" if a["taxes"] is not None else "not collected") + ".")
    if f["cash"] is not None:
        lines.append(f"Cheapest cash trip with these filters: {_dates(f, f['cash'])}, €{f['cash']['price']:,.0f}.")
    lines.append("Briefing in plain words: " + " ".join(sentences(f)))
    return "\n".join(lines)


def prompt(f):
    """The briefing request: it names the recommended trip so the model explains that one and no other."""
    return f"The recommended trip is: {sentences(f)[0]} Explain it; do not propose a different one unless I ask for different constraints.\n\n{BRIEFING}"


def available(secrets=None):
    """(provider, key) for the first configured provider, Gemini (free) before Claude; None when there is no key."""
    for provider, name in (("gemini", "GEMINI_API_KEY"), ("claude", "ANTHROPIC_API_KEY")):
        try:
            key = (secrets or {}).get(name)
        except Exception:  # st.secrets raises when no secrets file exists at all
            key = None
        if key := key or os.environ.get(name):
            return provider, key
    return None


def label(cfg):
    return "Google Gemini (free tier)" if cfg and cfg[0] == "gemini" else "Claude Haiku"


def left_today():
    return MAX_PER_DAY - _used.get(date.today(), 0)


def _system(facts):
    return f"{SYSTEM}\n\n<data>\n{facts}\n</data>"


def _gemini(history, facts, key, urlopen):
    """The whole answer from the first Gemini model that answers. Raises RuntimeError with a plain message otherwise."""
    def body(think):
        config = {"maxOutputTokens": 16000}  # thinking counts toward the limit
        if think:
            config["thinkingConfig"] = {"thinkingLevel": "low"}  # the default (medium) took over 90 s for the briefing
        return json.dumps({"systemInstruction": {"parts": [{"text": _system(facts)}]},
                           "contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                                        for m in history],
                           "generationConfig": config}).encode()

    def post(model, think):
        req = urllib.request.Request(GEMINI_URL.format(model), data=body(think), method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})  # header, so the key is never in a URL or log
        with urlopen(req, timeout=60) as r:
            return json.load(r)

    last = None
    for model in GEMINI:
        try:
            try:
                res = post(model, True)
            except urllib.error.HTTPError as err:
                if err.code != 400:
                    raise
                res = post(model, False)  # a model that does not take thinkingLevel: ask again without it
        except urllib.error.HTTPError as err:
            last = err.code
            if err.code in (404, 429, 500, 503):  # model gone, free quota used up, or busy: try the next one
                continue
            raise RuntimeError(f"Gemini refused the request (HTTP {err.code}). Check GEMINI_API_KEY in the app's secrets.") from None
        except (TimeoutError, urllib.error.URLError):
            last = "timeout"
            continue
        cand = (res.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
        if not text:
            raise RuntimeError("The expert declined to answer that. Try asking about prices, dates or miles.")
        return text + ("\n\n(The answer was cut short.)" if cand.get("finishReason") == "MAX_TOKENS" else "")
    raise RuntimeError("Gemini's free quota is used up for now. Try again later." if last == 429 else
                       "Gemini did not answer in time. Try again in a minute." if last == "timeout" else f"Gemini is unavailable right now (HTTP {last}).")


def _claude(history, facts, key, client):
    if client is None:
        import anthropic  # only needed when Claude is the provider
        client = anthropic.Anthropic(api_key=key)
    with client.messages.stream(model=CLAUDE, max_tokens=16000, system=_system(facts), output_config={"effort": "low"},
                                messages=[{"role": m["role"], "content": m["content"]} for m in history]) as stream:
        yield from stream.text_stream
        final = stream.get_final_message()
    if final.stop_reason == "refusal":
        raise RuntimeError("The expert declined to answer that. Try asking about prices, dates or miles.")
    if final.stop_reason == "max_tokens":
        yield "\n\n(The answer was cut short.)"


def chat(history, facts, cfg, client=None, urlopen=urllib.request.urlopen):
    """Yield the reply to the last user message of `history` ([{'role': 'user'|'assistant', 'content'}], oldest first). Claude streams;
    Gemini arrives in one piece. Raises RuntimeError with a plain message when the daily cap is reached or the model declines."""
    if left_today() <= 0:
        raise RuntimeError(f"The expert has answered {MAX_PER_DAY} questions today, its daily limit. Try again tomorrow.")
    _used[date.today()] = _used.get(date.today(), 0) + 1
    history = history[-TURNS:]
    while history and history[0]["role"] != "user":  # both APIs want the conversation to start with the traveller
        history = history[1:]
    provider, key = cfg
    if provider == "gemini":
        yield _gemini(history, facts, key, urlopen)
    else:
        yield from _claude(history, facts, key, client)


@st.cache_data(ttl=24 * 3600, show_spinner=False, max_entries=32)
def _briefing(ask, data, _cfg, day):  # keyed on the facts and the day (the key is not hashed); one model call per search per day
    return "".join(chat([{"role": "user", "content": ask}], data, _cfg))  # an exception is not cached


_failed = {}


def briefing(f, cfg, day):
    """Today's briefing for facts() `f`: (three sentences, note). The model's words when it answers, with note None; otherwise the same
    three sentences built in Python (sentences()), with a note saying why, so the page never shows nothing and never contradicts the
    Overview. Without a fare for the filters, or without a key, no question is spent. ponytail: cached in memory per search and day, so a
    restart of the app asks again. A failure is remembered for 15 minutes, so a broken key or a used-up quota does not spend a question (and
    a long wait) on every page view, but is retried soon after."""
    plain = " ".join(sentences(f))
    if f["best"] is None:
        return plain, None
    if cfg is None:
        return plain, "Written from the saved fares. With an AI key the expert puts it in its own words and answers questions."
    data = text(f)
    k = (data, day)
    if k in _failed and time.time() - _failed[k][0] < 900:
        return plain, _failed[k][1]
    try:
        return _briefing(prompt(f), data, cfg, day), None
    except Exception as err:  # never crash a page for the briefing: the plain sentences say the same
        msg = "Written from the saved fares. " + (str(err) if isinstance(err, RuntimeError) else
                                                 f"The expert is unavailable right now ({type(err).__name__}).")
        _failed[k] = (time.time(), msg)
        return plain, msg
