"""Price alerts, offline: a temporary data folder and a fake `gh`."""
import json
import os
from datetime import date, timedelta

import alerts


def day(i):
    return (date.today() + timedelta(i)).isoformat()


def write(folder, name, header, rows):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        f.write(header + "\n" + "\n".join(rows) + "\n")


def test_alert_from_the_app_link_is_read_back(monkeypatch):
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(alerts.new_issue_link("ATH", "ZRH", "business", "eur", 250, "2026-12-01", "2026-12-31")).query)
    assert q["labels"] == ["price-alert"] and q["title"] == ["Alert: ATH → ZRH business below €250"]
    assert alerts.parse(q["body"][0]) == dict(orig="ATH", dest="ZRH", cabin="business", unit="eur", limit=250.0,
                                              start="2026-12-01", end="2026-12-31")
    assert alerts.parse("route: ath → fra\ncabin: Economy\nmiles below: 9,500")["unit"] == "miles"  # hand-written works too
    assert alerts.parse("route: ATH-ZRH\ncabin: first\nbelow: 100") is None  # no such cabin


def test_matches_comment_once_then_only_when_cheaper(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "DATA", str(tmp_path))
    write(tmp_path, "daily.csv", "day,orig,dest,cabin,price,source,fetched,nonstop",
          [f"{day(3)},ATH,ZRH,business,240,calendar,x,1", f"{day(4)},ATH,ZRH,business,260,calendar,x,1",
           f"{day(5)},ATH,ZRH,business,230,calendar,x,0",  # not nonstop: ignored
           f"{day(-1)},ATH,ZRH,business,100,calendar,x,1",  # in the past: ignored
           f"{day(6)},ATH,ZRH,economy,90,calendar,x,1"])
    a = alerts.parse("route: ATH-ZRH\ncabin: business\nbelow: 250")
    hits = alerts.matches(a)
    assert hits == [(day(3), 240.0)]
    first = alerts.message(a, hits, None)
    assert "is now €240" in first and "below your limit of €250" in first and "<!-- alert best: 240.0 -->" in first
    assert alerts.message(a, hits, 240.0) is None  # the same news is not told twice
    assert "cheaper than the €245" in alerts.message(a, hits, 245.0)
    assert alerts.matches(dict(a, start=day(4))) == []  # date range respected


def test_run_comments_on_the_alerts_that_are_met(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "DATA", str(tmp_path))
    write(tmp_path, "daily.csv", "day,orig,dest,cabin,price,source,fetched,nonstop", [f"{day(3)},ATH,FRA,economy,61,calendar,x,1"])
    write(tmp_path, "daily_award.csv", "day,orig,dest,cabin,miles,nonstop,fetched", [f"{day(3)},ATH,FRA,business,16935,1,x"])
    issues = [dict(number=1, title="a", body="route: ATH-FRA\ncabin: economy\nbelow: 70", comments=[]),
              dict(number=2, title="b", body="route: ATH-FRA\ncabin: economy\nbelow: 70",
                   comments=[{"body": "old news <!-- alert best: 61.0 -->"}]),
              dict(number=3, title="c", body="route: ATH-FRA\ncabin: business\nmiles below: 20000", comments=[]),
              dict(number=4, title="d", body="just a note", comments=[])]
    calls = []

    def fake(*args):
        calls.append(args)
        return json.dumps(issues) if args[:2] == ("issue", "list") else ""
    monkeypatch.setattr(alerts, "gh", fake)
    alerts.run()
    commented = [a[2] for a in calls if a[:2] == ("issue", "comment")]
    assert commented == ["1", "3"]  # #2 was already told €61, #4 is not an alert
    assert "16,935 miles" in [a for a in calls if a[:3] == ("issue", "comment", "3")][0][4]
