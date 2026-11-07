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


NO_HISTORY = pd.DataFrame(columns=["day", "orig", "dest", "cabin", "price", "source", "fetched", "nonstop"])  # price_insights.COLUMNS


def expert_facts(t, pay="cash", nights=(), wdays=(), cabin="economy", hotel=None, end=30):
    """expert_chat.facts over the seeded database for the next `end` days, without the committed price history."""
    today = date.today()
    return ex.facts(t, "ATH", "FRA", cabin, pay, list(nights), list(wdays), (today + timedelta(1), today + timedelta(end)), today, hotel,
                    hist=NO_HISTORY)


def test_expert_facts_are_the_overview_pass_and_the_plain_briefing_names_the_same_trip(tmp_path):
    import deals
    import trip_plan as tp
    from insights import DAYS, cash_by_day
    t = seed(tmp_path / "t.db")
    today = date.today()
    start, end = today + timedelta(1), today + timedelta(30)
    f = expert_facts(t, hotel=tp.hotels(t, "FRA")[0])
    out, back = cash_by_day(t, "ATH", "FRA", "economy", True, start, end), cash_by_day(t, "FRA", "ATH", "economy", True, start, end + timedelta(7))
    assert f["best"] == deals.best_trip(out, back, start, end, ex.NIGHTS, DAYS)  # the very trip the Overview's boarding pass shows
    assert f["best"]["price"] == 210 and f["best"]["nights"] == 4 and f["best"]["depart"] == today + timedelta(10)  # €60 out, €150 back
    assert f["ctx"] == deals.trip_context(out, back, start, end) and f["hotel"] == dict(name="Steigenberger", adults=2, price=800.0)
    assert f["insight"]["level"] is None and f["skyexpress"] is None and f["award"] is None  # no price history, no Sky Express fares
    week = lambda d: d - timedelta(d.weekday())
    assert f["other"]["price"] > 210 and week(f["other"]["depart"]) != week(f["best"]["depart"])  # the alternative leaves in another week
    a, b, c = ex.sentences(f)
    assert a.startswith("The cheapest economy return from Athens to Frankfurt with these filters is €210: leave ")
    assert "4 nights" in a and "Steigenberger for those nights adds €800, so €1,010 in all" in a
    assert f"leaves on a {today + timedelta(10):%A}, 10 days from today" in b and "Trips like this in this period usually cost about €3" in b
    assert c.startswith("If those dates do not suit, the next-cheapest week is €") and f"{f['other']['nights']} nights" in c
    text = ex.text(f)
    assert "Recommended trip (the one the Overview's boarding pass shows): leave" in text and "4 nights, €210." in text
    assert f"Fares are saved up to {end:%a %d %b %Y}" in text and "Hotel: Steigenberger (2 adults) for those nights €800" in text
    assert ex.prompt(f).startswith(f"The recommended trip is: {a} Explain it; do not propose a different one unless I ask")
    assert "do not propose a different trip unless" in ex.SYSTEM and "I only have fares up to 29 January" in ex._system(text)  # the chat's rules
    # the Overview's pills narrow the pass; the same filters narrow the facts the same way
    other = [d for d in DAYS if d != f"{today + timedelta(10):%a}"][:2]
    g = expert_facts(t, nights=[2, 3], wdays=other)
    assert g["best"] == deals.best_trip(out, back, start, end, [2, 3], other) and g["best"]["price"] > 210 and g["hotel"] is None
    assert g["nights"] == [2, 3] and g["wdays"] == other
    # nothing saved for the filters: one sentence, and no question is spent on the model
    n = expert_facts(t, pay="miles", cabin="business")
    assert n["best"] is None and ex.text(n).endswith("Recommended trip: none, no fare is saved for these filters.")
    assert ex.sentences(n)[0].startswith("No business award seats are saved from Athens to Frankfurt")
    assert ex.briefing(n, ("gemini", "g"), "2026-10-31") == (ex.sentences(n)[0], None)


def test_expert_facts_in_miles_and_against_sky_express(tmp_path):
    t = seed(tmp_path / "t.db")
    today, now = date.today(), datetime.now().isoformat()
    d = lambda i: (today + timedelta(i)).isoformat()
    for o, dest, i, miles in (("ATH", "FRA", 5, 9000), ("FRA", "ATH", 8, 10000), ("FRA", "ATH", 9, 12000)):
        t.execute("INSERT INTO award_miles(day, orig, dest, cabin, miles, nonstop, fetched, taxes) VALUES(?,?,?,?,?,?,?,?)",
                  (d(i), o, dest, "economy", miles, 1, now, None))
    t.commit()
    f = expert_facts(t, pay="miles")
    assert f["best"] == dict(depart=today + timedelta(5), back=today + timedelta(8), nights=3, price=19000.0)  # as the Overview's grid in miles
    assert f["award"] == dict(cash=360.0, taxes=None) and f["cash"]["price"] == 210  # €200 + €160 on those dates; the cheapest cash trip
    a, b, c = ex.sentences(f)
    assert "is 19,000 miles plus taxes instead of €360 in cash: leave " in a and "3 nights" in a and "per 1,000" not in a + b + c
    assert "Prices are much the same whichever day you pick in this period: most trips cost about 20,000 miles" in b  # 19,000 and 21,000 are the only two trips
    assert c.startswith("Paying cash, the cheapest trip with these filters is €210 (leave ") and c.endswith("so the miles are a good use here.")
    assert "Same dates in cash: €360; award taxes: not collected." in ex.text(f)
    f = expert_facts(t)  # paying cash: miles are not mentioned
    assert f["award"] is None and "miles" not in " ".join(ex.sentences(f))
    for i in range(1, 31):  # Sky Express undercuts AEGEAN on every day
        for o, dest in (("ATH", "FRA"), ("FRA", "ATH")):
            t.execute("INSERT INTO gq_prices VALUES(?,?,?,?,?,?,?,?)", (d(i), o, dest, "economy", 50.0, "calendar", now, 1))
    t.commit()
    f = expert_facts(t)
    assert f["skyexpress"]["price"] == 100 and f["skyexpress"]["nights"] == 2
    assert ex.sentences(f)[2].startswith("Sky Express is cheaper: €100 (leave ") and "€110 less than AEGEAN." in ex.sentences(f)[2]
    assert "Sky Express (economy): cheapest leave" in ex.text(f) and "cheaper than AEGEAN by €110." in ex.text(f)
    assert expert_facts(t, cabin="business")["skyexpress"] is None  # Sky Express sells one cabin


