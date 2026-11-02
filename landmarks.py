"""Landmarks around a hotel for the Hotels page's map: the town centre, the main railway station and the cathedral (or main church),
from OpenStreetMap's Nominatim (free; at most one request a second and a named User-Agent, per its usage policy). Standard library
only, so the fetch workflow needs nothing more installed. Looked up once per hotel search and saved in data/hotel_landmarks.csv."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

URL = "https://nominatim.openstreetmap.org/"
UA = {"User-Agent": "AegeanBook fare tracker (github.com/Am015-dev/AegeanBook)", "Accept-Language": "en"}
FIELDS = ["query", "kind", "label", "name", "lat", "lng"]
BOX = 0.08  # degrees around the hotel to look in: about 9 km north-south


def get(path, tries=3, **q):
    req = urllib.request.Request(URL + path + "?" + urllib.parse.urlencode(dict(q, format="jsonv2")), headers=UA)
    for n in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                out = json.load(r)
            time.sleep(1.1)  # Nominatim's limit: one request a second
            return out
        except urllib.error.HTTPError as e:
            if e.code != 429 or n == tries - 1:
                raise
            time.sleep(10 * (n + 1))  # told to slow down: wait, then ask once more


def find(lat, lng, ask=get):
    """[dict(kind, label, name, lat, lng)] around a point: centre, station, church; a kind Nominatim has nothing for is left out."""
    box = dict(viewbox=f"{lng - BOX},{lat + BOX},{lng + BOX},{lat - BOX}", bounded=1, limit=5)
    town = ask("reverse", lat=lat, lon=lng, zoom=10)
    name = town.get("name") or ""
    out = [dict(kind="centre", label=f"{name} centre" if name else "Town centre", name=name, lat=float(town["lat"]), lng=float(town["lon"]))] \
        if town.get("lat") else []
    rank = lambda r: float(r.get("importance") or 0)
    # "<town> main station" finds Frankfurt (Main) Hauptbahnhof, "<town> railway station" Zürich HB or a small town's only station
    stations = [r for q in (f"{name} main station", f"{name} railway station") if name for r in ask("search", q=q, **box)
                if r.get("category") in ("railway", "public_transport") and r.get("type") in ("station", "halt")]
    if stations:
        s = max(stations, key=rank)
        out.append(dict(kind="station", label="Main station", name=s["name"], lat=float(s["lat"]), lng=float(s["lon"])))
    # the church near the town centre, not the most famous one in the next village: a box of about 2 km around the centre
    c_lat, c_lng = (out[0]["lat"], out[0]["lng"]) if out else (lat, lng)
    near = dict(viewbox=f"{c_lng - BOX / 4},{c_lat + BOX / 4},{c_lng + BOX / 4},{c_lat - BOX / 4}", bounded=1, limit=5)
    churches = [r for r in ask("search", q="cathedral", **near) if r.get("name")]
    label = "Cathedral"
    if not churches:
        churches, label = [r for r in ask("search", q="church", **near) if r.get("name")], "Main church"
    if churches:
        c = max(churches, key=rank)
        out.append(dict(kind="church", label=label, name=c["name"], lat=float(c["lat"]), lng=float(c["lon"])))
    return out


if __name__ == "__main__":  # self-check, no network
    answers = {"reverse": {"name": "Frankfurt am Main", "lat": "50.11", "lon": "8.68"}}
    def fake(path, q=None, **k):
        if path == "reverse":
            return answers["reverse"]
        return {"Frankfurt am Main main station": [{"name": "Frankfurt (Main) Hauptbahnhof", "category": "railway", "type": "station",
                                                     "importance": .51, "lat": "50.107", "lon": "8.663"},
                                                    {"name": "Hauptwache", "category": "railway", "type": "station", "importance": .38, "lat": "1", "lon": "1"}],
                "Frankfurt am Main railway station": [{"name": "Stadion", "category": "railway", "type": "station", "importance": .33, "lat": "2", "lon": "2"}],
                "cathedral": [], "church": [{"name": "Paulskirche", "importance": .4, "lat": "50.111", "lon": "8.681"}]}[q]
    got = {p["kind"]: p for p in find(50.11, 8.67, fake)}
    assert got["station"]["name"] == "Frankfurt (Main) Hauptbahnhof" and got["centre"]["label"] == "Frankfurt am Main centre"
    assert (got["church"]["label"], got["church"]["name"]) == ("Main church", "Paulskirche")
    print("ok")
