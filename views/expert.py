"""Expert: plan a trip from your own choices (destination, month, cabin, cash or miles, hotel) and get the best time to fly and why, then
ask follow-ups; plus today's briefing. The AI is in expert_chat, the trip maths in trip_plan."""
import importlib
from datetime import date

import streamlit as st

import expert_chat as ex
import overview as ov
import trip_plan as tp
from db import connect
from insights import DAYS
from ui import route

if getattr(ex, "VERSION", 0) < 2:  # Streamlit Cloud keeps an older copy of a module across redeploys
    ex = importlib.reload(ex)
if getattr(ov, "VERSION", 0) < 2:
    ov = importlib.reload(ov)
if getattr(tp, "VERSION", 0) < 2:
    tp = importlib.reload(tp)
db = connect()
today = date.today()
route("Expert", None, "Tell it your trip and it finds the best time to fly, in cash or miles, and explains why. Then ask follow-ups.")
try:
    cfg = ex.available(st.secrets)
except Exception:
    cfg = ex.available()
if not cfg:
    st.info("The expert needs an AI key, set once by the app's owner.")
    st.markdown("**Free:** create a Google Gemini key at aistudio.google.com (no card needed) and add `GEMINI_API_KEY = \"...\"` under "
                "**Settings → Secrets** of this app on Streamlit Cloud.\n\n"
                "**Paid alternative:** Claude Haiku, about €0.001 a question: add `ANTHROPIC_API_KEY = \"...\"` instead. When both are set, "
                f"the free Gemini key is used. Either way the app stops after {ex.MAX_PER_DAY} questions a day.")
    st.stop()

fares, summary, trips, weekday, hotel_night, awards = ov.collect(db, today)
dests = tp.destinations(db)
if not summary or not dests:
    st.info("No fares are saved yet, so there is nothing to analyse. The daily run fills them in every morning.")
    st.stop()
general = ov.facts(summary, fares, trips, hotel_night, awards, today)
chat = st.session_state.setdefault("expert-chat", [])
data = st.session_state.get("expert-data") or general  # a planned trip's data while its conversation lasts


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


with st.container(border=True, key="search"):
    st.markdown('<p class="group">Plan a trip</p>', unsafe_allow_html=True)
    c = st.columns([1, 1])
    dest = c[0].selectbox("Fly from Athens to", dests, format_func=lambda d: f"{tp.city(d)} ({d})", key="plan-dest")
    last = date.fromisoformat(max(s.index.max() for leg in fares.values() for s in leg.values() if len(s)))  # the last day with fares
    periods = tp.months(today, last)
    when = c[1].selectbox("When", range(len(periods)), format_func=lambda i: periods[i][0], key="plan-when")
    c = st.columns([1, 1, 2])
    cabin = c[0].pills("Cabin", ["economy", "business"], default="economy", format_func=str.capitalize, required=True, key="plan-cabin")
    trip = c[1].pills("Trip", ["One way", "Return"], default="Return", required=True, key="plan-trip")
    pay = c[2].pills("Pay with", list(tp.PAY), default="both", format_func=tp.PAY.get, required=True, key="plan-pay")
    nights, hotel = None, None
    if trip == "Return":
        c = st.columns([1, 3])
        nights = c[0].number_input("Nights", 1, 21, 3, key="plan-nights")
        saved = tp.hotels(db, dest)
        hotel = c[1].selectbox("Hotel", [None] + saved, key=f"plan-hotel-{dest}",
                               format_func=lambda h: "No hotel, flights only" if h is None else f"{h['name']} · {h['adults']} adults",
                               index=1 if saved else 0,
                               help="Hotels you searched on the Hotels page (one night) in this city, the most recent first.")
        if not saved:
            c[1].caption(f"No hotel in {tp.city(dest)} searched yet: search one on the Hotels page with 1 night, and it appears here.")
    weekdays = st.pills("Days I can leave", DAYS, selection_mode="multi", default=DAYS, key="plan-days")
    go = st.button("Ask the expert", type="primary", icon=":material/auto_awesome:")

if go:
    label, start, end = periods[when]
    t = tp.candidates(db, dest, start, end, cabin, nights, weekdays, hotel, pay)
    if t.empty:
        st.warning("No fares are saved for this choice: try another month, more weekdays or fewer nights.")
    else:
        chat.clear()
        data = tp.facts(t, dest, label, start, end, cabin, pay, nights, hotel, today) + "\n\nAll tracked routes, for follow-ups:\n" + general
        st.session_state["expert-data"] = data
        answer(tp.question(dest, label, cabin, pay, nights, weekdays, hotel), data)

for m in chat[:-2] if go else chat:  # this run's answer is already on the page
    with st.chat_message(m["role"], avatar=":material/person:" if m["role"] == "user" else ":material/auto_awesome:"):
        st.markdown(m["content"])

q = st.chat_input("Ask a follow-up, or anything about fares, dates, miles or hotels", max_chars=500)
if q:
    answer(q, data)
if chat and st.button("New conversation", icon=":material/refresh:"):
    chat.clear()
    st.session_state.pop("expert-data", None)
    st.rerun()

st.markdown(ex.CARD, unsafe_allow_html=True)
with st.container(border=True, key="brief"):
    st.markdown('<p class="group">Today’s briefing</p>', unsafe_allow_html=True)
    with st.spinner("Reading today’s prices…"):
        take = ex.briefing(general, cfg, today.isoformat())
    if isinstance(take, tuple):
        st.caption(take[1])
    else:
        st.markdown(take)
st.caption(f"Answered by {ex.label(cfg)} from the prices saved by the daily run, and it can be wrong: check the fare on aegeanair.com "
           f"before booking. {ex.left_today()} questions left today.")
