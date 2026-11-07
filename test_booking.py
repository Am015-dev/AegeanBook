"""The booking links (booking.py): exact URLs, and the Overview's passenger count multiplying the pass and feeding the links."""
from datetime import date

import booking
from test_overview import pages, seed

AEGEAN = "https://en.aegeanair.com/srv/posthandler/flight/search?AirportFrom=ATH&AirportTo=FRA&DateDeparture=07/11/2026"
PAX = "&Children12Nums=0&Children5Num=0&InfantsNum=0&Children16Nums=0&LangShort=en"


def test_aegean_and_google_urls_are_exact():
    d, b = date(2026, 11, 7), date(2026, 11, 14)
    assert booking.aegean_url("ATH", "FRA", d) == AEGEAN + "&AdultsNum=1" + PAX + "&TravelType=O"
    assert booking.aegean_url("ath", "fra", d, b, adults=3, cabin="business") == AEGEAN + "&AdultsNum=3" + PAX + "&TravelType=R&DateReturn=14/11/2026"
    assert "&AdultsNum=9&" in booking.aegean_url("ATH", "FRA", d, adults=12)  # AEGEAN takes 9 passengers at most
    assert booking.google_url("ATH", "FRA", d) == "https://www.google.com/travel/flights?q=Flights%20from%20ATH%20to%20FRA%20on%202026-11-07%20on%20Aegean"
    assert booking.google_url("ATH", "FRA", d, b, 2, "business") == ("https://www.google.com/travel/flights?q=Flights%20from%20ATH%20to%20FRA%20on"
                                                                    "%202026-11-07%20returning%202026-11-14%20for%202%20adults%20in%20business%20class%20on%20Aegean")


def test_overview_pass_is_priced_for_the_passengers_chosen(tmp_path, monkeypatch):
    open_page = pages(tmp_path, monkeypatch, None)
    seed(tmp_path / "tracker.db")
    at = open_page("overview.py").run()
    assert not at.exception
    text = lambda: " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption)
    assert "€210" in text() and "one adult, leaving in" in text() and "per-adult" not in text()  # €60 out + €150 back, for one
    hrefs = lambda: [b.proto.url for b in at.get("link_button")]
    assert any("AdultsNum=1&" in h and "AirportFrom=ATH&AirportTo=FRA" in h and "TravelType=R" in h for h in hrefs())
    # one adult and a 2-adult room: the review's "€1,504, so €1,718 for the whole trip" mixed the two; now both are named
    assert "Flights for 1 adult (€210) + this room = €1,010" in text() and "for the whole trip" not in text()
    at.number_input(key="ov-pax").set_value(3).run()
    assert not at.exception
    t = text()
    from test_overview import grid
    assert "€630" in " ".join(grid(at).stack())  # the date grid is for the passengers too, as its caption says
    assert "€630" in t and "3 adults, leaving in" in t and "The price is for 3 adults, from fares collected for one adult; the airline may price a group differently" in t
    # the room was priced for 2 adults, the flights are for 3: both bases named, never one silent total
    assert ("With Steigenberger for those 4 nights: €800 for a room for 2 adults. Flights for 3 adults (€630) + this room = €1,430; "
            "no price is saved for a room for 3 adults") in t and "for the whole trip" not in t
    assert sum("AdultsNum=3&" in h for h in hrefs()) == 1 and sum("for%203%20adults" in h for h in hrefs()) == 1
    at.number_input(key="ov-pax").set_value(2).run()  # the room's own count: added up
    assert "€800 for a room for 2 adults, so €1,220 for the whole trip for 2 adults." in text()
