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
