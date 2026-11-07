"""Nearby airports: a cheaper trip from or to an airport close by, offline."""
from datetime import date

import pandas as pd

import nearby

PLACES = pd.DataFrame({"lat": [37.94, 35.53, 35.34, 40.52], "lng": [23.94, 24.15, 25.18, 22.97]}, index=["ATH", "CHQ", "HER", "SKG"])
START, END = date(2026, 12, 1), date(2026, 12, 10)


def fares(price):
    return {"economy": pd.Series({f"2026-12-{d:02d}": float(price + d) for d in range(1, 11)})}


def test_cheaper_nearby_destination_found_and_far_ones_left_out():
    legs = {("ATH", "CHQ"): fares(100), ("ATH", "HER"): fares(60), ("ATH", "SKG"): fares(10)}
    got = nearby.cheaper(legs, PLACES, "ATH", "CHQ", START, END, "economy", range(2, 8), 101, oneway=True)
    assert [(r["end"], r["airport"], r["price"], r["depart"]) for r in got] == [("to", "HER", 61, date(2026, 12, 1))]
    assert 90 < got[0]["km"] < 110 and got[0]["saving"] == 40  # SKG is cheaper still but ~600 km from Chania


def test_cheaper_origin_and_returns_only_against_returns():
    legs = {("HER", "ATH"): fares(30), ("ATH", "HER"): fares(30), ("CHQ", "ATH"): fares(80), ("ATH", "CHQ"): fares(80), ("SKG", "ATH"): fares(5)}
    got = nearby.cheaper(legs, PLACES, "CHQ", "ATH", START, END, "economy", range(2, 8), 170)
    assert [(r["end"], r["airport"], r["nights"]) for r in got] == [("from", "HER", 2)]  # SKG-ATH one way only, and too far
    assert nearby.cheaper(legs, PLACES, "CHQ", "ATH", START, END, "economy", range(2, 8), 170, flies=lambda o, d: False) == []


def test_nothing_without_fares_or_when_dearer():
    assert nearby.cheaper({}, PLACES, "ATH", "CHQ", START, END, "economy", range(2, 8), 100) == []
    legs = {("ATH", "HER"): fares(200)}
    assert nearby.cheaper(legs, PLACES, "ATH", "CHQ", START, END, "economy", range(2, 8), 100, oneway=True) == []
    assert nearby.cheaper(legs, PLACES, "ATH", "CHQ", START, END, "business", range(2, 8), 900, oneway=True) == []
