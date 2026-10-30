"""Virtual expert (named ai_expert, not expert: Streamlit Cloud keeps an already loaded module across redeploys, so a renamed module is
how a page gets the new code): an AI model reads the saved fares (overview.facts) and answers a question about them in plain words.
Two providers, picked by which key is in the Streamlit secrets (or the environment):
- GEMINI_API_KEY: Google Gemini on its free tier (no card; create a key at aistudio.google.com). Used first, because it costs nothing.
- ANTHROPIC_API_KEY: Claude Haiku 5.5, about a tenth of a cent a question.
With neither the Overview hides the expert. The app is public, so a daily cap stops strangers from using up the key."""
import json
import os
import urllib.error
import urllib.request
from datetime import date

MODEL = "claude-haiku-5-5"
GEMINI = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]  # the second answers when the first is busy or out of free quota
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
MAX_PER_DAY = 40  # ponytail: in-process counter, resets when Cloud restarts the app; the provider's own limits are the hard cap
SYSTEM = """You are a calm, practical travel-price analyst for one traveller who flies AEGEAN between Athens, Frankfurt and Zurich.
You get the fares, award miles and hotel prices this app has collected, then a question.

Rules:
- Use only the data given. Quote dates (weekday + day + month) and euro amounts from it. Never invent a price, a route or a date.
- If the data cannot answer something (a route, cabin or date that is not there, or a future price), say so in one sentence.
- Lead with the answer in one or two sentences, then at most five short bullets of evidence. No headings, no tables.
- When miles are involved, compare them with the cash fare of the same day: euros saved per 1,000 miles.
- Prices change daily; recommend checking the price on aegeanair.com before booking."""

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


def _prompt(question, facts):
    return f"<data>\n{facts}\n</data>\n\nQuestion: {question}"


def _gemini(question, facts, key, urlopen=urllib.request.urlopen):
    """The whole answer from the first Gemini model that answers. Raises RuntimeError with a plain message otherwise."""
    body = json.dumps({"systemInstruction": {"parts": [{"text": SYSTEM}]},
                       "contents": [{"role": "user", "parts": [{"text": _prompt(question, facts)}]}],
                       "generationConfig": {"maxOutputTokens": 16000}}).encode()
    last = None
    for model in GEMINI:
        req = urllib.request.Request(GEMINI_URL.format(model), data=body, method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})  # header, so the key is never in a URL or log
        try:
            with urlopen(req, timeout=90) as r:
                res = json.load(r)
        except urllib.error.HTTPError as err:
            last = err.code
            if err.code in (404, 429, 500, 503):  # model gone, free quota used up, or busy: try the next one
                continue
            raise RuntimeError(f"Gemini refused the request (HTTP {err.code}). Check GEMINI_API_KEY in the app's secrets.") from None
        cand = (res.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        if not text:
            raise RuntimeError("The expert declined to answer that. Try asking about prices, dates or miles.")
        return text + ("\n\n(The answer was cut short.)" if cand.get("finishReason") == "MAX_TOKENS" else "")
    raise RuntimeError("Gemini's free quota is used up for now." if last == 429 else f"Gemini is unavailable right now (HTTP {last}).")


def _claude(question, facts, key, client=None):
    if client is None:
        import anthropic  # only needed when Claude is the provider
        client = anthropic.Anthropic(api_key=key)
    with client.messages.stream(model=MODEL, max_tokens=16000, system=SYSTEM, output_config={"effort": "low"},
                                messages=[{"role": "user", "content": _prompt(question, facts)}]) as stream:
        yield from stream.text_stream
        final = stream.get_final_message()
    if final.stop_reason == "refusal":
        raise RuntimeError("The expert declined to answer that. Try asking about prices, dates or miles.")
    if final.stop_reason == "max_tokens":
        yield "\n\n(The answer was cut short.)"


def ask(question, facts, cfg, client=None, urlopen=urllib.request.urlopen):
    """Yield the answer (Claude streams it; Gemini arrives in one piece). Raises RuntimeError with a plain message when the daily cap is
    reached or the model declines. cfg is what available() returned."""
    if left_today() <= 0:
        raise RuntimeError(f"The expert has answered {MAX_PER_DAY} questions today, its daily limit. Try again tomorrow.")
    _used[date.today()] = _used.get(date.today(), 0) + 1
    provider, key = cfg
    if provider == "gemini":
        yield _gemini(question, facts, key, urlopen)
    else:
        yield from _claude(question, facts, key, client)
