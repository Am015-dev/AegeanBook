from hotels import best_value


def test_best_value_picks_rating_per_euro_and_skips_unrated():
    nan = float("nan")
    assert best_value([4.5, 4.0, 5.0, nan], [300, 100, 200, 50], [500, 400, 3, 900]) == 1  # 5.0 has 3 reviews; nan rating skipped
    assert best_value([3.5], [50]) is None


def test_sold_out_nights_are_the_ones_far_above_the_hotels_median():
    import pandas as pd
    from hotels import sold_out
    p = pd.Series([150, 160, 170, 3348, None, 420], index=list("abcdef"))  # median 170: only 3348 is above 2.5 × 170 = 425
    assert list(sold_out(p)[sold_out(p)].index) == ["d"]
    assert not sold_out(pd.Series([100, 240, 110])).any()  # 240 < 2.5 × 110


def test_small_room_needs_no_stars_and_a_price_under_half_the_area():
    from hotels import small_room
    nan = float("nan")
    assert small_room([nan, 4, "", nan, None], [35, 35, 90, 35, 35], [160, 160, 160, nan, 160]) == [True, False, False, False, True]


def test_checked_on_says_the_day_or_the_span():
    from hotels import basis, checked_on
    assert checked_on(["2026-11-02T22:15:17", "2026-11-02T10:00:00"]) == "2 Nov"
    assert checked_on(["2026-11-02T22:15:17", "2026-11-05T10:00:00"]) == "2–5 Nov"
    assert checked_on(["2026-10-30T22:15:17", "2026-11-05T10:00:00"]) == "30 Oct–5 Nov"
    assert checked_on([]) == ""
    assert basis(2) == "a night for the room, 2 adults, before city tax" and basis(1).endswith("1 adult, before city tax")


def test_a_well_rated_hotel_without_stars_is_not_called_a_shared_room():
    from math import nan
    from hotels import small_room
    assert small_room([nan, nan], [35, 35], [160, 160], [4.5, 3.1], [900, 104]) == [False, True]
