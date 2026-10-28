# AEGEAN Fare & Miles Tracker (free)

Compares Economy cash, Business cash (Google Flights via `fast-flights`, scraped) and Business Miles+Bonus
(manual entry; AEGEAN has no public award API). No paid services.

    pip install -r requirements.txt
    streamlit run app.py

Self-check: `python collector.py`. Scraping may break whenever Google changes its page.

## Award recorder (optional, your own account, your own machine)

    pip install -r requirements-award.txt && playwright install chromium
    python award_collector.py login    # log in once, no password stored
    python award_collector.py record   # do one manual Business award search, then press Enter

Send/inspect `award_recording.json` (review for personal data first) to build the automatic daily search.
Test: `pytest` (set `CHROME_PATH` if Playwright's own Chromium isn't installed).
