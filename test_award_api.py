"""Offline tests for the request-based award collector: parsing a real (sanitised) AEGEAN flight response, building the per-day request
body and refusing anything that is not data."""
import csv
import json
from datetime import datetime, timezone
import os
from datetime import date
from urllib.parse import parse_qs

import pytest

import award_api as api

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests_fixtures", "award_flights.json")


def load():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def test_parse_real_response():
    fl = api.parse_flights(load())
    assert len(fl) == 30
    a3830 = next(f for f in fl if f["flight"] == "A3830")
    assert a3830["stops"] == 0 and a3830["aegean"]
    # the response prices A3830 as FLEX 9,032 / COMFORTFLE 11,290 / ECONOMY 15,000 and BUSINESS 16,935 / BUSINESSSA 25,000; the site's
    # Economy and Business tiles show the cheapest of each cabin's families
    assert a3830["fares"]["economy"][0] == 9032 and a3830["fares"]["business"][0] == 16935
    assert a3830["fares"]["economy"][1] == pytest.approx(30.44)  # cash taxes in EUR come with the miles
    assert any(not f["fares"] for f in fl)  # flights without award seats come back empty, not missing
    assert not next(f for f in fl if f["flight"] == "A3802 LH099")["aegean"]  # a codeshare is not an AEGEAN-only flight


def test_best_by_cabin_counts_only_aegean_and_separates_nonstop():
    fl = [dict(flight="LH1", stops=0, aegean=False, fares={"economy": (1000, 1)}),
          dict(flight="A31", stops=1, aegean=True, fares={"economy": (9000, 1), "business": (20000, 1)}),
          dict(flight="A32", stops=0, aegean=True, fares={"economy": (12000, 1)})]
    assert api.best_by_cabin(fl) == {("economy", 0): 9000, ("economy", 1): 12000, ("business", 0): 20000, ("business", 1): None}


TEMPLATE = "FORCE_OVERRIDE=true&LANGUAGE=GB&data=" + json.dumps({"B_DATE_1": "202611150000", "PAGE_TICKET": "1", "KEEP": "x y"}).replace(" ", "%20")


def test_build_body_changes_only_date_and_counter():
    body = api.build_body(TEMPLATE, date(2026, 12, 3), 7)
    form = {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}
    assert form["FORCE_OVERRIDE"] == "true" and form["LANGUAGE"] == "GB"
    assert json.loads(form["data"]) == {"B_DATE_1": "202612030000", "PAGE_TICKET": "7", "KEEP": "x y"}
    assert api.template_day(TEMPLATE) == "20261115"


class FakePage:
    def __init__(self, reply):
        self.reply, self.sent = reply, []

    def evaluate(self, js, arg):
        self.sent.append(arg)
        return self.reply


def test_post_day_refuses_anything_but_json():
    ok = FakePage(dict(status=200, ctype="application/json;charset=UTF-8", text='{"a": 1}'))
    assert api.post_day(ok, "https://x/y.json", TEMPLATE, date(2026, 12, 3), 2) == {"a": 1}
    for bad in (dict(status=200, ctype="text/html", text="<html>Additional security check</html>"), dict(status=403, ctype="application/json", text="{}")):
        with pytest.raises(api.Blocked):
            api.post_day(FakePage(bad), "https://x/y.json", TEMPLATE, date(2026, 12, 3), 2)


def test_numeric_date_and_counter_keep_their_type():
    """The real payload stores B_DATE_1 and PAGE_TICKET as numbers; sending text could be refused or misread."""
    tpl = "LANGUAGE=GB&data=" + json.dumps({"B_DATE_1": 202611150000, "PAGE_TICKET": 3})
    assert api.template_day(tpl) == "20261115"
    form = {k: v[0] for k, v in parse_qs(api.build_body(tpl, date(2026, 12, 3), 4)).items()}
    assert json.loads(form["data"]) == {"B_DATE_1": 202612030000, "PAGE_TICKET": 4}


