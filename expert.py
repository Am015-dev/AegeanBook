"""Virtual expert: Claude Haiku 5.5 reads the saved fares (overview.facts) and answers a question about them in plain words.
Optional and the only paid part of the app: it needs an Anthropic API key in the Streamlit secrets (or the environment) as
ANTHROPIC_API_KEY. Without one the Overview simply hides it. A question costs about a tenth of a cent; the app is public, so a daily
cap stops strangers from spending the key."""
import os
from datetime import date

MODEL = "claude-haiku-5-5"
MAX_PER_DAY = 40  # ponytail: in-process counter, resets when Cloud restarts the app; a spend limit in the Anthropic Console is the hard cap
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
    """The API key if one is configured, else None."""
    try:
        key = (secrets or {}).get("ANTHROPIC_API_KEY")
    except Exception:  # st.secrets raises when no secrets file exists at all
        key = None
    return key or os.environ.get("ANTHROPIC_API_KEY")


def left_today():
    return MAX_PER_DAY - _used.get(date.today(), 0)


def ask(question, facts, key, client=None):
    """Yield the answer as it streams. Raises RuntimeError with a plain message when the cap is reached or Claude declines."""
    if left_today() <= 0:
        raise RuntimeError(f"The expert has answered {MAX_PER_DAY} questions today, its daily limit. Try again tomorrow.")
    _used[date.today()] = _used.get(date.today(), 0) + 1
    if client is None:
        import anthropic  # only needed when the expert is used
        client = anthropic.Anthropic(api_key=key)
    with client.messages.stream(
        model=MODEL, max_tokens=4000, system=SYSTEM, output_config={"effort": "low"},
        messages=[{"role": "user", "content": f"<data>\n{facts}\n</data>\n\nQuestion: {question}"}],
    ) as stream:
        yield from stream.text_stream
        final = stream.get_final_message()
    if final.stop_reason == "refusal":
        raise RuntimeError("The expert declined to answer that. Try asking about prices, dates or miles.")
    if final.stop_reason == "max_tokens":
        yield "\n\n(The answer was cut short.)"
