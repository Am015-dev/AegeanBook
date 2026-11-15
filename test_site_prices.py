"""Each site's price for exactly the stay asked (Xotelo, TripAdvisor's comparison), on fake answers: no test calls the network."""
import os
from datetime import date, datetime, timedelta

import fetch_hotel
import hotels
import xotelo
from xotelo import key_for, location, site_rates, site_url, sites_text, town

# Xotelo's rates answer for Amrâth Grand Hotel de l'Empereur, Maastricht, 4-7 Dec 2026, 2 adults, as it came on 15 Nov 2026
AMRATH = {"chk_in": "2026-12-04", "chk_out": "2026-12-07", "currency": "EUR", "rates": [
    {"code": "BookingCom", "name": "Booking.com", "rate": 143, "tax": None}, {"code": "CtripTA", "name": "Trip.com", "rate": 155, "tax": None},
    {"code": "Agoda", "name": "Agoda.com", "rate": 126, "tax": None}, {"code": "TravelClickCPA", "name": "Official Hotel", "rate": 137, "tax": None},
    {"code": "Vio", "name": "Vio.com", "rate": 144, "tax": None}, {"code": "Expedia", "name": "Expedia", "rate": None, "tax": None}]}


def test_site_rates_are_each_sites_night_and_stay_cheapest_first():
    rows = site_rates(AMRATH, 3)
    assert [(r["site"], r["night"], r["total"]) for r in rows] == [("Agoda.com", 126, 378), ("Official Hotel", 137, 411),
                                                                    ("Booking.com", 143, 429), ("Vio.com", 144, 432), ("Trip.com", 155, 465)]
    assert sites_text(rows) == "Booking.com €143 / cheapest site €126 (Agoda.com)"
    assert sites_text(rows[2:3]) == "Booking.com €143 (the cheapest site)" and sites_text(rows[:1]) == "cheapest site €126 (Agoda.com)"
    assert site_rates({"rates": []}, 3) == [] and sites_text([]) == ""


def test_key_for_takes_the_same_name_and_refuses_a_guess():
    listed = [("Amrâth Grand Hotel De L'Empereur", "g188575-d230890"), ("The Dutch", "g188575-d1"), ("Amrâth Hotel Bigarré", "g188575-d2"),
              ("Hotel Beaumont", "g188575-d3")]
    assert key_for("Amrâth Grand Hotel de l’Empereur", listed) == "g188575-d230890"  # Google's curly apostrophe, other case
    assert key_for("The Dutch", listed) == "g188575-d1"
    assert key_for("Amrâth", listed) is None  # two Amrâth hotels: no guess
    assert key_for("Hotel Nowhere", listed) is None
    assert location("g188575-d230890") == "g188575" and location("") is None
    pages = {0: {"total_count": 150, "list": [{"name": "A", "key": "g1-d1"}]}, 100: {"total_count": 150, "list": [{"name": "B", "key": "g1-d2"}]}}
    assert town("g1", get=lambda path, **p: pages[p["offset"]]) == [("A", "g1-d1"), ("B", "g1-d2")]


def test_site_url_fills_in_the_hotel_dates_and_adults():
    d = date(2026, 12, 4)
    url, shows = site_url("BookingCom", "Amrâth Grand Hotel", "g188575-d230890", d, 3, 2)
    assert "checkin=2026-12-04" in url and "checkout=2026-12-07" in url and "group_adults=2" in url and "no_rooms=1" in url
    assert shows == "Booking.com, these dates, 2 adults"
    assert site_url("Agoda", "X", "g1-d2", d, 3, 2)[0].startswith("https://www.tripadvisor.com/")  # Agoda's link needs its own city id
    assert "checkout=2026-12-07&adult=2" in site_url("CtripTA", "X", "g1-d2", d, 3, 2)[0]
    url, shows = site_url("TravelClickCPA", "X", "g188575-d230890", d, 3, 2)
    assert url == "https://www.tripadvisor.com/Hotel_Review-g188575-d230890-Reviews.html" and shows.startswith("compare on TripAdvisor")


def test_hotel_page_shows_each_sites_price_for_the_adults_and_nights_asked(tmp_path, monkeypatch):
    """The chosen hotel's per-site table and the cheapest headline use the sites' own rates for exactly the stay asked."""
    from streamlit.testing.v1 import AppTest
    now, day = datetime.now().isoformat(timespec="seconds"), (date.today() + timedelta(19)).isoformat()
    places = {"Grand Hotel Here": dict(lat=50.110, lng=8.676, stars=5, rating=4.6, reviews=900, prices={day: 129}),
              "The Dutch": dict(lat=50.112, lng=8.678, stars=4, rating=4.5, reviews=400, prices={day: 99}),
              "Plain Inn": dict(lat=50.111, lng=8.677, stars=3, rating=4.1, reviews=90, prices={day: 120})}
    for name in ("PLACES", "NEARBY", "LANDMARKS"):
        monkeypatch.setattr(fetch_hotel, name, tmp_path / f"{name.lower()}.csv")
    fetch_hotel.save_places(hotels.place_rows(places, "Site Query", 3, 2, now), tmp_path / "places.csv")
    fetch_hotel.save_nearby(hotels.day_rows(places, "Site Query", 3, 2, now), tmp_path / "nearby.csv")
    asked = []

    def fake(path, **p):
        if path == "list":
            return {"total_count": 2, "list": [{"name": "Grand Hotel Here", "key": "g777-d1"}, {"name": "The Dutch", "key": "g777-d2"}]}
        asked.append((p["hotel_key"], p["chk_in"], p["chk_out"], p["adults"]))
        rate = {"g777-d1": AMRATH["rates"], "g777-d2": [{"code": "BookingCom", "name": "Booking.com", "rate": 110},
                                                      {"code": "Agoda", "name": "Agoda.com", "rate": 104}]}[p["hotel_key"]]
        return {"rates": rate}
    monkeypatch.setattr(xotelo, "_get", fake)
    page = os.path.abspath("views/hotel.py")
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(page, default_timeout=60).run()
    for k, v in (("ho-query", "Site Query"), ("ho-filt", "Grand Hotel Here"), ("ho-link", "g777-d1")):
        at.text_input(key=k).set_value(v)
    at.number_input(key="ho-nights").set_value(3)
    at.run()
    assert not at.exception
    out = (date.fromisoformat(day) + timedelta(3)).isoformat()
    assert ("g777-d1", day, out, 2) in asked and all(a[3] == 2 for a in asked)  # exactly the dates and adults asked
    sites = next(m.value for m in at.markdown if m.value.startswith("| Site |")).splitlines()
    assert sites[0] == "| Site | € a night | € for 3 nights | See it |"
    assert sites[2].startswith("| Agoda.com | €126 | €378 | [compare on TripAdvisor")
    assert sites[3].startswith("| Official Hotel | €137 | €411 | [compare on TripAdvisor")
    assert sites[4].startswith("| Booking.com | €143 | €429 | [Booking.com, these dates, 2 adults](") and "group_adults=2" in sites[4]
    assert any(m.value.startswith('<p class="group">Prices for 2 adults, 3 nights from') for m in at.markdown)
    said = [m.value for m in at.markdown if "Cheapest for your stay:" in m.value][0]
    assert "**Cheapest for your stay: The Dutch**, €297 for 2 adults, 3 nights from" in said and "(€99 a night with taxes, Google Hotels; before taxes Agoda.com reports" in said
    assert "Plain Inn" not in said and "less than Grand Hotel Here: €387" in said
