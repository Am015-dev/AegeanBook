# AEGEAN Fare & Miles Tracker (free)

Compares Economy cash, Business cash (Google Flights via `fast-flights`, scraped) and Business Miles+Bonus
(manual entry; AEGEAN has no public award API). No paid services.

    pip install -r requirements.txt
    streamlit run app.py

Self-check: `python collector.py`. Scraping may break whenever Google changes its page.
