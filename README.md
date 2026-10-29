# AEGEAN Fare & Miles Tracker (free)

Compares Economy cash, Business cash (Google Flights date grid via `fli` (package `flights`), scraped) and Business Miles+Bonus
(manual entry; AEGEAN has no public award API). No paid services.

    pip install -r requirements.txt
    streamlit run app.py

Self-check: `python collector.py`. Scraping may break whenever Google changes its page.

## Award recorder (optional, your own account, your own machine)

    pip install -r requirements-award.txt && playwright install chromium
    python award_collector.py login    # log in once, no password stored
    python award_collector.py record   # do one manual Business award search, then press Enter

Send/inspect `award_recording.json` (review for personal data first) to build the automatic daily search.
The hotel page also uses Xotelo (free, unofficial) for a cheap-day calendar and Booking.com prices (`xotelo.py`, self-check `python xotelo.py`).
Saved prices are kept 5 days (`db.RETENTION_DAYS`) and reused; older rows are deleted on start.
Self-checks: `python db.py`, `python collector.py`, `python hotels.py`.
Test: `pytest` (set `CHROME_PATH` if Playwright's own Chromium isn't installed).

## Hotel page

`pages/hotel.py` (auto-listed in the Streamlit sidebar): flexible check-in range → cheapest night + price chart + Excel.
Source is Google Hotels via `stays` (lowest rate across the sites it compares, Booking.com usually among them).
Booking.com's own API is partner-only. Self-check: `python hotels.py`.

## When results look wrong

Open the app's **Health** page: it asks each data source (Google Flights calendar, Google Flights day search, Google Hotels,
Xotelo) one real question and shows which failed and why. Every update also ends with an honest summary
(priced / no flight / failed) and the Table tab shows, per day, whether a price is missing because there is no flight or
because it was never checked.

Offline tests: `pytest` (`test_core.py`). Each module also has `python <module>.py` self-checks.

## Run it on your own computer (recommended)

Streamlit's free hosting shares one outbound address between many apps, and Google answers it with HTTP 429 (too many
requests). On your own computer the same code runs without that limit, and your saved prices stay for 60 days.

1. Install Python 3.10 or newer.
2. Windows: double-click `run_windows.bat`. Mac or Linux: run `./run_mac_linux.sh`.
3. Open http://localhost:8501.

**From your phone, free and private:** install the Tailscale app on the computer and on the phone and sign in to the same
account. Then open `http://<computer-name>:8501` on the phone (the name is shown in the Tailscale app). The computer has to
be on. Nothing is exposed to the public internet.

## Searching and filters

- **Airports:** type a city, airport name or code. The list (`data/airports.csv`) has every airport with scheduled flights,
  built from [OurAirports](https://ourairports.com/data/) (public domain) by `data/build_airports.py`.
- **Dates:** tap 15, 30, 60 or 90 days (starting tomorrow), or Custom for exact dates.
- **Cheap days:** "Cheap means within" keeps every day within 0–30 % of the cheapest price (a route often has several days
  at the same lowest fare); "Days of the week" limits cards, chips and chart to the weekdays you can travel.
