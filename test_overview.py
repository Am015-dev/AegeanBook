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


def test_briefing_is_asked_once_per_day_and_a_failure_is_retried_later(monkeypatch):
    asked = []
    monkeypatch.setattr(ex, "chat", lambda history, facts, cfg: (asked.append(history), iter(["- **Book** now"]))[1])
    monkeypatch.setattr(ex, "_failed", {})
    ex._briefing.clear()
    assert ex.briefing("data v1", ("gemini", "g"), "2026-10-30") == "- **Book** now"
    ex.briefing("data v1", ("gemini", "g"), "2026-10-30")
    assert len(asked) == 1 and asked[0][0]["content"] == ex.BRIEFING  # the second visitor gets the cached briefing
    ex.briefing("data v2", ("gemini", "g"), "2026-10-30")
    assert len(asked) == 1  # a visitor's search changes the data, not today's briefing
    ex.briefing("data v2", ("gemini", "g"), "2026-10-31")
    assert len(asked) == 2  # a new day, a new briefing

    def broken(history, facts, cfg):
        asked.append(history)
        raise TimeoutError("timed out")
    monkeypatch.setattr(ex, "chat", broken)
    assert ex.briefing("data v3", ("gemini", "g"), "2026-11-01") == ("error", "The briefing is unavailable right now (TimeoutError).")
    ex.briefing("data v3", ("gemini", "g"), "2026-11-01")
    assert len(asked) == 3  # remembered: the next visitor neither waits nor spends a question
    now = ex.time.time()
    monkeypatch.setattr(ex.time, "time", lambda: now + 901)
    ex.briefing("data v3", ("gemini", "g"), "2026-11-01")
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


def test_overview_page_empty_and_filled_answers_from_the_data_only(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch, None)
    at = open_page("overview.py").run()
    assert not at.exception and "No fares are saved yet" in at.info[0].value
    seed(tmp_path / "tracker.db")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(ex, "briefing", lambda *a: pytest.fail("the Overview must not call the AI (it made the page change all the time)"))
    at = open_page("overview.py").run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    # the boarding pass: ATH -> FRA economy, €60 out on day 10, back on the cheapest FRA -> ATH day 2 to 7 nights later (€150, day 14)
    assert "Cheapest return" in text and "€210" in text and "4 nights" in text
    assert "Steigenberger" in text  # the hotel searched in Frankfurt, for the pass's nights
    grid, routes = at.dataframe[0].value, at.dataframe[1].value
    assert "4 nights" in grid.columns and list(routes.Route) == ["ATH → FRA", "FRA → ATH", "ATH → ZRH", "ZRH → ATH"]
    assert routes.Economy[0].startswith("€60 · ")


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
    assert m[0][0] == "Next 30 days" and m[0][1] == today + timedelta(1) and all(p.start <= p.end for p in m)
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


def test_months_reach_the_horizon_and_say_which_are_fetched():
    import trip_plan as tp
    today, last = date(2026, 10, 31), date(2027, 1, 29)  # the daily run's 90 days
    m = tp.months(today, last)
    assert [p.label for p in m[:4]] == ["Next 30 days", "November 2026", "December 2026", "January 2027"]
    assert m[-1].label == "September 2027" and m[-1].end == today + timedelta(330) and len(m) == 12  # up to the horizon, cut there
    assert m[0] == ("Next 30 days", date(2026, 11, 1), date(2026, 11, 30), True) and m[1].start == date(2026, 11, 1)
    assert [p.fetched for p in m] == [True] * 4 + [False] * 8  # fares saved to 29 Jan: February on is not fetched yet
    assert m[3].title == "January 2027" and m[7].title == "May 2027 · not fetched yet" and m[7].name == "May 2027"
    assert m[0].name == "the next 30 days" and (m[7].start, m[7].end) == (date(2027, 5, 1), date(2027, 5, 31))
    assert tp.months(today, last, horizon=60)[-1].label == "December 2026"  # a shorter horizon
    assert not any(p.fetched for p in tp.months(today, today))  # nothing saved past today: no period is fetched


