"""Overview page and virtual expert, offline: a seeded database and a fake Claude client."""
import os
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

import db
import expert_chat as ex
import overview

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(autouse=True)
def no_daily_files(monkeypatch):  # the committed data/*.csv must not leak into the test databases
    for name in ("DAILY", "DAILY_HOTEL", "DAILY_AWARD"):
        if hasattr(db, name):
            monkeypatch.setattr(db, name, "/nonexistent.csv")


def seed(path):
    t = db.connect(str(path))
    now, today = datetime.now().isoformat(), date.today()
    for i in range(1, 31):
        d = (today + timedelta(i)).isoformat()
        for o, dest in overview.LEGS:
            eco = 60.0 if (o, dest, i) == ("ATH", "FRA", 10) else 150.0 + i % 7 * 10
            t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", (d, o, dest, "economy", eco, "calendar", now, 1))
            t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", (d, o, dest, "business", eco + 100, "calendar", now, 1))
        t.execute("INSERT INTO hotel VALUES(?,?,?,?,?,?,?,?)", (d, "Steigenberger Frankfurt", "Frankfurter Hof", 1, "Steigenberger", 200.0, now, 2))
    t.commit()
    return t


def test_numbers_trips_briefing_and_facts(tmp_path):
    t = seed(tmp_path / "t.db")
    fares, summary, trips, weekday, hotel_night, awards = overview.collect(t, date.today())
    assert set(summary) == set(overview.LEGS)
    assert summary[("ATH", "FRA")]["economy"]["min"] == 60.0
    assert summary[("ATH", "FRA")]["economy"]["day"] == (date.today() + timedelta(10)).isoformat()
    r = trips[3]  # the €60 outbound wins, and the hotel is three nights at €200
    assert r["depart"] == (date.today() + timedelta(10)).isoformat() and r["hotel"] == 600.0 and r["total"] == r["flights"] + 600.0
    say = overview.briefing(summary, trips, weekday)
    assert len(say) <= 5 and say[0].startswith("Best deal: ATH → FRA") and "€60" in say[0] and any("Best value in Frankfurt" in s for s in say)
    text = overview.facts(summary, fares, trips, hotel_night, awards, date.today())
    assert f"{(date.today() + timedelta(10)).isoformat()},ATH-FRA,60,160" in text and "check_in,hotel_one_night_eur" in text


class FakeStream:
    def __init__(self, parts, stop="end_turn"):
        self.text_stream, self.stop = iter(parts), stop

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return SimpleNamespace(stop_reason=self.stop)


def fake_client(parts, stop="end_turn", seen=None):
    def stream(**kw):
        if seen is not None:
            seen.update(kw)
        return FakeStream(parts, stop)
    return SimpleNamespace(messages=SimpleNamespace(stream=stream))


def user(text):
    return {"role": "user", "content": text}


def test_claude_chat_keeps_the_conversation_and_has_a_daily_cap(monkeypatch):
    monkeypatch.setattr(ex, "_used", {})
    seen = {}
    history = [user("When?"), {"role": "assistant", "content": "Thursday."}, user("And for 4 nights?")]
    assert "".join(ex.chat(history, "day,leg", ("claude", "k"), fake_client(["Fri ", "22 Jan."], seen=seen))) == "Fri 22 Jan."
    assert seen["model"] == "claude-haiku-5-5" and "<data>\nday,leg" in seen["system"]
    assert [m["role"] for m in seen["messages"]] == ["user", "assistant", "user"] and seen["messages"][-1]["content"] == "And for 4 nights?"
    long = [user(str(i)) if i % 2 == 0 else {"role": "assistant", "content": str(i)} for i in range(25)]
    list(ex.chat(long, "", ("claude", "k"), fake_client(["ok"], seen=seen)))
    assert len(seen["messages"]) <= ex.TURNS and seen["messages"][0]["role"] == "user"  # trimmed, still starting with the traveller
    with pytest.raises(RuntimeError, match="declined"):
        list(ex.chat([user("x")], "", ("claude", "k"), fake_client([], stop="refusal")))
    monkeypatch.setattr(ex, "_used", {date.today(): ex.MAX_PER_DAY})
    with pytest.raises(RuntimeError, match="daily limit"):
        list(ex.chat([user("x")], "", ("claude", "k"), fake_client(["never"])))