def history_for(today, out, back):
    """A price history (price_insights.COLUMNS) in which the ATH→FRA fare of departure day `today + 10` was `out[i]` on fetch day
    `today - len(out) + i` (one row per change, as the daily run writes) and the FRA→ATH fare of `today + 14` was `back` throughout."""
    rows, n = [], len(out)
    for i, price in enumerate(out):
        at = datetime.combine(today - timedelta(n - i), datetime.min.time()) + timedelta(hours=6)
        if i == 0 or price != out[i - 1]:
            rows.append([(today + timedelta(10)).isoformat(), "ATH", "FRA", "economy", float(price), "calendar", at, "1"])
        if i == 0:
            rows.append([(today + timedelta(14)).isoformat(), "FRA", "ATH", "economy", float(back), "calendar", at, "1"])
    return pd.DataFrame(rows, columns=NO_HISTORY.columns)


def test_expert_facts_for_the_suggested_questions(tmp_path):
    """The facts the suggested questions need: the neighbouring days, a week away, both cabins on the same dates, Sky Express on the same
    dates and the fare's trend over the saved history; and questions() offers only the ones the facts can answer."""
    import trip_plan as tp
    t = seed(tmp_path / "t.db")
    today = date.today()
    d = lambda i: today + timedelta(i)
    f = expert_facts(t, hotel=tp.hotels(t, "FRA")[0])
    # a day either side of the pass, the same 4 nights: €170 + €210 and €190 + €160 (the seeded fares by day)
    assert f["near"] == dict(earlier=dict(depart=d(9), back=d(13), nights=4, price=380.0), later=dict(depart=d(11), back=d(15), nights=4, price=350.0))
    assert f["week"] == dict(depart=d(10), back=d(17), nights=7, price=240.0)  # €60 out, €180 back a week later
    assert f["cabins"] == dict(economy=210.0, business=410.0)  # business is €100 more each way in the seeded database
    assert f["trend"] is None and f["skyexpress_same"] is None and f["aegean_same"] is None and f["gq_serves"]  # no history, no Sky fares
    assert ex.questions(f) == [ex.NEAR, ex.WEEK, ex.BUSINESS, ex.SKY, ex.BOOK]
    text = ex.text(f)
    assert "A day earlier: leave " in text and "4 nights, €380. A day later: leave " in text and "4 nights, €350." in text
    assert "Cheapest 7-night trip with these filters: leave " in text and "7 nights, €240." in text
    assert "The recommended dates in each cabin: economy €210; business €410." in text
    assert "Sky Express flies this route too, but no Sky Express fare is saved for these days." in text
    assert "Answers to common questions:" in text and f"- {ex.WEEK} {ex.answer(f, ex.WEEK)}" in text and f"- {ex.BOOK} " in text
    assert "typical" not in text and "median" not in text and "snapshot" not in text and "per 1,000" not in text
    # the last days of the fares, 2 to 3 nights: a week away would end past the saved fares, so there is no 7-night trip to offer
    g = ex.facts(t, "ATH", "FRA", "economy", "cash", [2, 3], [], (d(28), d(30)), today, None, hist=NO_HISTORY)
    assert g["best"]["depart"] == d(28) and g["week"] is None and ex.WEEK not in ex.questions(g)
    # one way: no nights, no week, neighbours one way
    o = ex.facts(t, "ATH", "FRA", "economy", "cash", [], [], (d(1), d(30)), today, None, hist=NO_HISTORY, oneway=True)
    assert o["best"]["back"] is None and o["week"] is None and o["near"]["earlier"] == dict(depart=d(9), back=None, nights=None, price=170.0)
    assert o["cabins"] == dict(economy=60.0, business=160.0) and ex.questions(o) == [ex.NEAR, ex.BUSINESS, ex.SKY, ex.BOOK]
    # the trend of the recommended dates: the out fare fell from €100 to €60 over nine fetch days, the back fare stayed €150
    f = ex.facts(t, "ATH", "FRA", "economy", "cash", [], [], (d(1), d(30)), today, None, hist=history_for(today, [100, 95, 90, 85, 80, 75, 70, 65, 60], 150))
    assert f["trend"] == dict(days=9, first=250.0, last=210.0, low=210.0, high=250.0, since=d(-9), week_ago=245.0)  # a week before the newest: day -8
    assert f["insight"]["level"] == "low" and (f["insight"]["low"], f["insight"]["high"]) == (220.0, 240.0)
    assert "The fare of the recommended dates over the saved history: first seen €250 on " in ex.text(f) and "over 9 days; a week ago €245." in ex.text(f)
    # miles: no award seats on the neighbouring days, no business seats, no 7-night trip, Sky Express sells no miles: nothing to suggest
    now = datetime.now().isoformat()
    for o_, dest, i, miles in (("ATH", "FRA", 5, 9000), ("FRA", "ATH", 8, 10000)):
        t.execute("INSERT INTO award_miles(day, orig, dest, cabin, miles, nonstop, fetched, taxes) VALUES(?,?,?,?,?,?,?,?)",
                  (d(i).isoformat(), o_, dest, "economy", miles, 1, now, None))
    t.commit()
    m = expert_facts(t, pay="miles")
    assert m["near"]["earlier"]["price"] is None and m["near"]["later"]["price"] is None and m["cabins"] == dict(economy=19000.0, business=None)
    assert m["week"] is None and not m["gq_serves"] and m["trend"] is None and ex.questions(m) == []
    assert "Answers to common questions" not in ex.text(m)
    # Sky Express fares: its price on AEGEAN's dates and AEGEAN's on its dates
    for i in range(1, 31):
        for o_, dest in (("ATH", "FRA"), ("FRA", "ATH")):
            t.execute("INSERT INTO gq_prices VALUES(?,?,?,?,?,?,?,?)", (d(i).isoformat(), o_, dest, "economy", 50.0, "calendar", now, 1))
    t.commit()
    f = expert_facts(t)
    assert f["skyexpress"]["price"] == 100 and f["skyexpress_same"] == 100.0 and f["aegean_same"] == 340.0  # €160 out on day 1, €180 back on day 3
    assert "Sky Express on the recommended dates: €100; AEGEAN on Sky Express's dates: €340." in ex.text(f)


