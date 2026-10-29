"""Offline tests for the request-based award collector: parsing a real (sanitised) AEGEAN flight response, building the per-day request
body and refusing anything that is not data."""
import json
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