def test_calendar_fares_reads_each_days_cheapest_fare():
    import os
    resp = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests_fixtures", "award_flights.json"), encoding="utf-8"))
    fares = api.calendar_fares(resp)
    assert len(fares) == 7 and fares["2026-11-21"] == "FLEX 9032 30.44" and fares["2026-11-22"] == "ECONOMY 15000 36.92"
    assert api.calendar_fares({"bom": {"modelObject": {"availabilities": {}}}}) == {}   # a per-day answer without the calendar part


def test_a_calendar_day_we_cannot_read_is_not_no_seats():
    resp = {"bom": {"modelObject": {"availabilities": {"calendar": {"bounds": [{"calendarDates": [{"date": 0, "calendarAssociation": {"listRecos": []}}]}]}}}}}
    with pytest.raises(ValueError):
        api.calendar_fares(resp)


def test_days_to_check_takes_changes_new_days_and_the_oldest():
    fares = {d: "F" for d in "abcdef"}
    fares["b"] = "changed"
    seen = {"a": ("F", "t3"), "b": ("F", "t3"), "c": ("", "t1"), "d": ("F", "t2"), "e": ("F", "t4")}   # f is new, c predates the calendar column
    assert api.days_to_check(list("abcdef"), seen, fares, refresh=2) == ["b", "c", "d", "f"]
    assert api.days_to_check(list("ae"), seen, fares, refresh=0) == []


def test_build_body_can_change_the_route():
    body = api.build_body(TEMPLATE, date(2026, 12, 3), 7, "FRA", "ATH")
    data = json.loads(parse_qs(body, keep_blank_values=True)["data"][0])
    assert data["B_LOCATION_1"] == "FRA" and data["E_LOCATION_1"] == "ATH" and data["KEEP"] == "x y"


def _day_answer(days):
    """A day's answer: its calendar, {iso: miles or None}. The flights are empty, parse_flights is not what is tested here."""
    stamp = lambda iso: int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000)
    price = lambda m: {"recommendationPrice": {"price": {"totalPrice": {"milesAmount": {"amount": m}}, "totalTaxes": {"cashAmount": {"amount": 30.44}}}}, "fareFamily": "FLEX"}
    dates = [{"date": stamp(d), "calendarAssociation": {"recommendation": price(m)} if m else {}} for d, m in days.items()]
    return {"bom": {"modelObject": {"availabilities": {"calendar": {"bounds": [{"calendarDates": dates}]},
                                                       "upsell": {"bounds": [{"flights": [], "recommendations": {}, "associations": {}}]}}}}}


def _round_trip(there, back, flights=()):
    """A round-trip answer: each direction's calendar and flight list; `flights` holds (direction, miles, cabin code) of AEGEAN nonstops."""
    cal = [_day_answer(d)["bom"]["modelObject"]["availabilities"]["calendar"]["bounds"][0] for d in (there, back)]
    bounds = []
    for i in (0, 1):
        mine = [f for f in flights if f[0] == i]
        bounds.append({"flights": [{"id": n, "segments": [{"flightIdentifier": {"marketingAirline": "A3", "flightNumber": n}}]} for n, _ in enumerate(mine)],
                       "recommendations": {str(n): {"recommendationPrice": {"price": {"totalPrice": {"milesAmount": {"amount": f[1]}}, "totalTaxes": {"cashAmount": {"amount": 30.44}}}}}
                                           for n, f in enumerate(mine)},
                       "associations": {str(n): {"recoId": n, "boundAssociations": [{"fareFamily": f[2], "flightId": n}]} for n, f in enumerate(mine)}})
    return {"bom": {"modelObject": {"availabilities": {"calendar": {"bounds": cal}, "upsell": {"bounds": bounds}}}}}


def test_round_trip_body_keeps_the_type_of_the_date():
    data = json.loads(parse_qs(api.build_body(TEMPLATE, date(2026, 12, 3), 7, "FRA", "ATH", date(2026, 12, 9)), keep_blank_values=True)["data"][0])
    assert (data["TRIP_TYPE"], data["B_LOCATION_2"], data["E_LOCATION_2"]) == ("R", "ATH", "FRA") and type(data["B_DATE_2"]) is type(data["B_DATE_1"])
    assert str(data["B_DATE_2"]).startswith("20261209")