def test_each_suggested_question_has_a_plain_answer_from_the_facts(tmp_path):
    import trip_plan as tp
    t = seed(tmp_path / "t.db")
    today = date.today()
    d = lambda i: today + timedelta(i)
    when = lambda x: f"{x:%A} {x.day} {x:%B}" + (f" {x.year}" if x.year != today.year else "")
    f = expert_facts(t, hotel=tp.hotels(t, "FRA")[0])
    # a day earlier or later: both dearer, so no
    assert ex.answer(f, ex.NEAR) == (f"No: a day earlier ({when(d(9))}) costs €380, €170 more, and a day later ({when(d(11))}) costs €350, "
                                     "€140 more, for the same 4 nights.")
    # ... but when the weekday filter left the cheap day out, the neighbour is cheaper and the answer says why the pass did not take it
    g = ex.facts(t, "ATH", "FRA", "economy", "cash", [], [f"{d(9):%a}", f"{d(11):%a}"], (d(8), d(30)), today, None, hist=NO_HISTORY)
    assert g["best"] == dict(depart=d(9), back=d(14), nights=5, price=320.0)  # €170 out, €150 back
    assert ex.answer(g, ex.NEAR) == (f"Yes: leaving a day later (leave {when(d(10))}, back {when(d(15))}, 5 nights) costs €220, €100 less for the "
                                     f"same 5 nights, but {d(10):%A} is not among the days you can leave. The other way, a day earlier "
                                     f"({when(d(8))}) costs €370, €50 more.")
    # a week away
    assert ex.answer(f, ex.WEEK) == (f"A week away costs €240: leave {when(d(10))}, back {when(d(17))}, 7 nights. That is €30 more than the "
                                     "4-night trip, and the hotel would add 3 more nights.")
    w = expert_facts(t, nights=[7])
    assert w["best"]["nights"] == 7 and ex.answer(w, ex.WEEK) == f"The recommended trip is already a week: leave {when(d(10))}, back {when(d(17))}, 7 nights, €240."
    n = dict(f, week=None)
    assert ex.answer(n, ex.WEEK) == "No 7-night trip is saved for these days: the way back would fall after the last day with fares."
    # business: the same dates in the other cabin, and what the gap means
    assert ex.answer(f, ex.BUSINESS) == ("Business on the same dates costs €410, €200 more than the €210 economy fare: about double economy, "
                                         "worth it only if the comfort matters more to you than the money.")
    b = expert_facts(t, cabin="business")
    assert b["best"]["price"] == 410 and ex.answer(b, ex.BUSINESS) == ("Economy on the same dates costs €210, €200 less than the €410 business fare: "
                                                                        "about double economy, worth it only if the comfort matters more to you than the money.")
    assert ex.answer(dict(f, cabins=dict(economy=210.0, business=300.0)), ex.BUSINESS).endswith("a small gap, so business is worth it if you value the wider seat and the lounge.")
    assert ex.answer(dict(f, cabins=dict(economy=210.0, business=700.0)), ex.BUSINESS).endswith("3.3 times the economy fare, a steep premium that is rarely worth it.")
    assert ex.answer(dict(f, cabins=dict(economy=210.0, business=210.0)), ex.BUSINESS) == ("Business on the same dates costs €210, the same price as the €210 "
                                                                                          "economy fare: business costs no more here, so take it.")
    assert ex.answer(dict(f, cabins=dict(economy=210.0, business=None)), ex.BUSINESS) == "No business fare is saved for these dates to compare with."
    # Sky Express: none saved, cheaper, dearer, the same
    assert ex.answer(f, ex.SKY) == "No Sky Express fare is saved for these days yet. Search the route on the Flights page and the two airlines appear side by side."
    gq = dict(depart=d(1), back=d(3), nights=2, price=100.0)
    assert ex.answer(dict(f, skyexpress=gq, skyexpress_same=100.0, aegean_same=340.0), ex.SKY) == (
        f"Sky Express is cheaper: €100 (leave {when(d(1))}, back {when(d(3))}, 2 nights, economy) against AEGEAN's €210, €110 less. "
        "On AEGEAN's dates Sky Express costs €100; on Sky Express's dates AEGEAN costs €340.")
    assert ex.answer(dict(f, skyexpress=dict(gq, price=300.0), skyexpress_same=None, aegean_same=None), ex.SKY) == (
        f"AEGEAN is cheaper: €210 against Sky Express's cheapest €300 (leave {when(d(1))}, back {when(d(3))}, 2 nights, economy), €90 less. "
        "Sky Express has no flight on AEGEAN's dates; AEGEAN has no flight on Sky Express's dates.")
    assert ex.answer(dict(f, skyexpress=dict(gq, price=210.0), skyexpress_same=250.0, aegean_same=None), ex.SKY).startswith("The same: Sky Express's cheapest trip is also €210 (")
    # book now or wait: from the usual range and the trend of these dates' fare
    assert ex.answer(f, ex.BOOK) == ("Hard to say yet: no price history is saved for this route yet, too few to call €210 low or high. Book when the dates "
                                     "suit you, and set an alert on the Alerts page in case it drops.")
    fell = ex.facts(t, "ATH", "FRA", "economy", "cash", [], [], (d(1), d(30)), today, None, hist=history_for(today, [100, 95, 90, 85, 80, 75, 70, 65, 60], 150))
    assert ex.answer(fell, ex.BOOK) == (f"Book now: €210 is a low fare for this route, which usually costs €220–€240 return. The fare for these dates "
                                        f"has fallen from €250 to €210 since {when(d(-9))} (9 days of prices), and lately it is falling; it is at its lowest so far.")
    rose = ex.facts(t, "ATH", "FRA", "economy", "cash", [], [], (d(1), d(30)), today, None, hist=history_for(today, [20, 25, 30, 35, 40, 45, 50, 55, 60], 150))
    assert ex.answer(rose, ex.BOOK) == (f"Wait if you can: €210 is high for this route, which usually costs €180–€200 return. Set an alert on the Alerts "
                                        f"page and it tells you when it drops. The fare for these dates has risen from €170 to €210 since {when(d(-9))} "
                                        "(9 days of prices), and lately it is rising.")
    flat = ex.facts(t, "ATH", "FRA", "economy", "cash", [], [], (d(1), d(30)), today, None, hist=history_for(today, [50, 55, 60, 62, 65, 70, 62, 65, 62, 60], 150))
    assert ex.answer(flat, ex.BOOK) == (f"No rush, but no reason to wait either: €210 is about what this route usually costs (€210–€214 return). The fare for "
                                        f"these dates has risen from €200 to €210 since {when(d(-10))} (10 days of prices), and lately it has been steady.")
    few = ex.facts(t, "ATH", "FRA", "economy", "cash", [], [], (d(1), d(30)), today, None, hist=history_for(today, [60, 65, 60], 150))
    assert ex.answer(few, ex.BOOK) == (f"Hard to say yet: the route has only 3 days of prices saved since {when(d(-3))}, too few to call €210 low or high. "
                                       f"Book when the dates suit you, and set an alert on the Alerts page in case it drops. The fare for these dates is back "
                                       f"at €210 after moving between €210 and €215 since {when(d(-3))} (3 days of prices).")
    still = dict(few, trend=dict(days=5, first=210.0, last=210.0, low=210.0, high=210.0, since=d(-5), week_ago=None))
    assert ex.answer(still, ex.BOOK).endswith(f"The fare for these dates has not moved since {when(d(-5))} (5 days of prices).")
    for q in ex.questions(f):  # every answer is plain: no banned words, no number the model could not have
        a = ex.answer(f, q)
        assert "typical" not in a and "median" not in a and "snapshot" not in a and "per 1,000" not in a
    with pytest.raises(KeyError):
        ex.answer(f, "What about hotels?")  # a typed question is the model's


