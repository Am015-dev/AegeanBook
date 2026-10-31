"""Fetching a month nobody has fetched yet, from the Overview, the Expert and Explore: one button that asks GitHub's servers for the days
still missing (Google blocks Streamlit's own server, so nothing is searched here), then ui.github_progress reloads the page with the fares.
The daily run keeps those months fresh afterwards (fetch_route.py --refresh reads the range from data/tracked_routes.csv).
A new module on purpose: Streamlit Cloud can run a page against older, already loaded modules."""
import time
from datetime import timedelta

import streamlit as st

import gh
from collector import gaps
from db import saved_flight_days
from trip_plan import HORIZON
from ui import github_note, github_progress

VERSION = 1  # pages reload this module when Streamlit Cloud still has an older copy loaded (it keeps modules across redeploys)


def missing(db, legs, today, nonstop=True):
    """(runs of days still missing, any leg with nothing saved at all). legs: [(orig, dest, first, last)]; a leg is cut at today + HORIZON.
    The runs are what to fetch ([(orig, dest, first, last)], as collector.gaps gives them: only the days not saved yet)."""
    runs, blank = [], False
    for o, d, a, b in legs:
        b = min(b, today + timedelta(HORIZON))
        if a > b:
            continue
        saved = saved_flight_days(db, o, d, nonstop, a, b)
        runs += [(o, d, x, y) for x, y in gaps(a, b, saved)]
        blank = blank or not saved
    return runs, blank


def button(key, label, legs, what, nonstop=True):
    """The button that starts fetching `legs` (from missing()) on GitHub, when the app has a token; without one, a note that `what` (e.g.
    "May 2027 from Athens to Frankfurt") is not fetched yet and where to search it. Afterwards the progress and the result note render here,
    and the page reloads with the fares when the run is done."""
    tok = gh.token()
    if not tok:
        st.caption(f"{what} is not fetched yet. Search those dates on the Flights page to fetch the fares.")
        return
    if not st.session_state.get(f"gh-{key}"):  # while a fetch runs, only its progress shows
        if st.button(label, key=f"fetch-{key}", icon=":material/cloud_download:"):
            try:
                st.session_state[f"gh-{key}"] = (gh.fetch(tok, legs, nonstop), time.time())
            except gh.GitHubError as err:
                st.error(str(err))
            else:
                st.rerun()  # the button gives way to the progress at once
    github_progress(key, "fetch-route.yml", "data/routes.csv", "fares")
    github_note(key)