def test_gemini_is_free_first_falls_back_and_keeps_the_key_out_of_urls(monkeypatch):
    import io
    import json
    import urllib.error
    monkeypatch.setattr(ex, "_used", {})
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert ex.available() == ("gemini", "g")  # the free key wins when both are set
    calls = []

    def urlopen(req, timeout):
        calls.append(req)
        if "3.8-flash" in req.full_url:  # the first model's free quota is used up
            raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, None)
        parts = [{"text": "thinking...", "thought": True}, {"text": "Fly Thursday."}]
        return io.BytesIO(json.dumps({"candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}]}).encode())
    history = [user("When?"), {"role": "assistant", "content": "Thursday."}, user("Why?")]
    assert "".join(ex.chat(history, "data", ("gemini", "g"), urlopen=urlopen)) == "Fly Thursday."  # thoughts are not shown
    assert ["gemini-3.8-flash" in calls[0].full_url, "gemini-3.5-flash-lite" in calls[1].full_url] == [True, True]
    assert calls[1].get_header("X-goog-api-key") == "g" and "key=" not in calls[1].full_url
    body = json.loads(calls[1].data)
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"] and "<data>" in body["systemInstruction"]["parts"][0]["text"]
    empty = lambda req, timeout: io.BytesIO(b'{"candidates": [{"finishReason": "SAFETY"}]}')
    with pytest.raises(RuntimeError, match="declined"):
        list(ex.chat([user("x")], "", ("gemini", "g"), urlopen=empty))

    def busy(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, None)
    with pytest.raises(RuntimeError, match="free quota"):
        list(ex.chat([user("x")], "", ("gemini", "g"), urlopen=busy))


def test_briefing_is_asked_once_per_day_and_data_and_a_failure_is_retried_later(monkeypatch):
    asked = []
    monkeypatch.setattr(ex, "chat", lambda history, facts, cfg: (asked.append(history), iter(["- **Book** now"]))[1])
    monkeypatch.setattr(ex, "_failed", {})
    ex._briefing.clear()
    assert ex.briefing("data v1", ("gemini", "g"), "2026-10-30") == "- **Book** now"
    ex.briefing("data v1", ("gemini", "g"), "2026-10-30")
    assert len(asked) == 1 and asked[0][0]["content"] == ex.BRIEFING  # the second visitor gets the cached briefing
    ex.briefing("data v2", ("gemini", "g"), "2026-10-30")
    assert len(asked) == 2  # new prices, new briefing

    def broken(history, facts, cfg):
        asked.append(history)
        raise TimeoutError("timed out")
    monkeypatch.setattr(ex, "chat", broken)
    assert ex.briefing("data v3", ("gemini", "g"), "2026-10-30") == ("error", "The briefing is unavailable right now (TimeoutError).")
    ex.briefing("data v3", ("gemini", "g"), "2026-10-30")
    assert len(asked) == 3  # remembered: the next visitor neither waits nor spends a question
    now = ex.time.time()
    monkeypatch.setattr(ex.time, "time", lambda: now + 901)
    ex.briefing("data v3", ("gemini", "g"), "2026-10-30")
    assert len(asked) == 4  # but tried again after 15 minutes, and a success is not blocked by the old failure


def test_gemini_reasons_briefly_and_copes_with_a_slow_or_picky_model(monkeypatch):
    import io
    import json
    import urllib.error
    monkeypatch.setattr(ex, "_used", {})
    seen = []
    ok = lambda: io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": "Fine."}]}}]}).encode())

    def picky(req, timeout):  # rejects thinkingConfig, then answers without it
        body = json.loads(req.data)
        seen.append(body["generationConfig"])
        if "thinkingConfig" in body["generationConfig"]:
            raise urllib.error.HTTPError(req.full_url, 400, "bad field", {}, None)
        return ok()
    assert "".join(ex.chat([user("x")], "", ("gemini", "g"), urlopen=picky)) == "Fine."
    assert seen[0]["thinkingConfig"] == {"thinkingLevel": "low"} and "thinkingConfig" not in seen[1]

    def slow(req, timeout):
        if "3.8-flash" in req.full_url:
            raise TimeoutError("timed out")
        return ok()
    assert "".join(ex.chat([user("x")], "", ("gemini", "g"), urlopen=slow)) == "Fine."  # the lighter model answers instead

    def dead(req, timeout):
        raise TimeoutError("timed out")
    with pytest.raises(RuntimeError, match="did not answer in time"):
        list(ex.chat([user("x")], "", ("gemini", "g"), urlopen=dead))


def pages(tmp_path, monkeypatch, key):
    from streamlit.testing.v1 import AppTest
    monkeypatch.chdir(tmp_path)
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    if key:
        monkeypatch.setenv("GEMINI_API_KEY", key)
    return lambda name: AppTest.from_file(os.path.join(HERE, "views", name), default_timeout=60)


def test_overview_page_empty_filled_and_with_the_experts_take(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch, None)
    at = open_page("overview.py").run()
    assert not at.exception and "No fares are saved yet" in at.info[0].value
    seed(tmp_path / "tracker.db")
    at = open_page("overview.py").run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert "€60" in text and "Cheapest whole trip to Frankfurt" in text and "needs a free key" in text
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(ex, "briefing", lambda facts, cfg, day: "- **Book now:** ATH → FRA at €60")
    at = open_page("overview.py").run()
    assert not at.exception and "ATH → FRA at €60" in " ".join(m.value for m in at.markdown)


def test_expert_page_setup_briefing_and_a_follow_up_conversation(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch, None)
    seed(tmp_path / "tracker.db")
    at = open_page("expert.py").run()
    assert not at.exception and "needs an AI key" in at.info[0].value and not at.chat_input
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(ex, "briefing", lambda facts, cfg, day: "- **Book now:** €60")
    seen = []
    monkeypatch.setattr(ex, "chat", lambda history, facts, cfg: (seen.append([m["content"] for m in history]), iter([f"Answer {len(seen)}"]))[1])
    at = open_page("expert.py").run()
    assert "€60" in " ".join(m.value for m in at.markdown)
    at.chat_input[0].set_value("When is it cheapest?").run()
    at.chat_input[0].set_value("And for 4 nights?").run()
    assert not at.exception and seen[-1] == ["When is it cheapest?", "Answer 1", "And for 4 nights?"]  # the follow-up carries the conversation
    assert [m.markdown[0].value for m in at.chat_message] == ["When is it cheapest?", "Answer 1", "And for 4 nights?", "Answer 2"]
    assert "Google Gemini (free tier)" in at.caption[-1].value

    def busy(history, facts, cfg):
        raise RuntimeError("Gemini did not answer in time. Try again in a minute.")
    monkeypatch.setattr(ex, "chat", busy)
    at.chat_input[0].set_value("And for 7 nights?").run()
    assert "did not answer in time" in at.error[0].value
    assert len(at.session_state["expert-chat"]) == 4  # the unanswered question is not kept, so it does not confuse the next answer


def test_trip_planner_months_hotels_candidates_and_question(tmp_path):
    import trip_plan as tp
    t = seed(tmp_path / "t.db")
    today = date.today()
    d = lambda i: (today + timedelta(i)).isoformat()
    for i in (5, 6):  # business award seats on two days each way
        for o, dest in (("ATH", "ZRH"), ("ZRH", "ATH")):
            t.execute("INSERT INTO award_miles(day, orig, dest, cabin, miles, nonstop, fetched, taxes) VALUES(?,?,?,?,?,?,?,?)",
                      (d(i) if o == "ATH" else d(i + 3), o, dest, "business", 15000, 1, datetime.now().isoformat(), None))
    t.commit()
    assert tp.city("ZRH") == "Zurich" and set(tp.destinations(t)) == {"FRA", "ZRH"}
    m = tp.months(today, today + timedelta(60))
    assert m[0][0] == "Next 30 days" and m[0][1] == today + timedelta(1) and all(a <= b for _, a, b in m)
    assert [h["name"] for h in tp.hotels(t, "FRA")] == ["Steigenberger"] and tp.hotels(t, "ZRH") == []  # matched by the city's name
    start, end = today + timedelta(1), today + timedelta(20)
    biz = tp.candidates(t, "ZRH", start, end, "business", 3, [], None, "miles")
    assert biz.miles.iloc[0] == 30000 and biz.depart.iloc[0] in (d(5), d(6))  # miles first when paying with miles
    assert biz.value_per_1000.notna().any()
    hotel = tp.hotels(t, "FRA")[0]
    eco = tp.candidates(t, "FRA", start, end, "economy", 3, [], hotel)
    first = eco.iloc[0]
    assert first.depart == d(10) and first.hotel == 600.0 and first.total_cash == first.cash + 600.0  # the €60 day wins, plus 3 nights
    friday = tp.candidates(t, "FRA", start, end, "economy", None, ["Fri"], None)
    assert len(friday) and all(pd.Timestamp(x).weekday() == 4 for x in friday.depart)  # only the weekdays asked for
    q = tp.question("ZRH", "December 2026", "business", "both", 4, ["Fri", "Sat"], None)
    assert "Zurich (ZRH)" in q and "business" in q and "Fri/Sat" in q and "why" in q
    text = tp.facts(biz, "ZRH", "Next 30 days", start, end, "business", "miles", 3, None, today)
    assert "fewest miles first" in text and "award taxes are not collected yet" in text and "depart,back,cash,miles" in text


def test_expert_page_plans_a_trip_from_the_choices(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch, "test")
    seed(tmp_path / "tracker.db")
    monkeypatch.setattr(ex, "briefing", lambda facts, cfg, day: "- **Book now:** €60")
    seen = []
    monkeypatch.setattr(ex, "chat", lambda history, facts, cfg: (seen.append((history, facts)), iter([f"Answer {len(seen)}"]))[1])
    at = open_page("expert.py").run()
    at.selectbox(key="plan-dest").set_value("FRA")
    at.button[0].click().run()  # Ask the expert, with the defaults: economy, return 3 nights, cash or miles, the searched hotel
    assert not at.exception
    history, facts = seen[-1]
    assert "Frankfurt am Main (FRA)" in history[-1]["content"] and "3-night return" in history[-1]["content"]
    assert "Steigenberger" in history[-1]["content"] and "candidate trips" in facts and "All tracked routes" in facts
    at.chat_input[0].set_value("And with miles?").run()
    history, facts = seen[-1]
    assert [m["content"] for m in history][-1] == "And with miles?" and len(history) == 3 and "candidate trips" in facts  # follow-up keeps the plan
