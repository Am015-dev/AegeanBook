# AEGEAN Fare & Miles Tracker (free)

Compares Economy cash, Business cash (Google Flights date grid via `fli` (package `flights`), scraped) and Business Miles+Bonus
(manual entry; AEGEAN has no public award API). No paid services.

    pip install -r requirements.txt
    streamlit run app.py

## The pages

**Overview** is the one place to find a trip. One search bar: From, To (every airport with fares saved first, then every other AEGEAN
destination, and **Anywhere** at the top), Leaving between (two dates: a weekend, mid-December, the whole summer; the next 30 days by default, up to 11 months ahead), Trip (return or one way), Nights (any stay from 1 to 60),
Cabin, Pay with (cash or Miles+Bonus award miles) and Passengers. Under it, one answer:

- a **route** gives a boarding pass with the cheapest trip leaving in that period and two plain sentences on what the price means
  ("Usually €180–€240 return. €140 is cheap."), **Book on aegeanair.com** (on a phone it stays at the bottom of the screen while the pass is
  on it), the Google Flights link, **Alert me if it gets cheaper** (opens Alerts on exactly that trip: `route`, `cabin`, `unit`, `price`,
  `depart`, `back`, `nights`, `trip`) and **Ask the expert about it**, the cheapest month, the date grid (day you leave × nights you stay,
  any stay from 1 to 60 nights, or a list of days for a one-way trip, with **Leave on** weekday pills; the day column and the header stay
  put while it scrolls, and the cheapest cell is scrolled into view), **How this fare moved**, and two expanders, **Best weekday to fly**
  and **How far ahead to book**, with the chart and a one-line takeaway for that route and cabin;
- **Anywhere** lists every destination with fares saved from there, cheapest first, as cards (the dates of the trip, how many days have a
  flight, what it usually costs; a route saved one way only shows its one-way); **See dates** puts that destination in To;
- a route or month with **nothing saved** gets one button, **Search this route on GitHub** (or **Search May 2027 on GitHub**), which fetches
  both directions on GitHub's servers and shows the answer when it lands, usually in 1–3 minutes; a route AEGEAN does not fly says so and
  names the airports it does fly in that city. Without the GitHub token, a line points to the Flights page instead.

On first load the page shows the first saved route (Athens → Frankfurt) and says so until you pick something. On a phone the bar keeps
From, To and Leaving between on lines of their own and puts Trip, Cabin, Pay with, Passengers and Nights on shared lines (`ui.CSS`, keyed
containers `ov-bar-route`, `ov-bar-trip` and `ov-answer`), so the pass is one screen down. **Expert** is the other
top-level page; Flights (search any dates by hand), Hotels, Alerts, Database, Statistics and Health are under **More**.

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
own computer the Flights page's **Award seats** tab (shown when Pay with is Miles) also has a **Collect award miles now** button. A day with empty miles was checked and has no award
seats; "nonstop" rows only count nonstop AEGEAN flights, the other rows any all-AEGEAN itinerary.

Send/inspect `award_recording.json` (review for personal data first) to build the automatic daily search.
The hotel page also uses Xotelo (free, unofficial) for a cheap-day calendar and Booking.com prices (`xotelo.py`, self-check `python xotelo.py`).
Saved prices are kept 5 days (`db.RETENTION_DAYS`) and reused; older rows are deleted on start.
Self-checks: `python db.py`, `python collector.py`, `python hotels.py`, `python plain.py`.
Test: `pytest` (set `CHROME_PATH` if Playwright's own Chromium isn't installed).

## Hotel page

`views/hotel.py` (under More): Hotel, Hotel name contains, Nights, Adults and Check-in between, then **Update prices**. Under the title
one sentence says when the prices were checked ("Prices checked Sat 31 Oct 10:25 for all 30 nights on Google Hotels and Booking.com"),
and the cards say "Cheapest night €143 · Sat 14 Nov (Google Hotels) · Booking.com €160", the average and the dearest night, with the
cheapest nights as chips and a chart where the cheapest nights are circled. The page prices the hotel only; flights plus hotel is the
Overview's job. A hotel with nothing saved shows one sentence and the Update prices button. Options holds the TripAdvisor key,
Booking.com on the cheap days, re-checking saved days, "Cheap means within …" and "Days of the week", each with a one-line help.
Source is Google Hotels via `stays` (lowest rate across the sites it compares, Booking.com usually among them).
Booking.com's own API is partner-only. Self-check: `python hotels.py`.

