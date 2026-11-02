"""The flights behind the Overview's boarding pass: number, times and Google's lowest fare for the leave day and the return day, from
data/flight_details.csv (written on GitHub's servers by fetch_details.py, because Google rate-limits Streamlit Cloud); or a button
that asks GitHub to look them up. A new module on purpose: Streamlit Cloud can run a page against older, already loaded modules."""
import csv
import os
import time

import streamlit as st

import gh
import ui

PATH = "data/flight_details.csv"
LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), PATH)


def load(path=None):
    """{(iso day, orig, dest, cabin): [row]} from the repository's current file (or the deployed copy), cheapest first."""
    path = path or gh.data_file(PATH) or LOCAL
    out = {}
    if os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                out.setdefault((r["day"], r["orig"], r["dest"], r["cabin"]), []).append(r)
    for rows in out.values():
        rows.sort(key=lambda r: float(r["price"]))
    return out


PARTS = {"Any time": (0, 24), "Morning": (0, 12), "Afternoon": (12, 18), "Evening": (18, 24)}  # by the hour a flight leaves


def took(m):
    """"1 h 30" or "55 min"."""
    return f"{m // 60} h {m % 60:02d}" if m >= 60 else f"{m} min"


def line(label, day, r):
    """"Out · Tue 8 Dec: A3 992 09:25 → 10:55, 1 h 30 · €53"."""
    m = int(r["minutes"])
    took = f"{m // 60} h {m % 60:02d}" if m >= 60 else f"{m} min"
    return f"{label} · {day:%a} {day.day} {day:%b}: {r['flight']} {r['depart']} → {r['arrive']}, {took} · €{float(r['price']):,.0f}"


def render(orig, dest, cabin, depart, back, key):
    """Under the pass: the flights of the leave day (and the return day) when known; otherwise a button that fetches them on GitHub
    (with the app's token only; without one, nothing)."""
    import pandas as pd
    known = load()
    legs = [("Out", orig, dest, depart)] + ([("Back", dest, orig, back)] if back else [])
    found, missing = [], []
    for label, o, d, day in legs:
        rows = known.get((day.isoformat(), o, d, cabin))
        if rows:
            found.append((label, o, d, day, rows))
        else:  # ponytail: a day Google answered with no flights stays "missing", so the button stays; fine for a rare case
            missing.append((o, d, day))
    if found:  # Google Flights' list: every flight of the day with its times, the time of day to filter on
        st.markdown('<p class="group">The flights on these days</p>', unsafe_allow_html=True)
        when = st.pills("Leaves", list(PARTS), default="Any time", required=True, key=f"{key}-when")
        lo, hi = PARTS[when]
        cols = st.columns(len(found), gap="medium")
        for col, (label, o, d, day, rows) in zip(cols, found):
            keep = [r for r in rows if lo <= int(r["depart"][:2]) < hi]
            with col:
                st.markdown(f"**{label} · {day:%a} {day.day} {day:%b} · {o} → {d}**")
                if not keep:
                    st.caption(f"No flight leaves in the {when.lower()} that day.")
                    continue
                st.dataframe(pd.DataFrame([{"Flight": r["flight"], "Leaves": r["depart"], "Arrives": r["arrive"],
                                            "Takes": took(int(r["minutes"])), "€": float(r["price"])} for r in keep]),
                             hide_index=True, width="stretch", column_config={"€": st.column_config.NumberColumn(format="€%d")})
        st.caption("Cheapest first. The price is Google's lowest fare for that flight, one adult, as last checked.")
    tok = gh.token()
    if missing and tok and f"gh-{key}" not in st.session_state:
        days = " and ".join(f"{d:%a} {d.day} {d:%b}" for _, _, d in missing)
        if st.button(f"Show the flights of {days}", key=key, icon=":material/schedule:", type="tertiary"):
            try:
                st.session_state[f"gh-{key}"] = (gh.fetch_details(tok, missing, cabin), time.time())
            except gh.GitHubError as err:
                st.warning(str(err), icon=":material/warning:")
    ui.github_progress(key, "fetch-details.yml", PATH, what="flight times")
    ui.github_note(key)
