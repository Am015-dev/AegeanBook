"""Expert: the Overview's answer for your route (cheapest trip, dates, nights, price) explained in three plain sentences, then follow-up
questions. The numbers come from the same functions as the Overview's boarding pass, with the same filters (expert_chat.facts), so the two
pages never disagree; the words come from an AI model, or from plain Python when the model is unavailable."""
import importlib
from datetime import date, timedelta

import streamlit as st

import expert_chat as ex
import fetch_ahead as fa
import overview as ov
import trip_plan as tp
from db import connect
from insights import DAYS
from ui import route

if getattr(ex, "VERSION", 0) < 4:  # Streamlit Cloud keeps an older copy of a module across redeploys
    ex = importlib.reload(ex)
if getattr(ov, "VERSION", 0) < 4:
    ov = importlib.reload(ov)
if getattr(tp, "VERSION", 0) < 5:
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
    stays = [int(n) for v in q.get_all("nights") for n in v.split(",") if n.strip().isdigit()]  # ?nights=2,3 or ?nights=2&nights=3
    if stays and all(n in ex.NIGHTS for n in stays):
        st.session_state["plan-nights"] = sorted(set(stays))
asked_when = q.get("when")  # the period is matched once the list exists, below
if q.get("from") in froms:
    q.clear()
for key, value in (("plan-cabin", "economy"), ("plan-pay", "cash"), ("plan-nights", ex.NIGHTS), ("plan-days", DAYS)):
    st.session_state.setdefault(key, value)  # the Overview's defaults; set here, not as widget defaults, so a link can preset them

with st.container(border=True, key="search"):
    st.markdown('<p class="group">Your trip</p>', unsafe_allow_html=True)
    c = st.columns([1.2, 1.2, 1])
    place = lambda a: f"{tp.city(a)} ({a})"
    orig = c[0].selectbox("From", froms, format_func=place, key="plan-orig",
                          help="Every airport with fares saved: the daily routes, plus any route you searched on the Flights page.")
    dest = c[1].selectbox("To", [d for o, d in fares if o == orig], format_func=place, key=f"plan-dest-{orig}")
    last = date.fromisoformat(db.execute("SELECT MAX(day) FROM prices WHERE price IS NOT NULL").fetchone()[0])  # the last day with fares, any route
    periods = tp.months(today, last)  # the Overview's own list, so both pages open on the same answer: the next 30 days, then every month
    if asked_when in [x.name for x in periods]:  # ?when=January 2027 from the Overview's link
        st.session_state["plan-when"] = [x.name for x in periods].index(asked_when)
    when = c[2].selectbox("When", range(len(periods)), format_func=lambda i: periods[i].title, key="plan-when")
    c = st.columns([1, 1, 2])
    cabin = c[0].pills("Cabin", ["economy", "business"], format_func=str.capitalize, required=True, key="plan-cabin")
    pay = c[1].pills("Pay with", list(ex.PAY), format_func=ex.PAY.get, required=True, key="plan-pay")
    nights = c[2].pills("Nights", ex.NIGHTS, selection_mode="multi", key="plan-nights")
    wdays = st.pills("Days I can leave", DAYS, selection_mode="multi", key="plan-days")
    saved = tp.hotels(db, dest)
    hotel = st.selectbox("Hotel", [None] + saved, key=f"plan-hotel-{dest}", index=1 if saved else 0,
                         format_func=lambda h: "No hotel, flights only" if h is None else f"{h['name']} · {h['adults']} adults",
                         help="Hotels you searched on the Hotels page (one night) in this city, the most recent first; added to a cash return.")
    if not saved:
        st.caption(f"No hotel in {tp.city(dest)} searched yet: search one on the Hotels page with 1 night, and it appears here.")
    p = periods[when]
    stays = sorted(nights) or ex.NIGHTS
    # no fares saved for this route in the period (either way): one button fetches them on GitHub, the way back `stays` later
    todo, blank = fa.missing(db, [(orig, dest, p.start, p.end), (dest, orig, p.start + timedelta(min(stays)), p.end + timedelta(max(stays)))], today)
    if blank and pay == "cash":  # award miles cannot be fetched from here: they are collected on the owner's computer, 90 days ahead
        fa.button(f"ahead|{orig}|{dest}|{p.start}", f"Fetch {p.label} fares" if when else f"Fetch the {p.label.lower()}", todo,
                  f"{p.name.capitalize()} from {tp.city(orig)} to {tp.city(dest)}")

f = ex.facts(db, orig, dest, cabin, pay, nights, wdays, (p.start, p.end), today, hotel)
# the chat reads the same facts, then every tracked route for follow-ups (without the Overview's Frankfurt trip totals, which include a hotel)
data = ex.text(f) + "\n\nAll tracked routes, for follow-ups (one-way fares):\n" + ov.facts(summary, fares, {}, hotel_night, awards, today)
chat = st.session_state.setdefault("expert-chat", [])
search = (orig, dest, cabin, pay, tuple(stays), tuple(wdays or DAYS), when, hotel and hotel["name"])
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

if not cfg:
    st.info("The expert needs an AI key, set once by the app's owner, to explain in its own words and answer questions.")
    st.markdown("**Free:** create a Google Gemini key at aistudio.google.com (no card needed) and add `GEMINI_API_KEY = \"...\"` under "
                "**Settings → Secrets** of this app on Streamlit Cloud.\n\n"
                "**Paid alternative:** Claude Haiku, about €0.001 a question: add `ANTHROPIC_API_KEY = \"...\"` instead. When both are set, "
                f"the free Gemini key is used. Either way the app stops after {ex.MAX_PER_DAY} questions a day.")
    st.stop()


def answer(q, data):
    """Show the question and the expert's answer; keep both only once answered."""
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(q)
    with st.chat_message("assistant", avatar=":material/auto_awesome:"):
        try:
            with st.spinner("Thinking…"):
                a = st.write_stream(ex.chat(chat + [{"role": "user", "content": q}], data, cfg))
            # saved only once answered, so a question cut off by a new one (or an error) does not linger without its answer
            chat.extend([{"role": "user", "content": q}, {"role": "assistant", "content": a if isinstance(a, str) else "".join(map(str, a))}])
        except Exception as err:  # never crash the page for an API problem; say what happened
            st.error(str(err) if isinstance(err, RuntimeError) else f"The expert is unavailable right now ({type(err).__name__}).")


for m in chat:
    with st.chat_message(m["role"], avatar=":material/person:" if m["role"] == "user" else ":material/auto_awesome:"):
        st.markdown(m["content"])

q = st.chat_input("Ask about these dates, or anything about fares, miles or hotels", max_chars=500)
if q:
    answer(q, data)
if chat and st.button("New conversation", icon=":material/refresh:"):
    chat.clear()
    st.rerun()
st.caption(f"Answered by {ex.label(cfg)} from the prices saved by the daily run, and it can be wrong: check the fare on aegeanair.com "
           f"before booking. {ex.left_today()} questions left today.")
