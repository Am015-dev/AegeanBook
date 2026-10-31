"""Links that take the traveller from a fare in this app to the place to book it: aegeanair.com and Google Flights. Pure, stdlib only.

aegeanair.com deep link (found 2026-10-31). AEGEAN publishes no booking-link API, and no guide or forum documents one; the direct form
on https://en.aegeanair.com/ could not be read from this environment (HTTP 403 "Access Denied" from its bot protection, also through the
Wayback Machine). What AEGEAN's own fare site https://flights.aegeanair.com/en/ (built by EveryMundo) does when its "Book now" and search
form are used is a GET that its booking engine answered 200:

    https://en.aegeanair.com/srv/posthandler/flight/search?AirportFrom=ATH&AirportTo=LON&DateDeparture=30/11/2026&AdultsNum=1
        &Children12Nums=0&Children5Num=0&InfantsNum=0&Children16Nums=0&LangShort=en&Btn_DaysRange=7&DaysRange=3&directFlights=INDIRECT
        &TravelType=O&Referer=everymundo&emcid=T-4OIsW2ART                         (one way)
    ...&TravelType=R&DateReturn=14/11/2026...                                      (return)

So it carries the airports, the dates (DD/MM/YYYY), one way or return, and the passengers by age group (that form's "Adult, older than
12" counter is AdultsNum). Choosing Business on that form added nothing to the URL, so the cabin is NOT carried: AEGEAN's engine shows
both cabins on its results page anyway. Left out here: the EveryMundo tracking fields (Referer, emcid, Btn_DaysRange, DaysRange) and
directFlights, whose other values are undocumented. Not confirmed: that the results page really pre-fills from these (it renders in
JavaScript and did not render in a headless browser here), and whether the parameter names change when AEGEAN redesigns its site.
Sources: https://en.aegeanair.com/plan/booking-info/booking_methods/ (form fields, 9 passengers at most), https://flights.aegeanair.com/en/
(the deep link above), https://ignav.com/docs/booking-links (no AEGEAN entry; carrier URL formats change without notice)."""
from urllib.parse import quote, urlencode

AEGEAN = "https://en.aegeanair.com/srv/posthandler/flight/search"
GOOGLE = "https://www.google.com/travel/flights?q="


def aegean_url(orig, dest, depart, back=None, adults=1, cabin="economy"):
    """AEGEAN's flight search, pre-filled: IATA codes, `depart` / `back` dates (back None for one way), 1 to 9 adults. `cabin` is
    accepted for symmetry with google_url but the link cannot carry it (see the module docstring)."""
    q = [("AirportFrom", orig.upper()), ("AirportTo", dest.upper()), ("DateDeparture", f"{depart:%d/%m/%Y}"),
         ("AdultsNum", max(1, min(9, int(adults)))), ("Children12Nums", 0), ("Children5Num", 0), ("InfantsNum", 0), ("Children16Nums", 0),
         ("LangShort", "en"), ("TravelType", "R" if back else "O")]
    if back:
        q.append(("DateReturn", f"{back:%d/%m/%Y}"))
    return AEGEAN + "?" + urlencode(q, safe="/")


def google_url(orig, dest, depart, back=None, adults=1, cabin="economy"):
    """Google Flights, asked in words (it understands them): the route, the dates, Aegean, and the passengers and cabin when they are
    not the defaults, so a one-adult economy link is the one the Overview always had."""
    q = f"Flights from {orig} to {dest} on {depart:%Y-%m-%d}" + (f" returning {back:%Y-%m-%d}" if back else "")
    q += f" for {adults} adults" if adults > 1 else ""
    q += " in business class" if cabin == "business" else ""
    return GOOGLE + quote(q + " on Aegean")