def test_expert_page_plans_a_trip_from_the_choices(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch, "test")
    seed(tmp_path / "tracker.db")
    monkeypatch.setattr(ex, "briefing", lambda facts, cfg, day: "- **Book now:** €60")
    seen = []
    monkeypatch.setattr(ex, "chat", lambda history, facts, cfg: (seen.append((history, facts)), iter([f"Answer {len(seen)}"]))[1])
    at = open_page("expert.py").run()
    at.selectbox(key="plan-orig").set_value("ATH").run()
    at.selectbox(key="plan-dest-ATH").set_value("FRA")
    at.button[0].click().run()  # Ask the expert, with the defaults: economy, return 3 nights, cash or miles, the searched hotel
    assert not at.exception
    history, facts = seen[-1]
    assert "Frankfurt am Main (FRA)" in history[-1]["content"] and "3-night return" in history[-1]["content"]
    assert "Steigenberger" in history[-1]["content"] and "candidate trips" in facts and "All tracked routes" in facts
    at.chat_input[0].set_value("And with miles?").run()
    history, facts = seen[-1]
    assert [m["content"] for m in history][-1] == "And with miles?" and len(history) == 3 and "candidate trips" in facts  # follow-up keeps the plan


def test_cheapest_month_table_and_colours():
    import deals
    days = pd.date_range("2026-10-31", "2026-12-10").strftime("%Y-%m-%d")
    fares = pd.Series([300.0] + [100.0 + i for i in range(len(days) - 1)], index=days)
    m = deals.by_month(fares)
    assert list(m.index) == ["Nov 2026", "Dec 2026"] and m["Nov 2026"] == 100.0  # one day of October is not October's cheapest fare
    t = deals.month_table({("ATH", "FRA"): {"economy": fares}}, "economy")
    assert list(t.index) == ["ATH → FRA"] and list(t.columns) == ["Nov 2026", "Dec 2026"]
    two = pd.DataFrame({"Nov 2026": [100.0, 80.0], "Dec 2026": [120.0, 85.0]}, index=["ATH → FRA", "ATH → ZRH"])
    css = deals.month_colours(two)
    assert "0E7A53" in css.iloc[0, 0] and "C2410C" in css.iloc[0, 1]  # cheapest month green, 20% dearer orange
    assert "0E7A53" in css.iloc[1, 0] and css.iloc[1, 1] == ""  # 6% dearer: plain


def test_mixed_trips_use_miles_one_way(tmp_path):
    import trip_plan as tp
    t = seed(tmp_path / "t.db")
    today = date.today()
    out_day = (today + timedelta(5)).isoformat()  # award seats only on the way out
    t.execute("INSERT INTO award_miles(day, orig, dest, cabin, miles, nonstop, fetched, taxes) VALUES(?,?,?,?,?,?,?,?)",
              (out_day, "ATH", "ZRH", "business", 15000, 1, datetime.now().isoformat(), None))
    t.commit()
    start, end = today + timedelta(1), today + timedelta(20)
    c = tp.candidates(t, "ZRH", start, end, "business", 3, [], None)
    assert c.miles.isna().all()  # no trip has seats both ways
    text = tp.facts(c, "ZRH", "Next 30 days", start, end, "business", "both", 3, None, today)
    assert f"Miles out, cash back: depart {out_day}" in text and "15,000 miles replace" in text
    assert "Cash out, miles back: no trip" in text and "out_miles" in text
    assert "Miles out" not in tp.facts(c, "ZRH", "Next 30 days", start, end, "business", "cash", 3, None, today)  # not asked for miles


