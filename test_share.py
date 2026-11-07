from datetime import date, datetime, timezone

import share

TODAY = date(2026, 11, 7)
ARGS = (["ATH", "SKG"], {"HER", "JTR"}, TODAY, 330)


def test_roundtrip():
    q = share.build_search("ATH", "JTR", (date(2026, 11, 20), date(2026, 12, 10)), "return", (2, 7), "business", "miles", 2)
    assert q["when"] == "2026-11-20..2026-12-10"
    assert share.parse_search(q, *ARGS) == {"from": "ATH", "to": "JTR", "when": (date(2026, 11, 20), date(2026, 12, 10)),
                                            "trip": "return", "nights": (2, 7), "cabin": "business", "pay": "miles", "pax": 2}


def test_single_day_and_any():
    q = share.build_search("ATH", "ANY", (date(2026, 11, 20),) * 2, "oneway", (2, 7), "economy", "cash", 1)
    assert q["when"] == "2026-11-20" and "nights" not in q
    got = share.parse_search(q, *ARGS)
    assert got["when"] == (date(2026, 11, 20),) * 2 and got["to"] == "ANY"


def test_garbage_ignored():
    bad = {"from": "XXX", "to": "<script>", "when": "2020-01-01..2026-12-01", "trip": "x", "nights": "9-2", "cabin": "first",
           "pay": "gold", "pax": "99"}
    assert share.parse_search(bad, *ARGS) == {}
    assert share.parse_search({"when": "nope..2026-12-01", "nights": "a-b", "pax": "-1", "from": ["ATH"]}, *ARGS) == {}
    assert share.parse_search({"when": "2026-12-10..2026-11-20"}, *ARGS) == {}  # backwards
    assert share.parse_search({}, *ARGS) == {}


def test_ics():
    s = share.ics([(date(2026, 11, 20), "Flight ATH → FRA (AEGEAN, from €214)", "Book: https://a.b/?x=1,2;y")],
                  now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert s.startswith("BEGIN:VCALENDAR\r\n") and s.endswith("END:VCALENDAR\r\n")
    assert "DTSTART;VALUE=DATE:20261120" in s and "DTEND;VALUE=DATE:20261121" in s
    assert "SUMMARY:Flight ATH → FRA (AEGEAN\\, from €214)" in s and "x=1\\,2\\;y" in s
