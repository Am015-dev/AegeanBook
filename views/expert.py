"""Expert: today's briefing and a chat about the saved fares, miles and hotel prices (expert_chat)."""
from datetime import date

import streamlit as st

import expert_chat as ex
from db import connect
from overview import collect, facts
from ui import route

db = connect()
today = date.today()
route("Expert", None, "Ask anything about the saved fares, award miles and hotel prices. It remembers the conversation, so you can ask "
      "follow-ups like “and for 4 nights?”.")
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

fares, summary, trips, weekday, hotel_night, awards = collect(db, today)
if not summary:
    st.info("No fares are saved yet, so there is nothing to analyse. The daily run fills them in every morning.")
    st.stop()
data = facts(summary, fares, trips, hotel_night, awards, today)

st.markdown(ex.CARD, unsafe_allow_html=True)
with st.container(border=True, key="brief"):
    st.markdown('<p class="group">Today’s briefing</p>', unsafe_allow_html=True)
    with st.spinner("Reading today’s prices…"):
        take = ex.briefing(data, cfg, today.isoformat())
    if isinstance(take, tuple):
        st.caption(take[1])
    else:
        st.markdown(take)

chat = st.session_state.setdefault("expert-chat", [])


def ask_example():
    st.session_state["expert-pending"] = st.session_state.pop("expert-ex", None)


for m in chat:
    with st.chat_message(m["role"], avatar=":material/person:" if m["role"] == "user" else ":material/auto_awesome:"):
        st.markdown(m["content"])
if not chat:
    st.pills("Try one of these", ex.EXAMPLES, key="expert-ex", on_change=ask_example)

q = st.chat_input("Ask about fares, dates, miles or hotels", max_chars=500) or st.session_state.pop("expert-pending", None)
if q:
    chat.append({"role": "user", "content": q})
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(q)
    with st.chat_message("assistant", avatar=":material/auto_awesome:"):
        try:
            answer = st.write_stream(ex.chat(chat, data, cfg))
            chat.append({"role": "assistant", "content": answer if isinstance(answer, str) else "".join(map(str, answer))})
        except Exception as err:  # never crash the page for an API problem; say what happened
            chat.pop()  # the question was not answered: let it be asked again
            st.error(str(err) if isinstance(err, RuntimeError) else f"The expert is unavailable right now ({type(err).__name__}).")
if chat:
    if st.button("New conversation", icon=":material/refresh:"):
        chat.clear()
        st.rerun()
st.caption(f"Answered by {ex.label(cfg)} from the prices saved by the daily run, and it can be wrong: check the fare on aegeanair.com "
           f"before booking. {ex.left_today()} questions left today.")
