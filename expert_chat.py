"""Virtual expert: an AI model reads the saved fares (overview.facts) and talks about them in plain words: a short daily briefing, and a
chat that remembers the conversation so follow-up questions work.
Two providers, picked by which key is in the Streamlit secrets (or the environment):
- GEMINI_API_KEY: Google Gemini on its free tier (no card; create a key at aistudio.google.com). Used first, because it costs nothing.
- ANTHROPIC_API_KEY: Claude Haiku 5.5, about a tenth of a cent a question.
With neither, the pages hide the expert. The app is public, so a daily cap stops strangers from using up the key.
A new module name on purpose (it replaces expert.py and ai_expert.py): Streamlit Cloud keeps already loaded modules across redeploys."""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import date

import streamlit as st

VERSION = 2  # pages reload this module when Streamlit Cloud still has an older copy loaded (it keeps modules across redeploys)
CLAUDE = "claude-haiku-5-5"
GEMINI = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]  # the second answers when the first is busy or out of free quota
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
MAX_PER_DAY = 60  # ponytail: in-process counter, resets when Cloud restarts the app; the provider's own limits are the hard cap
TURNS = 10  # messages of the conversation sent back each time; older ones are dropped to keep requests small
SYSTEM = """You are a calm, practical travel-price analyst for one traveller who flies AEGEAN between Athens, Frankfurt and Zurich.
The fares, award miles and hotel prices this app has collected are in <data> below. The traveller asks questions; follow-ups refer to
earlier answers in the conversation.

Rules:
- Use only the data given. Quote dates (weekday + day + month) and euro amounts from it. Never invent a price, a route or a date.
- If the data cannot answer something (a route, cabin or date that is not there, or a future price), say so in one sentence.
- Lead with the answer in one or two sentences, then at most five short bullets of evidence. No headings, no tables.
- When miles are involved, compare them with the cash fare of the same day: euros saved per 1,000 miles.
- Prices change daily; recommend checking the price on aegeanair.com before booking."""
BRIEFING = ("Write today's briefing for this traveller: the three most useful things to know or act on now, from the data (a deal worth "
            "booking, a timing pattern, a good use of miles, a cheap whole trip). Exactly three bullets, one line each, each starting with a "
            "short bold label. No introduction and no closing line.")
EXAMPLES = ["Which dates give the cheapest 3-night Frankfurt trip, flights and hotel?",
            "Is business to Zurich worth it this month compared with economy?",
            "Where do my miles save the most cash?",
            "I can only travel on weekends: when should I go to Frankfurt?"]

CARD = ("<style>.st-key-brief{background:#fff;border:0 !important;border-left:4px solid #2563C9 !important;border-radius:14px;"
        "box-shadow:0 1px 2px rgba(0,0,0,.04),0 6px 20px rgba(0,0,0,.05);padding:16px 20px}.st-key-brief ul{margin-bottom:.2rem}</style>")

_used = {}


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


@st.cache_data(ttl=12 * 3600, show_spinner=False, max_entries=4)
def _briefing(facts, cfg, day):
    return "".join(chat([{"role": "user", "content": BRIEFING}], facts, cfg))  # an exception is not cached


_failed = {}


def briefing(facts, cfg, day):
    """Today's three-bullet briefing, or ('error', message). Cached per day and per data version (`facts` changes when the daily run
    brings new prices), so every visitor sees the same one and it costs one request a day. A failure is remembered for 15 minutes, so a
    broken key or a used-up quota does not spend a question (and a long wait) on every page view, but is retried soon after."""
    k = (hash(facts), day)
    if k in _failed and time.time() - _failed[k][0] < 900:
        return "error", _failed[k][1]
    try:
        return _briefing(facts, cfg, day)
    except Exception as err:  # never crash a page for the briefing
        msg = str(err) if isinstance(err, RuntimeError) else f"The briefing is unavailable right now ({type(err).__name__})."
        _failed[k] = (time.time(), msg)
        return "error", msg