def test_trip_grid_adds_both_ways_and_marks_the_cheapest():
    import deals
    out = pd.Series({"2026-11-02": 100.0, "2026-11-03": 80.0})
    back = pd.Series({"2026-11-05": 50.0, "2026-11-06": 60.0})
    t = deals.trip_grid(out, back, date(2026, 11, 1), date(2026, 11, 4), nights=range(2, 5))
    assert list(t.index) == ["Mon 02 Nov", "Tue 03 Nov"]  # days with no trip at all are left out
    assert t.loc["Tue 03 Nov", "2 nights"] == 130 and t.loc["Mon 02 Nov", "3 nights"] == 150 and pd.isna(t.loc["Mon 02 Nov", "2 nights"])
    css = deals.grid_colours(t)
    assert "#0E7A53;color:#fff" in css.loc["Tue 03 Nov", "2 nights"] and "#E3F4EA" in css.loc["Tue 03 Nov", "3 nights"]  # 140 <= 143
    assert css.loc["Mon 02 Nov", "4 nights"] == ""  # 160 is more than 10% dearer
    flat = deals.trip_grid(pd.Series({"2026-11-02": 9000, "2026-11-03": None}, dtype=object), pd.Series({"2026-11-05": 9000.0}),
                           date(2026, 11, 1), date(2026, 11, 3), nights=range(2, 4))  # award miles: the same price, None on days without seats
    assert flat.loc["Mon 02 Nov", "3 nights"] == 18000 and pd.isna(flat.loc["Mon 02 Nov", "2 nights"])
    assert deals.grid_colours(flat).loc["Mon 02 Nov", "3 nights"] == "background-color:#E3F4EA;color:#0E7A53"  # possible, not "cheapest"


def test_trip_context_and_filters_on_best_trip_and_grid():
    import deals
    out = pd.Series({"2026-11-02": 100.0, "2026-11-03": 80.0, "2026-11-04": 90.0})  # Mon, Tue, Wed
    back = pd.Series({"2026-11-05": 50.0, "2026-11-06": 60.0, "2026-11-07": 70.0})
    ctx = deals.trip_context(out, back, date(2026, 11, 1), date(2026, 11, 4), nights=range(2, 4))
    assert ctx == dict(low=130.0, typical=150.0, days=4)  # trips 150, 130, 140, 150, 160
    assert deals.trip_context(out, None, date(2026, 11, 1), date(2026, 11, 4)) == dict(low=80.0, typical=90.0, days=4)  # one-way
    assert deals.trip_context(pd.Series(dtype=float), None, date(2026, 11, 1), date(2026, 11, 4)) is None
    best = deals.best_trip(out, back, date(2026, 11, 1), date(2026, 11, 4))
    assert best["depart"] == date(2026, 11, 3) and best["nights"] == 2 and best["price"] == 130
    best = deals.best_trip(out, back, date(2026, 11, 1), date(2026, 11, 4), weekdays=["Mon", "Wed"])  # cannot leave on the cheap Tuesday
    assert best["depart"] == date(2026, 11, 2) and best["nights"] == 3 and best["price"] == 150  # Mon 100 + Thu 50 (Wed 90 + Fri 60 ties)
    best = deals.best_trip(out, back, date(2026, 11, 1), date(2026, 11, 4), nights=[2], weekdays=["Mon", "Wed"])
    assert best["depart"] == date(2026, 11, 4) and best["nights"] == 2 and best["price"] == 150
    assert deals.best_trip(out, back, date(2026, 11, 1), date(2026, 11, 4), weekdays=["Sun"]) is None
    g = deals.trip_grid(out, back, date(2026, 11, 1), date(2026, 11, 4), nights=[2, 3], weekdays=["Tue", "Wed"])
    assert list(g.index) == ["Tue 03 Nov", "Wed 04 Nov"] and list(g.columns) == ["2 nights", "3 nights"]
    one = deals.best_trip(out, None, date(2026, 11, 1), date(2026, 11, 4), weekdays=["Mon", "Wed"])
    assert one["depart"] == date(2026, 11, 4) and one["back"] is None and one["price"] == 90


