# Refresh data/skyexpress_airports.csv (iata, city, country, seasonal): the destinations Sky Express's own site lists, each matched to
# its airport through Wikipedia's Sky Express destinations table (which also marks the seasonal ones) and data/airports.csv:
# python data/build_skyexpress_airports.py        (writes data/skyexpress_airports.csv next to this script)
# ponytail: run by hand when Sky Express's network changes (a few times a year), not automated: the site's card markup
# (<h3 class="card-title">City</h3><span class="country">Country</span>) and Wikipedia's table layout are the ceiling. When the site
# is not parseable the Wikipedia rows alone are used. Check the printed counts and any "not matched" lines and fix FIX below.
"""Build data/skyexpress_airports.csv: every airport Sky Express (GQ) flies to now or seasonally, with its IATA code from data/airports.csv."""
import csv
import html
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_aegean_airports import DROP, airport_not_city, match, plain, wikipedia_rows  # noqa: E402  the same matching as the AEGEAN list

SITE = "https://www.skyexpress.gr/en/discover/destinations"
WIKI = "https://en.wikipedia.org/wiki/Sky_Express_(Greece)"
FIX = {("London", ""): "LGW", ("Skyros", "Skyros Island National Airport"): "SKU"}  # (city, airport) -> IATA where the match fails; "" when the city has no Wikipedia row


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "AegeanBook/1.0 (airport list refresh)"})
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8")


def site_rows(page):
    """[(city, country)] from the destination cards of Sky Express's destinations page; [] when the markup changed."""
    cards = re.findall(r'<h3[^>]*class="card-title"[^>]*>(.*?)</h3>\s*<span[^>]*class="country"[^>]*>(.*?)</span>', page, re.S)
    return [(html.unescape(c).strip(), html.unescape(k).strip()) for c, k in cards]


def main():
    with open(os.path.join(HERE, "airports.csv"), encoding="utf-8") as f:
        every = list(csv.DictReader(f))
    known = [r for r in every if r["size"] != "2"]  # Sky Express also serves small island airfields (Astypalaia, Leros): second try
    wiki = {}  # plain city -> (country, city, airport, note), the current destinations only
    try:
        page = get(WIKI)
        for country, city, airport, note in wikipedia_rows(page[page.index('id="Destinations"'):]):  # the table after that heading
            if not any(w in note.lower() for w in DROP):
                wiki[plain(city)] = (country, city, airport, note)
    except Exception as err:  # the site alone still gives the list, without seasonal marks
        print(f"Wikipedia not read ({type(err).__name__}: {err}): every airport is marked year-round")
    try:
        cities = site_rows(get(SITE))
    except Exception as err:
        print(f"skyexpress.gr not read ({type(err).__name__}: {err})")
        cities = []
    source = "skyexpress.gr"
    if not cities:  # the markup changed, or the site was down: Wikipedia's rows are the list
        source, cities = "Wikipedia", [(c, k) for k, c, _, _ in wiki.values()]
        if not cities:
            sys.exit("neither source could be read")
    out, missed = [], []
    for city, country in cities:
        _, city, airport, note = wiki.get(plain(city), (country, city, "", ""))  # the site's country name, Wikipedia's city spelling
        code = FIX.get((city, airport)) or (match(city, airport, country, known) or match(city, airport, country, every) if airport else None)
        if not code:  # no Wikipedia row (a new destination): the one big airport of that city in that country
            hits = [r for r in known if plain(r["country"]) == plain(country) and plain(r["city"].split(",")[0]) == plain(city)]
            hits = [r for r in hits if r["size"] == "0"] or hits
            code = hits[0]["iata"] if len(hits) == 1 else None
        if not code:
            missed.append((country, city, airport))
            continue
        out.append((code, city.split(" / ")[0], country.split(" / ")[0], int("seasonal" in note.lower())))
    out = sorted(set(out), key=lambda r: (r[2], r[1], r[0]))
    with open(os.path.join(HERE, "skyexpress_airports.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["iata", "city", "country", "seasonal"])
        w.writerows(out)
    greek = sum(r[2] == "Greece" for r in out)
    print(f"{len(out)} airports from {source} ({greek} in Greece, {len(out) - greek} abroad, {sum(r[3] for r in out)} seasonal)")
    for m in missed:
        print("not matched (add to FIX):", m)
    for r in out:
        if airport_not_city(r[1]):
            print("city reads like an airport (fix the source row or FIX):", r)


if __name__ == "__main__":
    main()
