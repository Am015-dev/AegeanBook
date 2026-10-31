"""Expert: the Overview's answer for your route (cheapest trip, dates, nights, price) explained in three plain sentences, the links to
book it, then follow-up questions. The numbers come from the same functions as the Overview's boarding pass, with the same filters
(expert_chat.facts), so the two pages never disagree; the words come from an AI model, or from plain Python when the model is unavailable.
The suggested questions under the briefing (pills) are answered in plain Python, instantly and with or without a key; typed questions go to
the model, which reads the same answers in its data."""
import importlib
from datetime import date, timedelta

import streamlit as st

import booking
import expert_chat as ex
import fetch_ahead as fa
import overview as ov
import trip_plan as tp
from db import connect
from insights import DAYS
from ui import route

if getattr(ex, "VERSION", 0) < 5:  # Streamlit Cloud keeps an older copy of a module across redeploys
    ex = importlib.reload(ex)
if getattr(ov, "VERSION", 0) < 4:
    ov = importlib.reload(ov)
if getattr(tp, "VERSION", 0) < 6:
    tp = importlib.reload(tp)
if getattr(fa, "VERSION", 0) < 1:
    fa = importlib.reload(fa)
db = connect()
today = date.today()
route("Expert", None, "Why this is the trip to book, and what else would work.")
try:
    cfg = ex.available(st.secrets)
except Exception:
    cfg = ex.available()

fares, summary, trips, weekday, hotel_night, awards = ov.collect(db, today)
froms = list(dict.fromkeys(o for o, _ in fares))  # the Overview's order, so both pages open on the same route
if not summary:
    st.info("No fares are saved yet, so there is nothing to analyse. The daily run fills them in every morning.")
    st.stop()
q = st.query_params
if q.get("from") in froms:  # from the Overview's "Ask the expert about it": open on exactly the pass the traveller saw
    st.session_state["plan-orig"] = q["from"]
    if (q["from"], q.get("to")) in fares:
        st.session_state[f"plan-dest-{q['from']}"] = q["to"]
    if q.get("cabin") in ("economy", "business"):
        st.session_state["plan-cabin"] = q["cabin"]
    if q.get("pay") in ex.PAY:
        st.session_state["plan-pay"] = q["pay"]
    stays = [int(n) for v in q.get_all("nights") for n in v.replace("-", ",").split(",") if n.strip().isdigit()]  # ?nights=2-7, or 2,3
    if stays and all(1 <= n <= 60 for n in stays):
        st.session_state["plan-nights"] = (min(stays), max(stays))
    if q.get("trip") in ("return", "oneway"):
        st.session_state["plan-trip"] = q["trip"]
asked_when = q.get("when")  # the period is matched once the list exists, below
if q.get("from") in froms:
    q.clear()
for key, value in (("plan-cabin", "economy"), ("plan-pay", "cash"), ("plan-nights", (2, 7)), ("plan-trip", "return"), ("plan-days", DAYS)):
    st.session_state.setdefault(key, value)  # the Overview's defaults; set here, not as widget defaults, so a link can preset them

with st.container(border=True, key="search"):
    st.markdown('<p class="group">Your trip</p>', unsafe_allow_html=True)
    c = st.columns([1.2, 1.2, 1])
    place = lambda a: f"{tp.city(a)} ({a})"
    orig = c[0].selectbox("From", froms, format_func=place, key="plan-orig",
                          help="Every airport with fares saved: the daily routes, plus any route you searched on the Flights page.")
    dest = c[1].selectbox("To", [d for o, d in fares if o == orig], format_func=place, key=f"plan-dest-{orig}")
    last = date.fromisoformat(db.execute("SELECT MAX(day) FROM prices WHERE price IS NOT NULL").fetchone()[0])  # the last day with fares, any route
    try:  # ?when=2026-12-12,2026-12-18 from the Overview's link: the same days the pass was for
        a, b = (date.fromisoformat(x) for x in asked_when.split(","))
        if today < a <= b <= today + timedelta(tp.HORIZON):
            st.session_state["plan-when"] = (a, b)
    except (AttributeError, ValueError, TypeError):
        pass
    when = c[2].date_input("Leaving between", (today + timedelta(1), today + timedelta(30)), min_value=today + timedelta(1),
                           max_value=today + timedelta(tp.HORIZON), key="plan-when", format="DD/MM/YYYY")
    when = tuple(when) if isinstance(when, (tuple, list)) else (when,)
    c = st.columns([1, 1, 1, 1.6])
    cabin = c[0].pills("Cabin", ["economy", "business"], format_func=str.capitalize, required=True, key="plan-cabin")
    pay = c[1].pills("Pay with", list(ex.PAY), format_func=ex.PAY.get, required=True, key="plan-pay")
    trip = c[2].pills("Trip", ["return", "oneway"], format_func={"return": "Return", "oneway": "One way"}.get, required=True, key="plan-trip")
    nights = (2, 7)
    if trip == "return":
        nights = c[3].slider("Nights", 1, 60, key="plan-nights", help="How long to stay, shortest to longest you would accept.")
    wdays = st.pills("Days I can leave", DAYS, selection_mode="multi", key="plan-days")
    saved = tp.hotels(db, dest)
    hotel = st.selectbox("Hotel", [None] + saved, key=f"plan-hotel-{dest}", index=1 if saved else 0,
                         format_func=lambda h: "No hotel, flights only" if h is None else f"{h['name']} · {h['adults']} adults",
                         help="Hotels you searched on the Hotels page (one night) in this city, the most recent first; added to a cash return.")
    if not saved:
        st.caption(f"No hotel in {tp.city(dest)} searched yet: search one on the Hotels page with 1 night, and it appears here.")
    p = tp.period(when[0], when[-1], last, today)
    stays = list(range(nights[0], nights[1] + 1))
    # no fares saved for this route in the period (either way): one button fetches them on GitHub, the way back `stays` later
    todo, blank = fa.missing(db, [(orig, dest, p.start, p.end), (dest, orig, p.start + timedelta(min(stays)), p.end + timedelta(max(stays)))], today)
    if blank and pay == "cash":  # award miles cannot be fetched from here: they are collected on the owner's computer, 90 days ahead
        fa.button(f"ahead|{orig}|{dest}|{p.start}", f"Search {p.name} on GitHub", todo,
                  f"{p.name.capitalize()} from {tp.city(orig)} to {tp.city(dest)}")

