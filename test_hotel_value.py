from hotels import best_value


def test_best_value_picks_rating_per_euro_and_skips_unrated():
    nan = float("nan")
    assert best_value([4.5, 4.0, 5.0, nan], [300, 100, 200, 50], [500, 400, 3, 900]) == 1  # 5.0 has 3 reviews; nan rating skipped
    assert best_value([3.5], [50]) is None
