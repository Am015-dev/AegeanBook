from datetime import date

import network


def test_a_pair_is_no_flight_only_when_every_window_answered_with_none(tmp_path):
    today = date(2026, 11, 2)
    asked = []
    fake = lambda o, d, s, e: asked.append((o, d, s)) or ({"2027-06-21": 80} if d == "FRA" else {})
    assert network.check("A3", "ATH", "FRA", today, fake, pause=0)["flies"] == 1  # found next summer
    assert network.check("A3", "ATH", "ERF", today, fake, pause=0)["flies"] == 0  # both windows empty
    def down(o, d, s, e):
        raise RuntimeError("HTTP 429")
    assert network.check("A3", "ATH", "MUC", today, down, pause=0) is None  # no answer: nothing saved, asked again next run
    out = tmp_path / "network.csv"
    network.save([network.check("A3", "ATH", "ERF", today, fake, pause=0)], out)
    network.save([network.check("A3", "ATH", "FRA", today, fake, pause=0)], out)
    net = network.load(out)
    assert network.flies(net, "A3", "FRA", "ATH") is True and network.flies(net, "A3", "ERF", "ATH") is False
    assert network.flies(net, "A3", "ATH", "MUC") is None  # not checked yet: offered as before
    assert network.flies(net, "A3", "FRA", "IST", home={"ATH", "SKG"}) is False  # two foreign airports: never flown


def test_only_pairs_touching_greece_or_cyprus_are_checked():
    ps = network.pairs("A3")
    assert ("ATH", "FRA") in ps and ("FRA", "IST") not in ps and all(a < b for a, b in ps)