def test_overview_pass_says_whether_the_price_is_good_and_honours_the_filters(tmp_path, monkeypatch):
    import re
    from insights import DAYS
    open_page = pages(tmp_path, monkeypatch, None)
    seed(tmp_path / "tracker.db")
    at = open_page("overview.py").run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    # two plain sentences, no analyst words: the badge, a space, then what a trip usually costs (€210 against ~€360 return trips)
    assert re.search(r'<span class="good">Good price</span> A typical trip costs €3\d\d\.</span>', text)
    assert "Tracking since" not in text and "lowest of" not in text and "typical trip costs" in text
    assert "Cheapest return · Athens → Frankfurt am Main" in text and "one adult, leaving in the next 30 days" in text
    assert "Pick a route, or Anywhere, to start" in at.caption[0].value  # until something is picked
    cheap_day = f"{date.today() + timedelta(10):%a}"  # the €60 outbound
    other = [d for d in DAYS if d != cheap_day][:2]
    at.button_group(key="ov-days").set_value(other).run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    leave = re.search(r"<dt>Leave</dt><dd>(\w{3})", text).group(1)
    assert leave in other and "Good price" not in text  # the pass fits the days asked for; without the €60 day it is no bargain
    assert all(i[:3] in other for i in at.dataframe[0].value.index)
    at.button_group(key="ov-nights").set_value([2, 3]).run()
    assert not at.exception and list(at.dataframe[0].value.columns) == ["2 nights", "3 nights"]
    assert re.search(r"<dt>Stay</dt><dd>[23] nights", " ".join(m.value for m in at.markdown))
    assert "Pick a route, or Anywhere" not in " ".join(c.value for c in at.caption)  # something was picked


def test_overview_bar_drives_the_pass_by_period_and_passengers(tmp_path, monkeypatch):
    """When in the bar is a filter: the pass is the cheapest trip leaving in that period, not the cheapest ever saved."""
    import re
    import trip_plan as tp
    open_page = pages(tmp_path, monkeypatch, None)
    t = seed(tmp_path / "tracker.db")
    today, now = date.today(), datetime.now().isoformat()
    m = date(today.year + (today.month > 9), (today.month + 2) % 12 + 1, 1)  # the month three months ahead: €50 each way, every day
    nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    for i in range((nxt - m).days + 7):
        for o, d in (("ATH", "FRA"), ("FRA", "ATH")):
            t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", ((m + timedelta(i)).isoformat(), o, d, "economy", 50.0, "calendar", now, 1))
    t.commit()
    at = open_page("overview.py").run()
    assert not at.exception and "€210" in " ".join(x.value for x in at.markdown)  # the next 30 days by default
    when = at.selectbox(key="ov-when")
    i = next(i for i, p in enumerate(tp.months(today, nxt)) if p.label == f"{m:%B %Y}")
    assert when.options[0] == "Next 30 days" and when.options[i] == f"{m:%B %Y}"
    when.set_value(i).run()
    assert not at.exception
    text = " ".join(x.value for x in at.markdown)
    assert "€100" in text and f"leaving in {m:%B %Y}" in text and re.search(rf"<dt>Leave</dt><dd>\w{{3}} 0\d {m:%b}", text)
    assert "Prices are much the same whichever day you pick" in text  # €100 against €100
    at.number_input(key="ov-pax").set_value(2).run()
    assert not at.exception and "€200" in " ".join(x.value for x in at.markdown) and "for 2 adults" in " ".join(c.value for c in at.caption)


