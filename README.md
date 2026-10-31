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

- **Airports:** type a city, airport name or code. Only AEGEAN's destinations are offered (`data/aegean_airports.csv`, about 135
  airports, seasonal ones marked), because a route AEGEAN does not fly is the slowest search of all: Google has no calendar for it,
  so every day would be searched one by one. Refresh the list with `python data/build_aegean_airports.py` (reads Wikipedia's list of
  AEGEAN destinations and prints any airport it could not match). Names come from `data/airports.csv`, every airport with scheduled
  flights, built from [OurAirports](https://ourairports.com/data/) (public domain) by `data/build_airports.py`.
- **Dates:** tap 15, 30, 60 or 90 days (starting tomorrow), or Custom for exact dates.
- **Cheap days:** "Cheap means within" keeps every day within 0–30 % of the cheapest price (a route often has several days
  at the same lowest fare); "Days of the week" limits cards, chips and chart to the weekdays you can travel.

## Return trips and statistics

- **Return:** choose Return and one or more stay lengths (Nights away). The app also fetches the way back and ranks every
  departure + return pair by total price (two one-way fares, as Aegean prices them). See the Table tab for all pairs.
- **Statistics page:** every price update of the last 30 days (priced / nothing found / failed, time taken, first problem),
  plus what is saved now per route: days checked, how many came from Google's calendar vs. day-by-day search, and
  cheapest / median / dearest prices.

## Booking it, for one or more passengers

The boarding pass on the Overview has **Book on aegeanair.com** (`booking.py`): AEGEAN's own flight search, pre-filled with the airports, the
dates, one way or return, and the number of passengers chosen in the route bar (1 to 9 adults). The Flights page has the same button for the
cheapest economy day or return pair shown. AEGEAN publishes no booking-link API; the link is the one its own fare site
(flights.aegeanair.com) sends to `en.aegeanair.com/srv/posthandler/flight/search`, which answered it with its results page. What could not
be confirmed: that page renders in JavaScript, so it was not seen pre-filled from here, the cabin is not part of the link (AEGEAN shows both
cabins on its results anyway), and the names may change when AEGEAN redesigns its site; if the search opens empty, enter the dates from
the pass by hand. Fares are collected for one adult, so the pass multiplies the per-adult fare by the passengers chosen and says so; the
airline may price a group differently.

## Compare with Sky Express (economy only, for now)

Sky Express (GQ) is AEGEAN's competitor on Greek domestic routes and a growing set of European ones. Where both airports are in its
network, the Overview's route bar and the Flights page's search card show a **Compare with Sky Express** toggle (its state is kept per
route). On the Overview it adds one line to the boarding pass stub, "Sky Express: €X on <day> (economy)", the cheapest Sky Express trip for
the same days and nights picked, plus a day-by-day table of both fares and the difference under the grid; on the Flights page Sky Express's
economy fare is a dashed line on the price charts, and **Update prices** fetches it for the same days (its own run on GitHub, or in the app
without a token). With no Sky Express fares saved yet, the Overview offers **Fetch Sky Express fares** (GitHub token needed, as for AEGEAN's
fetches). The fares are fetched by `fetch_route.py` with `AIRLINE=GQ` (the workflow's `airline` input), nonstop, economy only, into
`data/skyexpress.csv`, which the app loads into its own table (`gq_prices`) beside AEGEAN's, so nothing about the AEGEAN data changes; a
Sky Express route searched from the app is tracked in `data/tracked_routes.csv` (column `airline`) and refreshed by the daily run like the
others. Only itineraries whose every leg is Sky Express count, as only all-AEGEAN ones do for AEGEAN. The airport list is
`data/skyexpress_airports.csv` (62 airports: 33 in Greece, 29 abroad, seasonal ones marked), refreshed by hand with
`python data/build_skyexpress_airports.py`, which reads the destination cards on skyexpress.gr (and Wikipedia's Sky Express destinations
table for the airport of each city and the seasonal marks; Wikipedia alone when the site cannot be parsed) and prints any city it could
not match. Business is not compared: Sky Express sells one cabin on most of its network.

## Daily fares (automatic, free)

Every morning at 07:17 Athens time GitHub Actions (`.github/workflows/daily.yml`) runs `collect_daily.py`. It fetches
ATH–FRA, FRA–ATH, ATH–ZRH and ZRH–ATH, both cabins, nonstop, for the next 90 days. That is 720 prices. Days that fail
are retried in up to 4 passes. If any are still missing, the run is marked red, and those days keep yesterday's price.
The same run fetches the Hotels page's default hotel (Steigenberger Frankfurter Hof, 1 night, 2 adults) from Google Hotels and Booking.com for every check-in day, into `data/daily_hotel.csv` (change `HOTEL` in `collect_daily.py` for another hotel). The flight results go to `data/daily.csv`, and the app loads that file when it starts, so these routes are ready without
pressing Update prices. To run it by hand, open GitHub → Actions → Daily fares → Run workflow. To change the routes,
edit `LEGS` in `collect_daily.py`.

## Explore: where can I fly cheaply?

The **Explore** page is Google Flights' Explore for AEGEAN: pick where you leave from, when (the next 30 days or a month), one way or a
2–7 night return, the cabin and a budget, and it lists every destination with fares saved, cheapest first (the price, the dates of that
trip, how many days have a flight, and the typical price), the ones over budget greyed at the bottom. **See dates** opens the Overview's
date grid on that route. It only knows the routes someone has searched (`data/routes.csv`) or the daily run tracks.

## Price insights (Overview)

Like Google Flights, the Overview says whether the boarding pass's fare is **Low**, **Typical** or **High** for that route, from our own
history: `data/history_prices.csv` keeps every fare change the daily run and the Flights page have seen since 29 Oct 2026. Low and high
are the 25th and 75th percentiles of every one-way fare seen on the route, on any departure day, in any snapshot of the last 60 days (the
two legs are added up for a return). Until a route has seven days of prices the pass only says since when it is tracked, and **How this
fare moved** under the date grid charts the pass's own days across the snapshots (`price_insights.py`, tests in `test_price_insights.py`).

## The expert (Expert page, and its daily take on the Overview)

An AI model reads the saved fares, award miles and hotel prices, writes a three-line briefing once a day (shown on the Overview) and answers questions in a chat that remembers the conversation. **Plan a trip** on the Expert page asks it from your own choices (destination, month, cabin, cash or miles, one way or return with nights, hotel, the days you can leave): the app works out every candidate trip first, and the expert explains the best time to fly and why. The hotel is one you searched on the Hotels page (1 night) in that city, the most recent first; the Overview's Frankfurt trips use the most recent Frankfurt search too. Add one key under
**Settings → Secrets** of the app on Streamlit Cloud:

- `GEMINI_API_KEY = "..."`: free. Create it at aistudio.google.com; no card needed. Uses `gemini-3.8-flash`, falling back to
  `gemini-3.5-flash-lite` when the first is busy or out of free quota.
- `ANTHROPIC_API_KEY = "..."`: Claude Haiku 5.5, about €0.001 a question. Used only when no Gemini key is set.

Without either key, the expert is hidden. Either way the app answers at most 60 questions a day (`MAX_PER_DAY` in `expert_chat.py`).

## Price alerts (free, by email or phone)

Anyone using the app can open **Alerts**, choose a route, cabin, cash or miles, a limit and optionally a month, and get:
- **a phone notification** through the free ntfy app (iPhone/Android, no account): the page shows a private topic to subscribe to and
  sends a first notification at once, so the visitor sees it works;
- **an email**: the first email asks to confirm the address (so nobody can sign a stranger up); nothing else is sent until its link is
  opened. Every email and notification has a link that stops the alert.

Every morning, after the daily run, `alerts.py` checks each alert and tells its owner when a fare (or award) meets it: the first time,
and again only when it gets cheaper. Alerts are stored as issues labelled `price-alert` in this private repository (visitors' email
addresses stay private), at most 5 per address and 300 in all.

**One-time setup by the owner** (free):
1. **Saving alerts:** on GitHub, Settings → Developer settings → Fine-grained tokens → Generate new token; Repository access: only this
   repository; Permissions → Issues: Read and write, **Actions: Read and write** and **Contents: Read** (the last two let the Flights page
   search on GitHub's servers and read the newest data, see "Searching from the app" below). Add it to the app's Streamlit secrets as
   `GITHUB_TOKEN = "..."` (or `GITHUB = "..."`). Without it, the Alerts page offers the owner a filled-in GitHub issue instead.
2. **Sending email:** turn on 2-step verification for a Gmail account, create an app password (Google Account → Security → App
   passwords), and add two repository secrets on GitHub (Settings → Secrets and variables → Actions): `SMTP_USER` (the Gmail address)
   and `SMTP_PASSWORD` (the app password). Add the same two lines to the Streamlit secrets as well, and confirmation emails go out the
   moment an alert is created instead of the next morning. Gmail sends up to about 500 emails a day. Phone alerts need no setup.

An alert can also be written by hand as an issue labelled `price-alert`: `route: ATH-ZRH`, `cabin: business`, `below: 250` (or
`miles below: 16000`), optional `from:` and `to:` dates. Those are answered with a comment on the issue, which GitHub notifies.


## Searching from the app

Google rate-limits Streamlit Cloud's shared server (HTTP 429), often before this app has asked anything. So with the GitHub token in
the secrets (Actions read and write, Contents read), **Update prices** on the Flights page does not search from Streamlit: it starts
`.github/workflows/fetch-route.yml`, which runs `fetch_route.py` on a fresh GitHub server (as the daily run does), retries failed days
after a pause, and commits the fares to `data/routes.csv`. The page shows "Searching on GitHub's servers" and reloads with the fares,
usually within 1–3 minutes. The app also reads `data/*.csv` straight from GitHub (every 5 minutes at most), so the daily fares and
fetched routes show without waiting for Streamlit to redeploy, and a fetched route survives the app restarting. Without the token (on
your own computer, say), the search runs in the app as before.

A searched route is remembered in `data/tracked_routes.csv` and refreshed every morning by the daily run (`fetch_route.py --refresh`,
the next 90 days in both cabins, at most 20 routes) for 60 days after it was last searched, so the fares build a history in
`data/history_prices.csv` and the app can later say whether a fare is low compared with recent weeks.

Hotels work the same way: **Update prices** on the Hotels page starts `.github/workflows/fetch-hotel.yml` (`fetch_hotel.py`: Google
Hotels for every check-in not saved yet, plus the TripAdvisor cheap-day calendar and Booking.com on the cheap days when a key is given),
which saves to `data/hotel_searches.csv`. **Show flight times** under the Overview's boarding pass starts `.github/workflows/fetch-details.yml`
(`fetch_details.py`), which saves the flight numbers, times and Google's lowest fare of the leave and return days to
`data/flight_details.csv`. Those prices are Google's lowest fare for the flight, whatever it includes; the app does not know the fare family. Every fetch merges its rows into the newest file before pushing, so fetches running at the
same time (and the daily run) never overwrite each other.
