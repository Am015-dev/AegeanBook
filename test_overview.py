"""Overview page and virtual expert, offline: a seeded database and a fake Claude client."""
import os
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

import db
import expert
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


def test_expert_streams_uses_haiku_and_has_a_daily_cap(monkeypatch):
    monkeypatch.setattr(expert, "_used", {})
    seen = {}
    assert "".join(expert.ask("When?", "day,leg\n...", "k", fake_client(["Fly ", "Thursday."], seen=seen))) == "Fly Thursday."
    assert seen["model"] == "claude-haiku-5-5" and "<data>" in seen["messages"][0]["content"]
    with pytest.raises(RuntimeError, match="declined"):
        list(expert.ask("x", "", "k", fake_client([], stop="refusal")))
    monkeypatch.setattr(expert, "_used", {date.today(): expert.MAX_PER_DAY})
    with pytest.raises(RuntimeError, match="daily limit"):
        list(expert.ask("x", "", "k", fake_client(["never"])))


def test_overview_page_empty_filled_and_with_expert(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    page = os.path.join(HERE, "views/overview.py")
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception and "No fares are saved yet" in at.info[0].value
    seed(tmp_path / "tracker.db")
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    assert "€60" in html and "Cheapest whole trip to Frankfurt" in html and not at.button  # no key: no Ask button
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(expert, "ask", lambda q, f, k: iter(["Thursday ", "is cheapest."]))
    at = AppTest.from_file(page, default_timeout=60).run()
    at.text_area[0].input("When is it cheapest?")
    at.button[0].click().run()
    assert not at.exception and "Thursday is cheapest." in " ".join(m.value for m in at.markdown)