def test_a_round_trip_answer_priced_too_high_or_without_fares_is_not_trusted():
    good = _round_trip({}, {}, [(0, 9032, "FLEX"), (0, 16935, "BUSINESS"), (1, 25000, "BUSINESS")])
    low = {"economy": 9032, "business": 16935}
    assert api.trusted_best(good, 0, low)[("business", 1)] == (16935, 30.44)
    assert api.trusted_best(good, 1, low) is None
    del good["bom"]["modelObject"]["availabilities"]["upsell"]["bounds"][0]["recommendations"]
    assert api.trusted_best(good, 0, low) is None


def test_days_beyond_the_fill_get_no_row_at_all(tmp_path, monkeypatch):
    def post(page, url, tpl, day, ticket, orig, dest, back):
        cal = {f"2026-11-{n:02}": 9032 for n in range(1, 8)}
        return _round_trip(cal, cal, [(0, 9032, "FLEX"), (1, 9032, "FLEX")])

    ac = _leg_setup(tmp_path, monkeypatch, post)
    monkeypatch.setattr(ac, "lowest_miles", lambda *a: {"economy": 9032})
    monkeypatch.setattr(ac, "FILL", 1)
    rows = list(ac.collect_pair_api(None, dict(url="u", template="t", ticket=0), "ATH", "FRA", date(2026, 11, 1), date(2026, 11, 7), lambda m: None))
    assert {r[2] for r in rows if r[0] == "ATH"} == {"2026-11-04", "2026-11-01"}                       # the map and one fill: nothing guessed


def test_a_full_run_asks_about_every_day_with_seats_and_trusts_no_stored_row(tmp_path, monkeypatch):
    calls = []

    def post(page, url, tpl, day, ticket, orig, dest, back):
        calls.append((day.isoformat(), back and back.isoformat()))
        return _round_trip({}, {}, [(0, 9032, "FLEX"), (1, 9032, "FLEX")])

    ac = _leg_setup(tmp_path, monkeypatch, post)
    monkeypatch.setattr(ac, "last_checked", lambda *a: pytest.fail("a full run must not read stored rows"))
    cal = {"ATH-FRA": {"2026-11-01": "FLEX 9032 30.44", "2026-11-02": "-", "2026-11-03": "FLEX 9032 30.44"}, "FRA-ATH": {"2026-11-01": "-", "2026-11-02": "FLEX 9032 30.44"}}
    rows = list(ac.collect_pair_api(None, dict(url="u", template="t", ticket=0), "ATH", "FRA", date(2026, 11, 1), date(2026, 11, 3), lambda m: None,
                                    calendars=cal, finished={("ATH", "FRA"): {"2026-11-03"}}))
    assert calls == [("2026-11-01", "2026-11-02"), ("2026-11-03", None)]        # ATH-FRA 11-03 is done; FRA-ATH 11-03 has no calendar entry, so it is asked about
    empty = [r for r in rows if r[3] and all(v is None for v in r[3].values()) and len(r[3]) == 2]
    assert {(r[0], r[2]) for r in empty} == {("ATH", "2026-11-02"), ("FRA", "2026-11-01")}                    # no direct seats says nothing about connections
    assert all(k[1] == 1 for r in empty for k in r[3])


def test_a_run_goes_on_where_the_last_runs_stopped(tmp_path):
    import award_collector as ac
    row = lambda day: dict(day=day, orig="ATH", dest="FRA", cabin="economy", miles=9032, nonstop=1, fetched="t")
    ac.save_csv([row("2026-12-01")], str(tmp_path / "2026-11-03.csv"), date(2026, 11, 1))
    ac.save_csv([row("2026-12-02")], str(tmp_path / "2026-11-02.csv"), date(2026, 11, 1))
    ac.save_csv([row("2026-12-03")], str(tmp_path / "2026-10-20.csv"), date(2026, 10, 1))    # too old: asked again
    assert ac.days_in_cycle(date(2026, 11, 3), str(tmp_path)) == {("ATH", "FRA"): {"2026-12-01", "2026-12-02"}}