## When results look wrong

Open the app's **Health** page: it asks each data source (Google Flights calendar, Google Flights day search, Google Hotels,
Xotelo) one real question and shows which failed and why. Every update also ends with an honest summary
(priced / no flight / failed) and the Flights page's **Day by day** tab shows, per day, whether a price is missing because there is no
flight or because it was never checked.

Offline tests: `pytest` (see **Checks** below). Each module also has `python <module>.py` self-checks.

## Checks

Nothing broken should reach main, or Streamlit Cloud: three layers of checks, all free.

**Unit tests**, `python -m pytest -q` (needs `pip install -r requirements.txt pytest`). About a minute, no network, no browser: every
module's logic, each page run with Streamlit's `AppTest` on small data of its own (`conftest.py` keeps the committed `data/*.csv` and
GitHub out of them), the guard that each page's `importlib.reload` check matches its module's `VERSION` (Streamlit Cloud keeps old modules
across redeploys), and `test_pages.py`, which runs every page under `views/` against the committed data in `data/`, so a page that only
breaks on the real files is caught too. `test_award_collector.py` and the smoke test skip themselves when Playwright is not installed.

**Browser smoke test**, `smoke_test.py` (marked `smoke`): the app as a visitor sees it. It starts `streamlit run app.py` on a free port with
the committed data, opens Overview, Expert, Flights, Hotels, Alerts and Database in Chromium at 1366×900 and 390×844 and fails on a
Streamlit exception on the page, anything wider than the screen, text under 12px, an uppercase letter-spaced label or an error in the
browser console; on the Overview it also toggles One way, asks for a month nobody has fetched (the **Search … on GitHub** button must
offer it) and opens Anywhere (a list of destinations). To run it:

    pip install -r requirements.txt pytest playwright
    playwright install --with-deps chromium      # once; or point PLAYWRIGHT_CHROMIUM_EXECUTABLE at a Chromium you have
    python -m pytest -q -m smoke smoke_test.py   # about a minute

It runs in the plain `pytest` too when Playwright and a Chromium are there, and is skipped otherwise. The app under test gets a GitHub
token that is not one, so the pages offer the fetch buttons the deployed app has; nothing is pressed and nothing reaches GitHub.