f = ex.facts(db, orig, dest, cabin, pay, stays, wdays, (p.start, p.end), today, hotel, oneway=trip == "oneway")
# the chat reads the same facts, then every tracked route for follow-ups (without the Overview's Frankfurt trip totals, which include a hotel)
data = ex.text(f) + "\n\nAll tracked routes, for follow-ups (one-way fares):\n" + ov.facts(summary, fares, {}, hotel_night, awards, today)
chat = st.session_state.setdefault("expert-chat", [])
search = (orig, dest, cabin, pay, trip, tuple(stays), tuple(wdays or DAYS), p.start, p.end, hotel and hotel["name"])
if st.session_state.get("expert-search") not in (None, search):  # a new search: the old answers were about other numbers
    chat.clear()
st.session_state["expert-search"] = search

st.markdown(ex.CARD, unsafe_allow_html=True)
with st.container(border=True, key="brief"):
    st.markdown('<p class="group">Today’s briefing</p>', unsafe_allow_html=True)
    with st.spinner("Reading today’s prices…"):
        take, note = ex.briefing(f, cfg, today.isoformat())
    st.markdown(take)
    if note:
        st.caption(note)
    if f["best"] is not None:  # book it from here: the Overview's two links, for the very trip the briefing names
        links = dict(depart=f["best"]["depart"], back=f["best"]["back"], cabin=cabin)
        c = st.columns(2)
        c[0].link_button("Book on aegeanair.com", booking.aegean_url(orig, dest, **links), type="primary", width="stretch",
                         icon=":material/airplane_ticket:")
        c[1].link_button("Check it on Google Flights", booking.google_url(orig, dest, **links), width="stretch", icon=":material/open_in_new:")


def show(role, text):
    with st.chat_message(role, avatar=":material/person:" if role == "user" else ":material/auto_awesome:"):
        st.markdown(text)


def answer(q, data):
    """Show the question and the expert's answer; keep both only once answered."""
    show("user", q)
    with st.chat_message("assistant", avatar=":material/auto_awesome:"):
        try:
            with st.spinner("Thinking…"):
                a = st.write_stream(ex.chat(chat + [{"role": "user", "content": q}], data, cfg))
            # saved only once answered, so a question cut off by a new one (or an error) does not linger without its answer
            chat.extend([{"role": "user", "content": q}, {"role": "assistant", "content": a if isinstance(a, str) else "".join(map(str, a))}])
        except Exception as err:  # never crash the page for an API problem; say what happened
            st.error(str(err) if isinstance(err, RuntimeError) else f"The expert is unavailable right now ({type(err).__name__}).")


def picked():
    """A suggested question was clicked: remember it for this run and clear the pill, so the same one can be asked again later."""
    st.session_state["expert-pending"] = st.session_state.get("expert-ask")
    st.session_state["expert-ask"] = None


asks = ex.questions(f)
if asks:
    st.markdown('<p class="group">Ask about it</p>', unsafe_allow_html=True)
    st.pills("Suggested questions", asks, key="expert-ask", on_change=picked, label_visibility="collapsed")
for m in chat:
    show(m["role"], m["content"])
pending = st.session_state.pop("expert-pending", None)
if pending in asks:  # answered from the facts, instantly, with or without a key; the model reads the same answer for follow-ups
    a = ex.answer(f, pending)
    show("user", pending)
    show("assistant", a)
    chat.extend([{"role": "user", "content": pending}, {"role": "assistant", "content": a}])

if cfg:
    q = st.chat_input("Ask about these dates, or anything about fares, miles or hotels", max_chars=500)
    if q:
        answer(q, data)
else:
    st.info("The expert needs an AI key, set once by the app's owner, to explain in its own words and answer typed questions.")
    st.markdown("**Free:** create a Google Gemini key at aistudio.google.com (no card needed) and add `GEMINI_API_KEY = \"...\"` under "
                "**Settings → Secrets** of this app on Streamlit Cloud.\n\n"
                "**Paid alternative:** Claude Haiku, about €0.001 a question: add `ANTHROPIC_API_KEY = \"...\"` instead. When both are set, "
                f"the free Gemini key is used. Either way the app stops after {ex.MAX_PER_DAY} questions a day.")
if chat and st.button("New conversation", icon=":material/refresh:"):
    chat.clear()
    st.rerun()
if cfg:
    st.caption(f"The suggested questions are answered from the prices saved by the daily run; typed ones by {ex.label(cfg)}, which can be "
               f"wrong: check the fare on aegeanair.com before booking. {ex.left_today()} questions left today.")
else:
    st.caption("The suggested questions are answered from the prices saved by the daily run. Check the fare on aegeanair.com before booking.")
