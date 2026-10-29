"""Xotelo (free, keyless, unofficial, built on TripAdvisor data): a month cheap-day heatmap and Booking.com nightly rates.
Hotels are identified by a TripAdvisor key like g187337-d202275 (found in the hotel's TripAdvisor URL)."""
import json
import re
import urllib.request
from datetime import timedelta

API = "https://data.xotelo.com/api/"
TIERS = {"cheap_price_days": "cheap", "average_price_days": "average", "high_price_days": "high"}


def hotel_key(text):
    """Pull 'g123-d456' out of a TripAdvisor URL or a bare key; None if absent."""
    m = re.search(r"g\d+-d\d+", text or "")
    return m.group(0) if m else None


def _get(path, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    r = json.load(urllib.request.urlopen(f"{API}{path}?{q}", timeout=40))
    if r.get("error"):
        raise RuntimeError(r["error"].get("message", "Xotelo error"))
    return r["result"]


def parse_heatmap(result):
    return {d: tier for k, tier in TIERS.items() for d in result["heatmap"].get(k, [])}


def heatmap_range(key, start, end):
    """{iso day: cheap|average|high}. One call covers about the 30 days before its chk_out date."""
    out, chk_out = {}, end + timedelta(1)
    while True:
        out.update(parse_heatmap(_get("heatmap", hotel_key=key, chk_out=chk_out.isoformat())))
        chk_out -= timedelta(30)
        if chk_out <= start:
            return out


def booking_rate(key, day, nights=1):
    """Booking.com's nightly rate in EUR for a stay starting `day`, or None if it isn't listed."""
    r = _get("rates", hotel_key=key, chk_in=day.isoformat(), chk_out=(day + timedelta(nights)).isoformat(), currency="EUR")
    return next((x["rate"] for x in r["rates"] if x["code"] == "BookingCom"), None)


if __name__ == "__main__":  # self-check, no network
    from datetime import date
    assert hotel_key("https://www.tripadvisor.com/Hotel_Review-g187337-d202275-Reviews-X.html") == "g187337-d202275"
    assert hotel_key("g1-d2") == "g1-d2" and hotel_key("steigenberger") is None
    h = {"heatmap": {"cheap_price_days": ["2026-11-14"], "average_price_days": ["2026-11-13"], "high_price_days": ["2026-11-16"]}}
    assert parse_heatmap(h) == {"2026-11-14": "cheap", "2026-11-13": "average", "2026-11-16": "high"}
    calls = []
    _get = lambda path, **p: (calls.append(p["chk_out"]), {"heatmap": {}})[1]
    heatmap_range("k", date(2026, 11, 1), date(2026, 12, 31))  # 61 days -> 3 calls
    assert len(calls) == 3, calls
    print("ok")