def test_overview_searches_a_route_with_no_fares_from_the_bar(tmp_path, monkeypatch):
    """To lists every AEGEAN airport, saved ones first; one with nothing saved gets one button that fetches both ways on GitHub."""
    import gh
    open_page = pages(tmp_path, monkeypatch, None)
    t = seed(tmp_path / "tracker.db")
    today, now = date.today(), datetime.now().isoformat()
    started = []
    monkeypatch.setattr(gh, "token", lambda: "tok")
    monkeypatch.setattr(gh, "fetch", lambda tok, legs, nonstop, airline="A3": started.append((legs, nonstop, airline)) or "abcd1234")
    monkeypatch.setattr(gh, "run", lambda tok, request, workflow="fetch-route.yml": None)
    at = open_page("overview.py").run()
    to = at.selectbox(key="ov-grid-dest-ATH")
    assert to.options[:3] == ["Anywhere", "Frankfurt am Main (FRA)", "Zurich (ZRH)"] and "Bilbao (BIO) · no fares yet" in to.options
    assert "Athens (ATH)" not in " ".join(to.options) and to.value == "FRA"  # not itself; the first saved route by default
    to.set_value("BIO").run()
    assert not at.exception and "No economy fares saved from Athens to Bilbao for the next 30 days yet" in at.info[0].value
    key = f"fetch-ahead|ATH|BIO|{today + timedelta(1)}"
    assert at.button(key=key).label == "Search this route on GitHub" and not at.dataframe[:-1]  # no pass, no grid; the route table stays
    at.button(key=key).click().run()
    assert not at.exception and started == [([("ATH", "BIO", today + timedelta(1), today + timedelta(30)),
                                              ("BIO", "ATH", today + timedelta(3), today + timedelta(37))], True, "A3")]
    assert "Searching on GitHub" in " ".join(x.value for x in at.info) and not [b for b in at.button if b.key == key]
    # a route AEGEAN does not fly: every day checked has no flight, so no button, but the nearest airports it does fly
    for i in range(1, 31):
        t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", ((today + timedelta(i)).isoformat(), "ATH", "LGW", "economy", None, "calendar", now, 1))
    t.commit()
    at = open_page("overview.py").run()
    at.selectbox(key="ov-grid-dest-ATH").set_value("LGW").run()
    assert not at.exception and "AEGEAN has no nonstop flights ATH → LGW on the days checked" in at.info[0].value
    assert "Try London Heathrow (LHR)" in at.info[0].value and not [b for b in at.button if b.label.startswith(("Search", "Fetch"))]
    # without a token nothing can be fetched from here: one line pointing to the Flights page
    monkeypatch.setattr(gh, "token", lambda: None)
    at = open_page("overview.py").run()
    at.selectbox(key="ov-grid-dest-ATH").set_value("BIO").run()
    assert not at.exception and not [b for b in at.button if b.label.startswith(("Search", "Fetch"))]
    assert "The next 30 days from Athens to Bilbao is not fetched yet. Search those dates on the Flights page" in " ".join(c.value for c in at.caption)


def test_overview_folds_the_insights_under_the_answer(tmp_path, monkeypatch):
    """The former Insights page's two charts, as expanders under the grid, for the route and cabin in the bar."""
    import re
    open_page = pages(tmp_path, monkeypatch, None)
    seed(tmp_path / "tracker.db")
    at = open_page("overview.py").run()
    assert not at.exception
    labels = [x.label for x in at.expander]
    assert labels[-2:] == ["Best weekday to fly", "How far ahead to book"]
    text = " ".join(m.value for m in at.markdown)
    assert re.search(r"is the cheapest day to fly Athens → Frankfurt am Main|No strong weekday pattern on Athens → Frankfurt am Main", text)
    assert "next 2 weeks" in text or "Not enough weeks saved yet" in text
    captions = " ".join(c.value for c in at.caption)
    assert "Average economy fare by the weekday you leave, over the 30 days" in captions
    at.button_group(key="ov-grid-cabin").set_value("business").run()
    assert not at.exception and "Average business fare by the weekday" in " ".join(c.value for c in at.caption)
    at.button_group(key="ov-grid-pay").set_value("miles").run()
    assert not at.exception and not [x for x in at.expander if x.label == "Best weekday to fly"]  # miles have no such pattern to show