def test_briefing_is_asked_once_per_search_and_day_and_falls_back_to_the_plain_sentences(tmp_path, monkeypatch):
    t = seed(tmp_path / "t.db")
    f, g = expert_facts(t), expert_facts(t, nights=[2, 3])
    plain = " ".join(ex.sentences(f))
    asked = []
    monkeypatch.setattr(ex, "chat", lambda history, facts, cfg: (asked.append((history, facts)), iter(["Fly on the 10th."]))[1])
    monkeypatch.setattr(ex, "_failed", {})
    ex._briefing.clear()
    assert ex.briefing(f, ("gemini", "g"), "2026-10-30") == ("Fly on the 10th.", None)
    ex.briefing(f, ("gemini", "g"), "2026-10-30")
    assert len(asked) == 1 and asked[0][0][0]["content"] == ex.prompt(f) and asked[0][1] == ex.text(f)  # the second visitor gets the cached briefing
    ex.briefing(g, ("gemini", "g"), "2026-10-30")
    assert len(asked) == 2  # another search, another briefing
    ex.briefing(f, ("gemini", "g"), "2026-10-31")
    assert len(asked) == 3  # a new day, a new briefing
    assert ex.briefing(f, None, "2026-10-31") == (plain, "Written from the saved fares. With an AI key the expert puts it in its own words "
                                                          "and answers questions.")
    assert len(asked) == 3  # without a key the plain sentences cost nothing

    def broken(history, facts, cfg):
        asked.append(history)
        raise TimeoutError("timed out")
    monkeypatch.setattr(ex, "chat", broken)
    assert ex.briefing(f, ("gemini", "g"), "2026-11-01") == (plain, "Written from the saved fares. The expert is unavailable right now (TimeoutError).")
    ex.briefing(f, ("gemini", "g"), "2026-11-01")
    assert len(asked) == 4  # remembered: the next visitor neither waits nor spends a question, and still reads the same trip
    now = ex.time.time()
    monkeypatch.setattr(ex.time, "time", lambda: now + 901)
    ex.briefing(f, ("gemini", "g"), "2026-11-01")
    assert len(asked) == 5  # but tried again after 15 minutes, and a success is not blocked by the old failure

    def quota(history, facts, cfg):
        raise RuntimeError("Gemini's free quota is used up for now. Try again later.")
    monkeypatch.setattr(ex, "chat", quota)
    assert ex.briefing(g, ("gemini", "g"), "2026-11-01") == (" ".join(ex.sentences(g)), "Written from the saved fares. Gemini's free quota is "
                                                                                           "used up for now. Try again later.")


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


