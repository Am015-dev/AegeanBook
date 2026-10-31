# Refresh data/aegean_airports.csv (iata, city, country, seasonal) from Wikipedia's list of AEGEAN destinations:
# python data/build_aegean_airports.py            (writes data/aegean_airports.csv next to this script)
# ponytail: run by hand when AEGEAN's network changes (a few times a year), not automated: Wikipedia's table layout and wording
# ("Seasonal", "Terminated") are the ceiling, so check the printed counts and any "not matched" lines and fix FIX below.
"""Build data/aegean_airports.csv: every airport AEGEAN flies to now or seasonally, with its IATA code from data/airports.csv."""
import csv
import html
import os
import re
import unicodedata
import urllib.request

URL = "https://en.wikipedia.org/wiki/List_of_Aegean_Airlines_destinations"
HERE = os.path.dirname(os.path.abspath(__file__))
DROP = ("terminated", "closed")  # notes that mean AEGEAN no longer flies there; "Begins <date>" and "Focus city" stay
FIX = {("Tirana", "Tirana International Airport Nënë Tereza"): "TIA", ("Frankfurt", "Frankfurt Airport"): "FRA",
       ("Lemnos", "Lemnos International Airport"): "LXS", ("Basel", "EuroAirport Basel Mulhouse Freiburg"): "BSL"}  # (city, airport) -> IATA where the match below fails; add after reading the output
# the app shows the `city` column (trip_plan.city), so it must be the city travellers book by, never the airport or its town:
# Chania, not Souda; Athens, not Eleftherios Venizelos. Words that mean a row got the airport instead (data/build_skyexpress_airports.py checks too).
AIRPORT_WORDS = ("airport", "international", "intl", "souda", "venizelos", "macedonia", "diagoras", "kazantzakis", "kapodistrias",
                 "ippokratis", "democritus", "schiphol", "heathrow", "gatwick", "malpensa", "fiumicino", "de gaulle", "orly", "ben gurion")


def airport_not_city(city):
    """True when a city cell reads like an airport name, so the printed output says which row to fix in FIX or the source."""
    return any(w in city.lower() for w in AIRPORT_WORDS)


def plain(s):
    """Lower-case ASCII words only: 'Nice Côte d'Azur Airport' -> 'nice cote d azur'; no 'airport' / 'international' noise."""
    s = unicodedata.normalize("NFKD", re.sub(r"[–—/-]", " ", s or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(w for w in re.sub(r"[^a-z0-9]+", " ", s).split() if w not in ("airport", "international", "intl"))


def cells(row):
    """Text of each td/th in one table row, with its rowspan: [(text, rowspan)]."""
    out = []
    for attrs, inner in re.findall(r"<t[dh]([^>]*)>(.*?)</t[dh]>", row, re.S):
        inner = re.sub(r"<br[^>]*>", " / ", inner)  # Basel<br>Mulhouse<br>Freiburg
        text = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
        text = re.sub(r"\[\d+\]", "", text).strip()  # footnote marks
        m = re.search(r'rowspan="?(\d+)', attrs)
        out.append((text, int(m.group(1)) if m else 1))
    return out


def wikipedia_rows(page):
    """[(country, city, airport, note)] from the first wikitable, with rowspan cells (one country, many cities) filled down."""
    table = page[page.index("wikitable"):]
    table = table[:table.index("</table>")]
    rows, carry = [], {}  # carry: column -> (text, rows left)
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)[1:]:
        got, col, filled = cells(tr), 0, []
        while col < 5:
            if col in carry:
                text, left = carry[col]
                filled.append(text)
                carry[col] = (text, left - 1)
                if left - 1 == 0:
                    del carry[col]
            elif got:
                text, span = got.pop(0)
                filled.append(text)
                if span > 1:
                    carry[col] = (text, span - 1)
            else:
                filled.append("")
            col += 1
        if filled[2]:
            rows.append(tuple(filled[:4]))
    return rows


def match(city, airport, country, known):
    """IATA code for a Wikipedia row from data/airports.csv: the same airport name, else the big airport of that city, else the one
    scheduled airport in that country whose name contains the Wikipedia name (or the city). known: airports.csv rows with scheduled flights."""
    city, country = city.split(" / ")[0], country.split(" / ")[0]  # Basel / Mulhouse / Freiburg
    if (city, airport) in FIX:
        return FIX[(city, airport)]
    want, ctry, city_plain = plain(airport), plain(country), plain(city)
    same = [r for r in known if plain(r["country"]) == ctry] or known
    for pick in (lambda r: plain(r["name"]) == want,
                 lambda r: plain(r["city"].split(",")[0]) == city_plain and r["size"] == "0",  # Porto: OPO, not Porto Santo
                 lambda r: set(want.split()) <= set(plain(r["name"]).split()),
                 lambda r: set(plain(r["name"]).split()) <= set(want.split()),
                 lambda r: plain(r["city"]).split(" ")[:1] == [city_plain.split(" ")[0]] and r["size"] == "0",
                 lambda r: city_plain and city_plain in plain(r["city"])):
        hits = [r for r in same if pick(r)]
        if len(hits) == 1:
            return hits[0]["iata"]
    return None


def main():
    req = urllib.request.Request(URL, headers={"User-Agent": "AegeanBook/1.0 (airport list refresh)"})
    page = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")
    with open(os.path.join(HERE, "airports.csv"), encoding="utf-8") as f:
        known = [r for r in csv.DictReader(f) if r["size"] != "2"]
    out, missed, dropped = [], [], 0
    for country, city, airport, note in wikipedia_rows(page):
        if any(w in note.lower() for w in DROP):
            dropped += 1
            continue
        code = match(city, airport, country, known)
        if not code:
            missed.append((country, city, airport))
            continue
        out.append((code, city.split(" / ")[0], country.split(" / ")[0], int("seasonal" in note.lower())))
    out.sort(key=lambda r: (r[2], r[1], r[0]))
    with open(os.path.join(HERE, "aegean_airports.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["iata", "city", "country", "seasonal"])
        w.writerows(out)
    print(f"{len(out)} airports ({sum(r[3] for r in out)} seasonal), {dropped} terminated or closed dropped")
    for m in missed:
        print("not matched (add to FIX):", m)
    for r in out:
        if airport_not_city(r[1]):
            print("city reads like an airport (fix the source row or FIX):", r)


if __name__ == "__main__":
    main()