def test_a_run_file_tells_which_days_are_done(tmp_path):
    import award_collector as ac
    p = tmp_path / "run.csv"
    ac.save_csv([dict(day="2026-11-05", orig="ATH", dest="FRA", cabin="economy", miles=9032, nonstop=1, fetched="t")], str(p), date(2026, 11, 1))
    assert ac.days_in_run(str(p)) == {("ATH", "FRA"): {"2026-11-05"}} and ac.days_in_run(str(tmp_path / "no.csv")) == {}


def test_pair_days_never_returns_before_it_leaves():
    assert api.pair_days(["d3", "d1", "d9"], ["d2", "d0", "d4"]) == ([("d1", "d2"), ("d3", "d4"), ("d9", None)], ["d0"])


def _leg_setup(tmp_path, monkeypatch, post):
    import award_collector as ac
    for fn in (ac.last_checked, ac.lowest_miles):
        monkeypatch.setattr(fn, "__defaults__", (str(tmp_path / "none.csv"),))
    monkeypatch.setattr(ac, "pause", lambda *a: None)
    monkeypatch.setattr(ac, "post_day", post)
    monkeypatch.setattr(ac, "_extend_session", lambda *a: None)
    return ac


def test_a_pair_never_calls_a_day_empty_unless_the_calendar_said_so(tmp_path, monkeypatch):
    seen = []

    def post(page, url, tpl, day, ticket, orig, dest, back):
        seen.append((day.isoformat(), back and back.isoformat()))
        cal = {"2026-11-02": None, "2026-11-03": 9032, "2026-11-04": 9032}                    # the calendar covers 3 days, not the fourth
        return _round_trip(cal, cal)

    ac = _leg_setup(tmp_path, monkeypatch, post)
    stamps = {}
    rows = list(ac.collect_pair_api(None, dict(url="u", template="t", ticket=0), "ATH", "ZRH", date(2026, 11, 1), date(2026, 11, 4), lambda m: None, stamps))
    got = {(r[0], r[2]): r[3] for r in rows}
    assert seen == [("2026-11-04", "2026-11-04"), ("2026-11-01", "2026-11-01"), ("2026-11-03", "2026-11-03")]    # two days per call
    assert got[("ATH", "2026-11-02")] == ac.NONE and stamps[("2026-11-02", "ATH", "ZRH")] == "-"                  # empty: no request spent
    assert got[("ZRH", "2026-11-02")] == ac.NONE and len(rows) == 8


def test_a_rejected_direction_is_asked_again_one_way(tmp_path, monkeypatch):
    seen = []

    def post(page, url, tpl, day, ticket, orig, dest, back):
        seen.append((day.isoformat(), orig, back))
        cal = {"2026-11-01": 9032}
        return _round_trip(cal, cal, [(0, 16935, "BUSINESS"), (1, 25000, "BUSINESS")]) if back else _round_trip(cal, cal, [(0, 16935, "BUSINESS")])

    ac = _leg_setup(tmp_path, monkeypatch, post)
    monkeypatch.setattr(ac, "lowest_miles", lambda *a: {"business": 16935})
    rows = list(ac.collect_pair_api(None, dict(url="u", template="t", ticket=0), "ATH", "FRA", date(2026, 11, 1), date(2026, 11, 1), lambda m: None))
    assert [(r[0], r[3][("business", 1)]) for r in rows] == [("ATH", (16935, 30.44)), ("FRA", (16935, 30.44))]
    assert seen == [("2026-11-01", "ATH", date(2026, 11, 1)), ("2026-11-01", "FRA", None)]                         # one extra call, one-way, back only