def grid(at):
    """The date grid (or the one-way day list) the Overview draws: deals.grid_html in its own frame (ui.grid_frame), read back from the
    frame's HTML as a DataFrame of cell texts, the days as index. Empty when the page drew none."""
    import re
    frames = [f.proto.srcdoc for f in at.get("iframe") if 'class="grid"' in f.proto.srcdoc]
    if not frames:
        return pd.DataFrame()
    cell = lambda row: [re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row)]
    rows = [cell(r) for r in re.findall(r"<tr>(.*?)</tr>", frames[0])]
    return pd.DataFrame([r[1:] for r in rows[1:]], index=[r[0] for r in rows[1:]], columns=rows[0][1:])


def routes(at):
    """The route table that closes the Overview (ui.html_table): [(route, economy, business)]."""
    import re
    t = next(m.value for m in at.markdown if 'class="h2h' in m.value and "<th>Route</th>" in m.value)
    return [tuple(re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<t[hd]>(.*?)</t[hd]>", r)) for r in re.findall(r"<tr>(.*?)</tr>", t)[1:]]


def page_links(monkeypatch):
    """Record every page_link the page draws: [(page, label, query_params)]. The links to the app's other pages cannot render outside its
    navigation (the page swallows that error), so the test reads what the page asked for."""
    from streamlit.delta_generator import DeltaGenerator
    seen = []
    monkeypatch.setattr(DeltaGenerator, "page_link", lambda self, page, label=None, query_params=None, **kw: seen.append((page, label, query_params)))
    return seen


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
    at.button(key="ov-grid-more-ATHFRA").click().run()  # the grid shows a week first; the button opens the whole month
    g, table = grid(at), routes(at)
    assert "4 nights" in g.columns and [r[0] for r in table] == ["ATH → FRA", "FRA → ATH", "ATH → ZRH", "ZRH → ATH"]
    assert table[0][1].startswith("€60 on ") and g.loc[f"{date.today() + timedelta(10):%a %d %b}", "4 nights"] == "€210"
    assert 'id="low">€210' in at.get("iframe")[0].proto.srcdoc  # the cheapest cell is marked, so the frame scrolls it into view


def test_expert_page_setup_briefing_and_a_follow_up_conversation(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch, None)
    seed(tmp_path / "tracker.db")
    at = open_page("expert.py").run()
    assert not at.exception and "needs an AI key" in at.info[0].value and not at.chat_input
    text = " ".join(m.value for m in at.markdown)  # no key: the briefing is still there, built in Python from the Overview's numbers
    assert "The cheapest economy return from Athens to Frankfurt with these filters is €210: leave " in text and "4 nights" in text
    assert "Written from the saved fares. With an AI key" in at.caption[0].value
    # the suggested questions are there without a key, answered from the facts the moment one is clicked, and the pill clears again
    today = date.today()
    pills = at.button_group(key="expert-ask")
    assert pills.options == [ex.NEAR, ex.WEEK, ex.BUSINESS, ex.SKY, ex.BOOK] and pills.value is None
    pills.set_value(ex.WEEK).run()
    assert not at.exception and at.button_group(key="expert-ask").value is None
    assert [m.markdown[0].value for m in at.chat_message] == [ex.WEEK, f"A week away costs €240: leave {today + timedelta(10):%A} "
                                                              f"{(today + timedelta(10)).day} {today + timedelta(10):%B}, back {today + timedelta(17):%A} "
                                                              f"{(today + timedelta(17)).day} {today + timedelta(17):%B}, 7 nights. That is €30 more than the "
                                                              "4-night trip, and the hotel would add 3 more nights."]
    at.button_group(key="expert-ask").set_value(ex.BUSINESS).run()
    assert [m.markdown[0].value for m in at.chat_message][2:] == [ex.BUSINESS, "Business on the same dates costs €410, €200 more than the €210 economy "
                                                                  "fare: about double economy, worth it only if the comfort matters more to you than the money."]
    assert len(at.session_state["expert-chat"]) == 4 and "answered from the prices saved by the daily run" in at.caption[-1].value
    # book it from here: the Overview's two links, for the pass's dates
    links = [b for b in at.get("link_button")]
    assert [b.label for b in links] == ["Book on aegeanair.com", "Check it on Google Flights"]
    assert f"DateDeparture={today + timedelta(10):%d/%m/%Y}" in links[0].url and f"DateReturn={today + timedelta(14):%d/%m/%Y}" in links[0].url
    assert f"{today + timedelta(10):%Y-%m-%d}" in links[1].url and "Aegean" in links[1].url
    at.button[0].click().run()  # "New conversation" clears the answered pills too
    assert not at.chat_message and not at.session_state["expert-chat"]
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(ex, "briefing", lambda f, cfg, day: ("- **Book now:** €60", None))
    seen = []
    monkeypatch.setattr(ex, "chat", lambda history, facts, cfg: (seen.append([m["content"] for m in history]), iter([f"Answer {len(seen)}"]))[1])
    at = open_page("expert.py").run()
    assert "€60" in " ".join(m.value for m in at.markdown)
    at.chat_input[0].set_value("When is it cheapest?").run()
    at.chat_input[0].set_value("And for 4 nights?").run()
    assert not at.exception and seen[-1] == ["When is it cheapest?", "Answer 1", "And for 4 nights?"]  # the follow-up carries the conversation
    assert [m.markdown[0].value for m in at.chat_message] == ["When is it cheapest?", "Answer 1", "And for 4 nights?", "Answer 2"]
    assert "Google Gemini (free tier)" in at.caption[-1].value
    at.button_group(key="expert-ask").set_value(ex.NEAR).run()  # a pill with a key: still answered in Python, and the model sees it next
    assert len(seen) == 2 and [m.markdown[0].value for m in at.chat_message][4] == ex.NEAR and at.chat_message[5].markdown[0].value.startswith("No: a day earlier (")
    at.chat_input[0].set_value("Why?").run()
    assert seen[-1] == ["When is it cheapest?", "Answer 1", "And for 4 nights?", "Answer 2", ex.NEAR, at.chat_message[5].markdown[0].value, "Why?"]

    def busy(history, facts, cfg):
        raise RuntimeError("Gemini did not answer in time. Try again in a minute.")
    monkeypatch.setattr(ex, "chat", busy)
    at.chat_input[0].set_value("And for 7 nights?").run()
    assert "did not answer in time" in at.error[0].value
    assert len(at.session_state["expert-chat"]) == 8  # the unanswered question is not kept, so it does not confuse the next answer


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


def test_expert_page_reads_the_overview_pass_and_opens_on_the_pass_the_link_names(tmp_path, monkeypatch):
    from insights import DAYS
    open_page = pages(tmp_path, monkeypatch, "test")
    seed(tmp_path / "tracker.db")
    briefed, seen = [], []
    monkeypatch.setattr(ex, "briefing", lambda f, cfg, day: (briefed.append(f), ("Briefing", None))[1])
    monkeypatch.setattr(ex, "chat", lambda history, facts, cfg: (seen.append((history, facts)), iter([f"Answer {len(seen)}"]))[1])
    at = open_page("expert.py").run()
    assert not at.exception
    # the defaults are the Overview's: economy, cash, 2 to 7 nights, any day, the pass's own window, the hotel searched in the city
    assert [at.button_group(key=k).value for k in ("plan-cabin", "plan-pay", "plan-trip", "plan-days")] == ["economy", "cash", "return", DAYS]
    assert at.slider(key="plan-nights").value == (2, 7)  # the Overview's default stay
    assert at.date_input(key="plan-when").value == (date.today() + timedelta(1), date.today() + timedelta(30))  # the Overview's default window
    f = briefed[-1]
    assert f["best"]["price"] == 210 and f["best"]["nights"] == 4 and f["hotel"]["name"] == "Steigenberger"  # the Overview's pass
    at.chat_input[0].set_value("Why those dates?").run()
    history, facts = seen[-1]
    assert history[-1]["content"] == "Why those dates?" and facts.startswith(ex.text(f)) and "All tracked routes" in facts
    assert f"- {ex.BOOK} Hard to say yet:" in facts and "never\n  write a number" in ex.SYSTEM  # the ready answers, and no number outside them
    assert "Best ATH-FRA-ATH economy trip" not in facts  # the Overview module's Frankfurt totals (with a hotel) would contradict the pass
    at.slider(key="plan-nights").set_value((2, 3)).run()  # a new search: new facts, and the old answers go
    assert not at.exception and briefed[-1]["nights"] == [2, 3] and briefed[-1]["best"]["price"] > 210 and not at.chat_message
    # the Overview's "Ask the expert about it" link names the pass the traveller saw
    at = open_page("expert.py")
    a, b = date.today() + timedelta(40), date.today() + timedelta(46)  # the days the pass was for, e.g. mid-December
    at.query_params.update({"from": "ATH", "to": "ZRH", "cabin": "business", "pay": "miles", "nights": "3-4", "trip": "return", "when": f"{a},{b}"})
    at.run()
    assert not at.exception and at.selectbox(key="plan-dest-ATH").value == "ZRH"
    assert [at.button_group(key=k).value for k in ("plan-cabin", "plan-pay")] == ["business", "miles"] and at.slider(key="plan-nights").value == (3, 4)
    assert at.date_input(key="plan-when").value == (a, b)  # the same days as the pass
    assert briefed[-1]["dest"] == "ZRH" and briefed[-1]["pay"] == "miles" and briefed[-1]["best"] is None  # no award seats seeded


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


def test_grid_html_marks_the_cheapest_and_keeps_the_days_as_row_headers():
    import deals
    out = pd.Series({(date(2027, 3, 1) + timedelta(i)).isoformat(): 100.0 + i for i in range(3)})
    back = pd.Series({(date(2027, 3, 2) + timedelta(i)).isoformat(): 50.0 for i in range(4)})
    g = deals.trip_grid(out, back, date(2027, 3, 1), date(2027, 3, 3), [1, 2])
    h = deals.grid_html(g, "€{:,.0f}".format)
    assert list(g.columns) == ["1 night", "2 nights"] and h.startswith('<table class="grid"><thead><tr><th class="corner">Leave ↓ · stay →</th><th>1 night</th><th>2 nights</th></tr>')
    assert h.count('id="low"') == 1 and 'id="low">€150</td>' in h and "<tr><th>Mon 01 Mar</th>" in h  # the first cheapest cell, once
    assert 'background-color:#0E7A53;color:#fff;font-weight:650" id="low"' in h and "<td>—</td>" not in h.split("<tr><th>Mon 01 Mar")[1].split("</tr>")[0]
    one = pd.DataFrame({"One way": [None, 80.0]}, index=["Mon 01 Mar", "Tue 02 Mar"])
    assert "<td>—</td>" in deals.grid_html(one, "€{:,.0f}".format) and 'id="low">€80' in deals.grid_html(one, "€{:,.0f}".format)
    assert "&lt;" in deals.grid_html(pd.DataFrame({"<b>": [1.0]}, index=["<i>"]), str)  # every text escaped


def test_overview_alert_link_carries_the_pass(tmp_path, monkeypatch):
    """Alert me if it gets cheaper opens the Alerts page on exactly the trip shown: route, cabin, unit, price, dates, nights and trip."""
    open_page = pages(tmp_path, monkeypatch, None)
    t = seed(tmp_path / "tracker.db")
    seen = page_links(monkeypatch)
    at = open_page("overview.py").run()
    assert not at.exception
    d10, d14 = date.today() + timedelta(10), date.today() + timedelta(14)
    alert = next(q for page, label, q in seen if page == "views/alerts.py" and label == "Track this price")
    when = f"{date.today() + timedelta(1)},{date.today() + timedelta(30)}"
    assert alert == {"route": "ATH-FRA", "cabin": "economy", "unit": "eur", "price": "210", "depart": d10.isoformat(), "back": d14.isoformat(),
                     "nights": "2-7", "trip": "return", "when": when}  # €60 out on day 10, €150 back 4 nights later, per adult; the bar's nights
    expert = next(q for page, label, q in seen if page == "views/expert.py")
    assert expert["from"] == "ATH" and expert["to"] == "FRA" and expert["nights"] == "2-7"
    at.number_input(key="ov-pax").set_value(2).run()  # the alert is on the fare, which is collected for one adult
    assert not at.exception and seen[-2][2]["price"] == "210"
    at.button_group(key="ov-trip").set_value("oneway").run()
    assert not at.exception
    alert = next(q for page, label, q in seen[-2:] if page == "views/alerts.py")
    assert alert == {"route": "ATH-FRA", "cabin": "economy", "unit": "eur", "price": "60", "depart": d10.isoformat(), "trip": "oneway",
                     "nights": "2-7", "when": when}  # no way back, so no back
    t.execute("INSERT INTO award_miles(day, orig, dest, cabin, miles, nonstop, fetched, taxes) VALUES(?,?,?,?,?,?,?,?)",
              (d10.isoformat(), "ATH", "FRA", "business", 25000, 1, datetime.now().isoformat(), None))
    t.commit()
    at = open_page("overview.py").run()
    at.button_group(key="ov-grid-pay").set_value("miles").run()
    at.button_group(key="ov-grid-cabin").set_value("business").run()
    assert not at.exception
    alert = next(q for page, label, q in seen[-2:] if page == "views/alerts.py")
    assert alert["unit"] == "miles" and alert["price"] == "25000" and alert["cabin"] == "business" and alert["trip"] == "oneway"


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
    assert re.search(r'<span class="good">Good price</span> Trips like this usually cost about €3\d\d\.</span>', text)
    assert "Tracking since" not in text and "lowest of" not in text and "typical" not in text.split("<style>")[0]
    assert "Cheapest return · Athens → Frankfurt" in text and "one adult, leaving in the next 30 days" in text
    cheap_day = f"{date.today() + timedelta(10):%a}"  # the €60 outbound
    other = [d for d in DAYS if d != cheap_day][:2]
    at.button_group(key="ov-days").set_value(other).run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    leave = re.search(r"<dt>Leave</dt><dd>(\w{3})", text).group(1)
    assert leave in other and "Good price" not in text  # the pass fits the days asked for; without the €60 day it is no bargain
    assert all(i[:3] in other for i in grid(at).index)
    at.slider(key="ov-nights").set_value((2, 3)).run()
    assert not at.exception and list(grid(at).columns) == ["2 nights", "3 nights"]
    assert re.search(r"<dt>Stay</dt><dd>[23] nights", " ".join(m.value for m in at.markdown))
    at.slider(key="ov-nights").set_value((14, 16)).run()  # a holiday, not a city break: any stay up to 60 nights
    assert not at.exception and list(grid(at).columns) == ["14 nights", "15 nights", "16 nights"]
    assert re.search(r"<dt>Stay</dt><dd>1[456] nights", " ".join(m.value for m in at.markdown))
    at.button_group(key="ov-trip").set_value("oneway").run()  # one way: no way back priced, the dates are a list of days
    assert not at.exception and not at.slider  # the nights control goes
    text = " ".join(m.value for m in at.markdown)
    assert "Cheapest one way · Athens → Frankfurt" in text and "<dt>Trip</dt><dd>One way</dd>" in text and "<dt>Stay</dt>" not in text
    assert "Pick your day" in text and list(grid(at).columns) == ["One way"]
    assert all(i[:3] in other for i in grid(at).index)  # the weekday filter still applies


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
    assert at.date_input(key="ov-when").value == (today + timedelta(1), today + timedelta(30))  # the next 30 days by default
    at.date_input(key="ov-when").set_value((m, nxt - timedelta(1))).run()  # that whole month, picked as two dates
    assert not at.exception
    text = " ".join(x.value for x in at.markdown)
    assert "€100" in text and f"leaving in 1–{(nxt - timedelta(1)).day} {m:%b}" in text and re.search(rf"<dt>Leave</dt><dd>\w{{3}} 0\d {m:%b}", text)
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
    assert to.options[:3] == ["Anywhere", "Frankfurt (FRA)", "Zurich (ZRH)"] and "Bilbao (BIO) · not searched yet" in to.options
    assert "Athens (ATH)" not in " ".join(to.options) and to.value == "FRA"  # not itself; the first saved route by default
    to.set_value("BIO").run()
    assert not at.exception and "No economy fares saved from Athens to Bilbao for the next 30 days yet" in at.info[0].value
    key = f"fetch-ahead|ATH|BIO|{today + timedelta(1)}"
    assert at.button(key=key).label == "Search this route on GitHub" and grid(at).empty and routes(at)  # no pass, no grid; the route table stays
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
    text = " ".join(m.value for m in at.markdown)
    assert "When to fly: cheapest weekdays and weeks" in [x.label for x in at.expander]  # folded under the dates
    text = " ".join(m.value for m in at.markdown)
    assert re.search(r"is the cheapest day to fly Athens → Frankfurt|No strong weekday pattern on Athens → Frankfurt", text)
    assert "next 2 weeks" in text or "Not enough weeks saved yet" in text
    captions = " ".join(c.value for c in at.caption)
    assert "The average economy fare by the weekday you leave, over the 30 days" in captions
    at.button_group(key="ov-grid-cabin").set_value("business").run()
    assert not at.exception and "The average business fare by the weekday" in " ".join(c.value for c in at.caption)
    at.button_group(key="ov-grid-pay").set_value("miles").run()
    assert not at.exception and "When to fly: cheapest weekdays and weeks" not in [x.label for x in at.expander]  # miles have no such pattern to show


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
    assert grid(at).loc[f"{pd.Timestamp(d(5)):%a %d %b}", "3 nights"] == "19,000"  # 9,000 out + 10,000 back three nights later
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

    at = open_page("overview.py").run()
    assert not at.exception and "BIO → ATH" in [r[0] for r in routes(at)]
    at.selectbox(key="ov-grid-orig").set_value("BIO").run()
    assert not at.exception and 'pass-codes">BIO<span class="line"></span>ATH' in " ".join(m.value for m in at.markdown)
    assert at.selectbox(key="ov-grid-dest-BIO").value == "ATH"  # the first saved destination from there, not Anywhere

    seen = []
    monkeypatch.setattr(ex, "chat", lambda history, facts, cfg: (seen.append((history, facts)), iter(["Answer"]))[1])
    monkeypatch.setattr(ex, "briefing", lambda f, cfg, day: (" ".join(ex.sentences(f)), None))  # the plain sentences, not the fake model
    at = open_page("expert.py")
    at.query_params["from"], at.query_params["to"] = "BIO", "ATH"  # the Overview's "Ask the expert about it" link
    at.run()
    assert not at.exception and at.selectbox(key="plan-orig").value == "BIO"
    text = " ".join(m.value for m in at.markdown)
    assert "The cheapest economy return from Bilbao to Athens with these filters is €184: leave " in text  # €91 out on day 1, €93 back on day 3
    at.chat_input[0].set_value("Why?").run()
    assert not at.exception and "Search: Bilbao (BIO) to Athens (ATH)" in seen[-1][1] and "2 nights, €184." in seen[-1][1]


def test_to_list_says_where_aegean_was_found_not_to_fly_from_here(tmp_path, monkeypatch):
    from datetime import datetime
    t = seed(tmp_path / "tracker.db")
    now = datetime.now().isoformat()
    for i in range(1, 6):  # Athens -> Erfurt searched: every day checked, no AEGEAN flight
        t.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)", ((date.today() + timedelta(i)).isoformat(), "ATH", "ERF", "economy", None, "calendar", now, 1))
    t.commit()
    at = pages(tmp_path, monkeypatch, None)("overview.py").run()
    assert not at.exception
    to = at.selectbox(key="ov-grid-dest-ATH")
    assert to.options[-1] == "Erfurt (ERF) · no AEGEAN flight from here found · seasonal"  # last, and seasonal by AEGEAN's own list
    assert "Bilbao (BIO) · not searched yet" in to.options


def test_seasonal_destinations_say_so(tmp_path, monkeypatch):
    seed(tmp_path / "tracker.db")
    at = pages(tmp_path, monkeypatch, None)("overview.py").run()
    assert not at.exception
    assert "Innsbruck (INN) · not searched yet · seasonal" in at.selectbox(key="ov-grid-dest-ATH").options
