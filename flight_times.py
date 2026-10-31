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


def line(label, day, r):
    """"Out · Tue 8 Dec: A3 992 09:25 → 10:55, 1 h 30 · €53"."""
    m = int(r["minutes"])
    took = f"{m // 60} h {m % 60:02d}" if m >= 60 else f"{m} min"
    return f"{label} · {day:%a} {day.day} {day:%b}: {r['flight']} {r['depart']} → {r['arrive']}, {took} · €{float(r['price']):,.0f}"


def render(orig, dest, cabin, depart, back, key):
    """Under the pass: the flights of the leave day (and the return day) when known; otherwise a button that fetches them on GitHub
    (with the app's token only; without one, nothing)."""
    known = load()
    legs = [("Out", orig, dest, depart)] + ([("Back", dest, orig, back)] if back else [])
    lines, missing = [], []
    for label, o, d, day in legs:
        rows = known.get((day.isoformat(), o, d, cabin))
        if rows:
            lines += [line(label, day, r) for r in rows]
        else:  # ponytail: a day Google answered with no flights stays "missing", so the button stays; fine for a rare case
            missing.append((o, d, day))
    if lines:
        st.caption("  \n".join(lines + ["Prices are Google's lowest fare for that flight."]))
    tok = gh.token()
    if missing and tok and f"gh-{key}" not in st.session_state:
        if st.button("Show flight times", key=key, icon=":material/schedule:"):
            try:
                st.session_state[f"gh-{key}"] = (gh.fetch_details(tok, missing, cabin), time.time())
            except gh.GitHubError as err:
                st.warning(str(err), icon=":material/warning:")
    ui.github_progress(key, "fetch-details.yml", PATH, what="flight times")
    ui.github_note(key)