**CI**, `.github/workflows/ci.yml`, on every pull request and every push to main: two jobs on Python 3.12 with the pip cache, `test`
(`pip install -r requirements.txt pytest`, then `python -m pytest -q`, like the deployed app: no browser) and `smoke` (adds Playwright
and Chromium, then the smoke test), each a few minutes. A new push cancels the run of the one before. The daily, fetch and hotel
workflows are the data runs and are not part of this.

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
- **Dates:** Leaving between, two dates (the next 30 days by default, up to 11 months ahead).
- **Pay with:** Cash € (the default) shows the cash fares; Miles adds the award seats collected for the route (Miles+Bonus, economy and
  business) beside them, as on the Overview, with one line saying where they come from ("Miles+Bonus award seats, collected on the
  owner's computer; taxes not included"), and an **Award seats** tab with the month calendar.
- **Options** (an expander under Update prices): "Nonstop only", "Re-check days already saved", the Sky Express toggle until its fares
  are saved, "Cheap means within …" (every day within 0–30 % of the cheapest price counts as cheap, 10 % by default; a route often has
  several days at the same lowest fare) and "Days of the week" (only the weekdays you can travel count), each with a one-line help.
- **What the page says:** one sentence under the title on when the prices were checked ("Prices checked Sat 31 Oct 10:22 for all 30 days,
  both ways"), the cards ("Cheapest economy €75 · Thu 19 Nov · one of 3 days within 10% of this price"), the chips as a sentence
  ("Economy is cheapest on Thu 19 Nov €75 …"), then the tabs that apply: **Price chart** (the cheapest days are circled), **Award seats**
  only when paying with miles, **Day by day**, and **Trip cost** only for a return once a hotel has one-night prices saved. A route with
  nothing saved shows one sentence and the Update prices button, nothing else.

## Return trips and statistics

- **Return:** choose Return and one or more stay lengths (Nights away). The app also fetches the way back and ranks every
  departure + return pair by total price (two one-way fares, as Aegean prices them). See the Day by day tab for all pairs.
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
network, the Overview (once Sky Express fares are saved for the route and period) and the Flights page's search card show a **Compare with
Sky Express** toggle (its state is kept per route). On the Overview it adds **Cheapest by airline** under the boarding pass: one card per airline with its cheapest trip for
the same days and nights picked, the cheaper one marked, a line saying how much cheaper, plus a day-by-day table of both fares and the
difference under the grid; on the Flights page Sky Express's
economy fare is a dashed line on the price charts, and **Update prices** fetches it for the same days (its own run on GitHub, or in the app
without a token). With no Sky Express fares saved yet, the Overview shows **Search Sky Express fares** in the toggle's place (GitHub token
needed, as for AEGEAN's fetches; without one, nothing). The fares are fetched by `fetch_route.py` with `AIRLINE=GQ` (the workflow's `airline` input), nonstop, economy only, into
`data/skyexpress.csv`, which the app loads into its own table (`gq_prices`) beside AEGEAN's, so nothing about the AEGEAN data changes; a
Sky Express route searched from the app is tracked in `data/tracked_routes.csv` (column `airline`) and refreshed by the daily run like the
others. Every Sky Express fare collected is also kept for good in `data/history_skyexpress.csv`, its own permanent history beside
`data/history_prices.csv` (one row per fare change, never trimmed), written by every fetch from the app and by the daily refresh, and shown
and downloadable on the Database page under **Sky Express fares**. Only itineraries whose every leg is Sky Express count, as only all-AEGEAN ones do for AEGEAN. The airport list is
`data/skyexpress_airports.csv` (62 airports: 33 in Greece, 29 abroad, seasonal ones marked), refreshed by hand with
`python data/build_skyexpress_airports.py`, which reads the destination cards on skyexpress.gr (and Wikipedia's Sky Express destinations
table for the airport of each city and the seasonal marks; Wikipedia alone when the site cannot be parsed) and prints any city it could
not match. Business is not compared: Sky Express sells one cabin on most of its network.

## Daily fares (automatic, free)

Every morning at 07:17 Athens time GitHub Actions (`.github/workflows/daily.yml`) runs `collect_daily.py`. It fetches
ATH–FRA, FRA–ATH, ATH–ZRH and ZRH–ATH, both cabins, nonstop, for the next 90 days. That is 720 prices. Days that fail
are retried in up to 4 passes. If any are still missing, the run is marked red, and those days keep yesterday's price.
The same run fetches the Hotels page's default hotel (Steigenberger Frankfurter Hof, 1 night, 2 adults) from Google Hotels and Booking.com for every check-in day, into `data/daily_hotel.csv` (change `HOTEL` in `collect_daily.py` for another hotel). The flight results go to `data/daily.csv`, and the app loads that file when it starts, so these routes are ready without
pressing Update prices. The same run also fetches Sky Express economy fares on those legs where both airports are in its network
(ATH–FRA and back today; `AIRLINE=GQ python fetch_route.py --daily`), into `data/skyexpress.csv` and its permanent history, so the
Compare with Sky Express toggle has something to show on the daily routes without a fetch. To run it by hand, open GitHub → Actions →
Daily fares → Run workflow. To change the routes, edit `LEGS` in `collect_daily.py`.

The run is three jobs. **collect** does the above and commits it, then plans the refresh (`python fetch_route.py --plan`): every route
searched from the app in the last 60 days (`data/tracked_routes.csv`, AEGEAN and Sky Express alike), in groups of 8 routes of one airline.
**refresh** is one job per group (a matrix, like the fetch-route workflow: at most 5 at a time), each on its own GitHub server with its own
share of Google's rate limit and its own 30-minute timeout, fetching its routes (`python fetch_route.py --refresh "<legs>" 1 new_rows.csv`)
and then merging its rows into whatever main is newest (`--merge-refresh`, the same conflict-proof loop as a fetch from the app, which does
not count as a new search), so a route that fails or a server Google throttles costs only its own job. **finish** runs on the newest main
once every job is in: the digest of what changed (`digest.py`, committed as `data/latest_changes.md` and posted as an issue), then the
price alerts; it is marked red when the daily legs still have days missing. Measured on 31 Oct 2026: the four daily legs take about 2
seconds (Google's calendar answers a month at once), the hotel about 2.5 minutes, and a 90-day route 1 to 10 seconds, so 40 routes on
one server fitted the old 150-minute timeout only as long as Google kept answering; one bad morning would have lost the day. Up to 120
routes are refreshed (`fetch_route.TRACK_MAX`; the oldest searches are skipped past that), about 15 jobs of a minute or two.

## Anywhere: where can I fly cheaply?

**Anywhere** in the Overview's To box is Google Flights' Explore for AEGEAN (`explore.py`): for the From, Leaving between, Nights and Cabin in the bar it
lists every destination with fares saved, cheapest first (the price, the dates of that trip, how many days have a flight, and what it
usually costs). **See dates** opens the date grid on that route. It only knows the routes someone has searched (`data/routes.csv`) or the
daily run tracks; a month nobody has fetched offers **Search all routes from Athens on GitHub**. The list is one grouped list, the price
trailing, with a **See dates for** pill row under it.

## Price insights (Overview)

Like Google Flights, the Overview says whether the boarding pass's fare is cheap, typical or high for that route, from our own history:
`data/history_prices.csv` keeps every fare change the daily run and the Flights page have seen since 29 Oct 2026. The pass says "Usually
€180–€240 return. €140 is cheap.": the range is the 25th to 75th percentile of every one-way fare seen on the route, on any departure day, in
any snapshot of the last 60 days (the two legs added up for a return). Until a route has seven days of prices the pass says instead what
trips like it usually cost among the dates saved, and **How this fare moved** under the date grid charts the pass's own days across the snapshots
(`price_insights.py`, tests in `test_price_insights.py`). **Best weekday to fly** and **How far ahead to book** (the former Insights page,
`insights.py`) are expanders under the grid for the route and cabin picked.

## The expert (Expert page)

The Expert page reads the same answer as the Overview's boarding pass and explains it. Its controls are the Overview's (From, To, Leaving between,
Cabin, Pay with, Nights, Days I can leave, plus a Hotel searched in that city), with the same defaults, and **Ask the expert about it** on the
Overview opens it on exactly the pass shown (`?from=&to=&cabin=&pay=&nights=`). The numbers come from the very functions the pass uses, with the
same filters (`expert_chat.facts`: `deals.best_trip` for the recommended trip, `deals.trip_context` for what most trips in the period cost and
the lowest, `price_insights` for the usual range, Sky Express's best trip when it has fares, the hotel for those nights), so the two pages cannot
disagree. **Today's briefing** is three plain sentences: the recommended trip with dates, nights and price; why (the usual range or what most
trips cost, the weekday, how far ahead); and the one alternative that matters (Sky Express when it is cheaper, the cheapest cash trip when
paying with miles, otherwise the next-cheapest week). An AI model writes them in its own words, once a day per search, from those facts and
nothing else; when it is busy, out of quota or there is no key, the page shows the same three sentences built in Python
(`expert_chat.sentences`), so it never shows nothing. Under the briefing are the Overview's two links for that very trip, **Book on
aegeanair.com** and **Check it on Google Flights** (`booking.aegean_url`, `booking.google_url`).

**Suggested questions** sit under the briefing as pills; clicking one asks it, and the answer comes instantly from the saved fares, in plain
Python (`expert_chat.questions` and `expert_chat.answer`), with or without an AI key:

- *Cheaper if I leave a day earlier or later?*: the same trip shifted a day either way (`facts()['near']`); when a neighbour is cheaper, why
  the pass did not take it (a weekday you cannot leave on, or outside the days searched).
- *What if I stay a week?*: the cheapest 7-night trip with the same filters (`week`), and the gap to the recommended stay.
- *Is business worth it?*: the recommended dates in both cabins (`cabins`), and what the gap means (a small gap, about double, a steep premium).
- *How does Sky Express compare?*, only on a route Sky Express serves: its cheapest trip, its price on AEGEAN's dates and AEGEAN's on its dates.
- *Should I book now or wait?*, cash fares only: the fare's verdict against the route's usual range (`price_insights`), and how the fare of those
  very dates has moved over the saved history (`trend`: first seen, now, lowest, highest, a week ago).

A question is offered only when the facts can answer it. The same answers go into the model's data, and its rules forbid any number that is
not there, so a typed question gets the same numbers in other words. The chat answers typed follow-ups from the same facts plus every tracked
route's one-way fares, and says when something is not in the data ("I only have fares up to 29 January; search that month on the Overview").
Add one key under **Settings → Secrets** of the app on Streamlit Cloud:

- `GEMINI_API_KEY = "..."`: free. Create it at aistudio.google.com; no card needed. Uses `gemini-3.8-flash`, falling back to
  `gemini-3.5-flash-lite` when the first is busy or out of free quota.
- `ANTHROPIC_API_KEY = "..."`: Claude Haiku 5.5, about €0.001 a question. Used only when no Gemini key is set.

Without either key, the page still shows the plain briefing, the booking links and the suggested questions; only typed questions need a key.
Either way the model answers at most 60 typed questions a day (`MAX_PER_DAY` in `expert_chat.py`); the suggested ones cost nothing.

## Price alerts (free, by email or phone)

Anyone using the app can open **Alerts**, or press **Alert me if it gets cheaper** under the Overview's boarding pass, and say in one
form what to watch: "Tell me when Athens → Frankfurt economy drops below €150 return (2–7 nights), leaving in December 2026". From the
pass the form is already filled in (route, cabin, cash or miles, return or one way, nights, month, and the pass's price as the limit),
so an alert is two clicks. The link carries query params `route=ATH-FRA`, `cabin`, `unit` (`eur` or `miles`), `trip` (`return` or
`oneway`), `nights=2-7`, `depart` and `back` (iso dates), `when=2026-12-01,2026-12-31` (the days to leave on) and `price`; any of them may
be missing and the form falls back to sensible defaults. The alert then comes:
- **as a phone notification** through the free ntfy app (iPhone/Android, no account): the page shows a private topic to subscribe to
  and sends a first notification at once, so the visitor sees it works;
- **as an email**: the first email asks to confirm the address (so nobody can sign a stranger up); nothing else is sent until its link
  is opened. Every email and notification has a link that stops the alert.

A return alert means what the traveller asked: it watches the cheapest return trip (the two one-way fares added up, for the nights and
the departure days chosen) exactly as the boarding pass prices one; a one-way alert watches the one-way fare. Every morning, after the
daily run, `alerts.py` reads the same database as the app (the daily legs, every route fetched from the app, the award miles) and tells
the owner of each alert when it is met, in one sentence with the dates and a booking link ("Athens → Frankfurt is now €139 return in
economy, below your €150: leave Thu 19 Nov, back Sat 21 Nov (2 nights). Book: …"): the first time, and again only when it gets
cheaper. Alerts are stored as issues labelled `price-alert` in this private repository (visitors' email addresses stay private), at
most 5 per address and 300 in all. The page lists a visitor's own alerts, each with a Stop button, once the visitor has shown the
address or phone is theirs: by making an alert for it in that browser, or by opening the confirm or stop link from any of its messages.

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
`miles below: 16000`), optional `trip: return` with `nights: 2-7`, and optional `from:` and `to:` departure dates. Those are answered
with a comment on the issue, which GitHub notifies.


## Searching from the app

Google rate-limits Streamlit Cloud's shared server (HTTP 429), often before this app has asked anything. So with the GitHub token in
the secrets (Actions read and write, Contents read), **Update prices** on the Flights page does not search from Streamlit: it starts
`.github/workflows/fetch-route.yml`, which runs `fetch_route.py` on a fresh GitHub server (as the daily run does), retries failed days
after a pause, and commits the fares to `data/routes.csv`. The page shows "Searching on GitHub's servers" and reloads with the fares,
usually within 1–3 minutes. The app also reads `data/*.csv` straight from GitHub (every 5 minutes at most), so the daily fares and
fetched routes show without waiting for Streamlit to redeploy, and a fetched route survives the app restarting. Without the token (on
your own computer, say), the search runs in the app as before.

A searched route is remembered in `data/tracked_routes.csv` and refreshed every morning by the daily run (the refresh jobs of
`daily.yml`, the next 90 days in both cabins, 8 routes a job, up to 120 routes) for 60 days after it was last searched, so the fares build a history in
`data/history_prices.csv` and the app can later say whether a fare is low compared with recent weeks. The fares a search brings back go
into that history at once (the workflow's save step, like Sky Express's), so no fare is lost before the next refresh; the history only
keeps a row when a price changes, so the refresh seeing the same fare adds nothing.

**Planning months ahead.** The Overview's and the Expert's *Leaving between* accept any two dates up to about 11 months
ahead (`trip_plan.period`), not only the days with fares saved. Pick days nobody has fetched and
the Overview and the Expert offer **Search 12–18 Dec on GitHub**, which fetches both directions for those days on
GitHub's servers (only the days still missing, like Update prices on Flights) and reload the page with the fares; with To set to Anywhere
the Overview's button fetches the month for every route from the chosen airport in one request. Without the GitHub token the pages say the month is not fetched yet and point to the
Flights page. A month fetched this way is tracked with its dates (`from,to` in `data/tracked_routes.csv`, the union of every range asked
for, at most today + 330 days), and the daily refresh fetches each tracked route up to that day instead of stopping at 90 days; the
daily routes (ATH–FRA, ATH–ZRH) get only the part beyond the daily run's own 90 days. *Actions minutes:* a one-way leg costs about
one minute per 60 days, so a 120-day summer range adds roughly 2 extra minutes per route pair per day to the daily run, for as long as
the route stays tracked (60 days after it was last asked for). GitHub gives private repositories 2,000 free minutes a month; the Actions
tab shows the usage.

Hotels work the same way: **Update prices** on the Hotels page starts `.github/workflows/fetch-hotel.yml` (`fetch_hotel.py`: Google
Hotels for every check-in not saved yet, plus the TripAdvisor cheap-day calendar and Booking.com on the cheap days when a key is given),
which saves to `data/hotel_searches.csv`. **Show flight times** under the Overview's boarding pass starts `.github/workflows/fetch-details.yml`
(`fetch_details.py`), which saves the flight numbers, times and Google's lowest fare of the leave and return days to
`data/flight_details.csv`. Those prices are Google's lowest fare for the flight, whatever it includes; the app does not know the fare family. Every fetch merges its rows into the newest file before pushing, so fetches running at the
same time (and the daily run) never overwrite each other.

## GitHub minutes

A private repository has 2,000 free GitHub Actions minutes a month; the morning run and the searches from the app spend about 600.
`budget.py` reads the account's usage from GitHub's billing API at the start of every run; past 85% the morning run and the searches do
nothing until the month resets, so the account never pays. The reading needs a token that can see billing: add a repository secret
`BILLING_TOKEN` (a fine-grained personal access token with the account permission *Plan: read*, or a classic token with the `user`
scope). Without it the guard cannot read the usage and nothing is paused. The Health page shows the same line, with the app's own token
when it has that permission.