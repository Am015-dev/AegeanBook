from datetime import date

import pandas as pd

import mix

S = lambda d: pd.Series(d, dtype=float)


def test_mix_wins_and_names_each_leg():
    a3_out, a3_back = S({"2026-11-10": 100}), S({"2026-11-14": 60})
    gq_out, gq_back = S({"2026-11-10": 45}), S({"2026-11-14": 90})
    m = mix.cheapest_mix(a3_out, a3_back, gq_out, gq_back, range(2, 8))
    assert (m["out_airline"], m["back_airline"]) == ("Sky Express", "AEGEAN")
    assert (m["depart"], m["back"], m["total"], m["saving"]) == (date(2026, 11, 10), date(2026, 11, 14), 105, 30)
    assert "= €210, €60 less" in mix.sentence(m, pax=2)


def test_no_mix_when_a_single_airline_is_as_cheap():
    a3 = S({"2026-11-10": 40}), S({"2026-11-14": 40})
    gq = S({"2026-11-10": 45}), S({"2026-11-14": 90})
    assert mix.cheapest_mix(*a3, *gq, range(2, 8)) is None


def test_stay_length_and_weekdays_bound_the_mix():
    a3_out, a3_back = S({"2026-11-10": 100}), S({"2026-11-14": 60})
    gq_out, gq_back = S({"2026-11-10": 45}), S({"2026-11-14": 90})
    assert mix.cheapest_mix(a3_out, a3_back, gq_out, gq_back, range(5, 8)) is None  # a 4-night stay is outside 5-7
    assert mix.cheapest_mix(a3_out, a3_back, gq_out, gq_back, range(2, 8), ["Fri"]) is None  # 10 Nov is a Tuesday
    assert mix.cheapest_mix(a3_out, None, gq_out, None, range(2, 8)) is None