def test_a_closed_browser_ends_the_run_instead_of_failing_every_day(tmp_path, monkeypatch):
    ac = _leg_setup(tmp_path, monkeypatch, lambda *a: pytest.fail("no request after the browser closed"))

    def closed(*a):
        raise RuntimeError("Target page, context or browser has been closed")

    monkeypatch.setattr(ac, "pause", closed)
    with pytest.raises(RuntimeError):
        list(ac.collect_pair_api(None, dict(url="u", template="t", ticket=0), "ATH", "FRA", date(2026, 11, 1), date(2026, 11, 8), lambda m: None))


def test_an_answer_without_fares_is_no_seats_only_when_aegean_says_so():
    resp = _day_answer({"2026-11-01": None})
    resp["bom"]["modelObject"]["availabilities"]["upsell"]["bounds"] = [{"someOtherKey": 1}]
    with pytest.raises(KeyError):
        api.parse_flights(resp)
    resp["bom"]["modelObject"]["messages"] = [{"code": "7124"}]
    assert api.best_with_taxes(api.parse_flights(resp)) == {(c, n): None for c in ("economy", "business") for n in (0, 1)}


def test_a_fare_family_we_do_not_know_is_not_silently_dropped():
    resp = _round_trip({}, {}, [(0, 9032, "NEWFARE")])
    with pytest.raises(KeyError):
        api.parse_flights(resp)


def test_a_day_with_no_seats_message_is_no_seats_not_an_error():
    resp = _day_answer({"2026-11-01": None})
    resp["bom"]["modelObject"]["messages"] = [{"code": "7124"}]
    resp["bom"]["modelObject"]["availabilities"]["upsell"]["bounds"] = [{"someOtherKey": 1}]  # no flights, no recommendations
    assert api.best_with_taxes(api.parse_flights(resp)) == {(c, n): None for c in ("economy", "business") for n in (0, 1)}


def test_the_session_is_extended_only_when_the_page_asks():
    import award_collector as ac

    class Button:
        first = property(lambda self: self)

        def __init__(self, shown):
            self.shown, self.clicks = shown, 0

        def count(self):
            return self.shown

        def is_visible(self):
            return True

        def click(self, timeout):
            self.clicks += 1

    class Page:
        def __init__(self, button):
            self.button = button

        def get_by_text(self, text, exact):
            return self.button

    asked, quiet, said = Button(1), Button(0), []
    ac._extend_session(Page(asked), said.append)
    ac._extend_session(Page(quiet), said.append)
    assert asked.clicks == 1 and quiet.clicks == 0 and said == ["SESSION extended"]


def test_the_run_waits_for_a_login_in_the_open_window(monkeypatch):
    import award_collector as ac

    class Page:
        url, forms, waited = "https://en.aegeanair.com/member/login/", 0, None

        def __init__(self):
            self.visits = []

        def wait_for_selector(self, sel, timeout):
            self.forms += 1
            if self.forms == 1:
                raise ac.PWTimeout("no form")

        def wait_for_url(self, ok, timeout):
            self.waited = timeout
            assert not ok(self.url) and ok("https://en.aegeanair.com/milesandbonus/")
            self.url = "https://en.aegeanair.com/milesandbonus/"

        def goto(self, url, referer=None):
            self.visits.append(url)

    page, said = Page(), []
    monkeypatch.setattr(ac, "pause", lambda *a: None)
    ac._wait_for_form(page, said.append, patience=600)
    assert page.forms == 2 and page.waited == 600000 and "LOGIN" in said[0] and page.visits == [ac.HOME_URL, ac.SEARCH_URL]
    page = Page()
    page.url = "https://en.aegeanair.com/somewhere/"
    with pytest.raises(ac.PWTimeout):  # not a login page: a real failure
        ac._wait_for_form(page, said.append)


def test_a_broken_session_stops_the_run():
    page = FakePage({"status": 200, "ctype": "application/json", "text": json.dumps({"bom": {"modelObject": {"messages": [{"code": "3010"}]}}})})
    with pytest.raises(api.Blocked):
        api.post_day(page, "u", TEMPLATE, date(2026, 12, 3), 7)