def test_overview_grid_in_miles(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch, None)
    t = seed(tmp_path / "tracker.db")
    d = lambda i: (date.today() + timedelta(i)).isoformat()
    for o, dest, i, miles in (("ATH", "FRA", 5, 9000), ("FRA", "ATH", 8, 10000), ("FRA", "ATH", 9, 12000)):
        t.execute("INSERT INTO award_miles(day, orig, dest, cabin, miles, nonstop, fetched, taxes) VALUES(?,?,?,?,?,?,?,?)",
                  (d(i), o, dest, "economy", miles, 1, datetime.now().isoformat(), None))
    t.commit()
    at = open_page("overview.py").run()
    at.button_group(key="ov-grid-pay").set_value("miles").run()
    assert not at.exception
    grid = at.dataframe[0].value
    assert grid.loc[f"{pd.Timestamp(d(5)):%a %d %b}", "3 nights"] == "19,000"  # 9,000 out + 10,000 back three nights later
    text = " ".join(m.value for m in at.markdown)
    assert "19,000<small> miles</small>" in text and "plus taxes and fees" in text and "3 nights" in text


def test_pages_reload_guards_match_the_module_versions():
    """Streamlit Cloud keeps already loaded modules across redeploys; a page reloads one only when its VERSION is below the number in the
    page's guard. A guard left behind when the module's VERSION goes up lets the page run against the old copy (AttributeError live)."""
    import glob
    import importlib
    import re
    for page in glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "views", "*.py")):
        src = open(page, encoding="utf-8").read()
        aliases = {alias: mod for mod, alias in re.findall(r"^import (\w+) as (\w+)$", src, re.M)}  # import overview as ov -> ov: overview
        for alias, n in re.findall(r'getattr\((\w+), "VERSION", 0\) < (\d+)', src):
            mod = importlib.import_module(aliases.get(alias, alias))
            assert int(n) == mod.VERSION, f"{os.path.basename(page)}: guard for {mod.__name__} says {n}, the module is VERSION {mod.VERSION}"


def test_any_route_with_saved_fares_works_like_the_daily_ones(tmp_path, monkeypatch):
    """BIO searched on the Flights page: it shows on the Overview (route cards, return grid From Bilbao) and the expert plans from it."""
    open_page = pages(tmp_path, monkeypatch, "test")
    t = seed(tmp_path / "tracker.db")
    now = datetime.now().isoformat()
    for i in range(1, 31):
        for o, d in (("BIO", "ATH"), ("ATH", "BIO")):
            t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", ((date.today() + timedelta(i)).isoformat(), o, d, "economy",
                                                                     90.0 + i, "calendar", now, 1))
    t.commit()
    import trip_plan as tp
    assert "BIO" in tp.origins(t) and tp.destinations(t, "BIO") == ["ATH"]
    plan = tp.candidates(t, "ATH", date.today() + timedelta(1), date.today() + timedelta(20), "economy", 3, None, None, "cash", "BIO")
    assert plan.iloc[0].cash == 91 + 94  # out on day 1 (€91), back 3 nights later on day 4 (€94)
    assert "from Bilbao (BIO) to Athens (ATH)" in tp.question("ATH", "Next 30 days", "economy", "cash", 3, None, None, "BIO")

    monkeypatch.setattr(ex, "briefing", lambda facts, cfg, day: "- ok")
    at = open_page("overview.py").run()
    assert not at.exception and "BIO → ATH" in list(at.dataframe[-1].value.Route)
    at.selectbox(key="ov-grid-orig").set_value("BIO").run()
    assert not at.exception and "Bilbao → Athens → Bilbao" in " ".join(c.value for c in at.caption)
    assert at.selectbox(key="ov-grid-dest-BIO").value == "ATH"  # the first saved destination from there, not Anywhere

    seen = []
    monkeypatch.setattr(ex, "chat", lambda history, facts, cfg: (seen.append((history, facts)), iter(["Answer"]))[1])
    at = open_page("expert.py")
    at.query_params["from"], at.query_params["to"] = "BIO", "ATH"  # the Overview's "Ask the expert about it" link
    at.run()
    assert at.selectbox(key="plan-orig").value == "BIO"
    at.button[0].click().run()
    assert not at.exception and "from Bilbao (BIO) to Athens (ATH)" in seen[-1][0][-1]["content"] and "Bilbao (BIO) to Athens" in seen[-1][1]
