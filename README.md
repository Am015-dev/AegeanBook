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

### Award miles (AEGEAN-operated flights, Economy and Business, next 90 days, four legs)

    python award_collector.py fetch                      # ATH-FRA FRA-ATH ATH-ZRH ZRH-ATH, or name legs: fetch ATH-FRA
    award_daily.bat                                      # fetch + commit/push data/daily_award.csv (Task Scheduler, daily)

The award search needs your AEGEAN login, so it only runs on your own computer (close every Chrome window using `.aegean_profile`
first). It writes `data/daily_award.csv`; the deployed app loads that file, so pushing it is what updates the live app. On your
own computer the Awards tab also has a **Collect award miles now** button. A day with empty miles was checked and has no award
seats; "nonstop" rows only count nonstop AEGEAN flights, the other rows any all-AEGEAN itinerary.

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

## Return trips and statistics

- **Return:** choose Return and one or more stay lengths (Nights away). The app also fetches the way back and ranks every
  departure + return pair by total price (two one-way fares, as Aegean prices them). See the Table tab for all pairs.
- **Statistics page:** every price update of the last 30 days (priced / nothing found / failed, time taken, first problem),
  plus what is saved now per route: days checked, how many came from Google's calendar vs. day-by-day search, and
  cheapest / median / dearest prices.

## Daily fares (automatic, free)

Every morning at 07:17 Athens time GitHub Actions (`.github/workflows/daily.yml`) runs `collect_daily.py`. It fetches
ATH–FRA, FRA–ATH, ATH–ZRH and ZRH–ATH, both cabins, nonstop, for the next 90 days. That is 720 prices. Days that fail
are retried in up to 4 passes. If any are still missing, the run is marked red, and those days keep yesterday's price.
The same run fetches the Hotels page's default hotel (Steigenberger Frankfurter Hof, 1 night, 2 adults) from Google Hotels and Booking.com for every check-in day, into `data/daily_hotel.csv` (change `HOTEL` in `collect_daily.py` for another hotel). The flight results go to `data/daily.csv`, and the app loads that file when it starts, so these routes are ready without
pressing Update prices. To run it by hand, open GitHub → Actions → Daily fares → Run workflow. To change the routes,
edit `LEGS` in `collect_daily.py`.

## The expert (Expert page, and its daily take on the Overview)

An AI model reads the saved fares, award miles and hotel prices, writes a three-line briefing once a day (shown on the Overview) and answers questions in a chat that remembers the conversation. **Plan a trip** on the Expert page asks it from your own choices (destination, month, cabin, cash or miles, one way or return with nights, hotel, the days you can leave): the app works out every candidate trip first, and the expert explains the best time to fly and why. The hotel is one you searched on the Hotels page (1 night) in that city, the most recent first; the Overview's Frankfurt trips use the most recent Frankfurt search too. Add one key under
**Settings → Secrets** of the app on Streamlit Cloud:

- `GEMINI_API_KEY = "..."`: free. Create it at aistudio.google.com; no card needed. Uses `gemini-3.8-flash`, falling back to
  `gemini-3.5-flash-lite` when the first is busy or out of free quota.
- `ANTHROPIC_API_KEY = "..."`: Claude Haiku 5.5, about €0.001 a question. Used only when no Gemini key is set.

Without either key, the expert is hidden. Either way the app answers at most 60 questions a day (`MAX_PER_DAY` in `expert_chat.py`).
