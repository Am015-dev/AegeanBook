# Rebuild data/airports.csv: download https://davidmegginson.github.io/ourairports-data/airports.csv and countries.csv, then
# python data/build_airports.py airports.csv countries.csv data/airports.csv
"""Build data/airports.csv (size, iata, city, country, name; size 0 = large scheduled, 1 = other scheduled, 2 = no scheduled flights) for every airport the flight library knows, from OurAirports (public domain)."""
import csv, sys
from fli.models import Airport
src, countries_csv, out = sys.argv[1:4]
country = {r["code"]: r["name"] for r in csv.DictReader(open(countries_csv, encoding="utf-8"))}
best = {}
for r in csv.DictReader(open(src, encoding="utf-8")):
    code = (r["iata_code"] or "").strip()
    if code not in Airport.__members__:
        continue
    rank = (r["scheduled_service"] == "yes", r["type"] == "large_airport", r["type"] == "medium_airport")
    if code not in best or rank > best[code][0]:
        best[code] = (rank, r["municipality"] or "", country.get(r["iso_country"], r["iso_country"]), r["name"])
rows = []
for code, member in Airport.__members__.items():
    rank, city, ctry, name = best.get(code, ((False, False, False), "", "", member.value))
    size = 0 if rank[0] and rank[1] else 1 if rank[0] and rank[2] else 2  # big scheduled airports first in the search list
    rows.append((size, code, city, ctry, name or member.value))
rows.sort(key=lambda r: (r[0], (r[2] or r[4]).lower()))

with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["size", "iata", "city", "country", "name"]); w.writerows(rows)
print(len(rows), "airports;", sum(1 for r in rows if r[2]), "with a city;", sum(1 for r in rows if r[0] < 2), "with scheduled flights")
