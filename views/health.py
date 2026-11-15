import os

import streamlit as st

from checks import run_checks
from freshness import freshness
from layout import page_head

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
page_head("Status", "Whether each data source answers right now, and the GitHub minutes used this month. Look here first if results look odd or empty.")
st.markdown('<p class="card-title">GitHub minutes</p>', unsafe_allow_html=True)
import budget as _budget
import gh as _gh
_usage = _gh.budget()
if _usage:
    st.caption(_budget.line(_usage))
else:
    st.caption("The monthly allowance of free automatic updates can't be shown on this site.")
    with st.expander("For the site owner: how to show it"):
        st.markdown("Add a GitHub token with the *Plan: read* permission to the app's secrets (see the README, “GitHub minutes”). "
                    "Without it the app cannot read the usage; the daily updates are not affected.")
st.markdown('<p class="card-title">Data freshness</p>', unsafe_allow_html=True)
for name, last, age, level, how in freshness(DATA):
    when = "no data yet" if last is None else f"last collected {last[:10]} ({'today' if age == 0 else 'yesterday' if age == 1 else f'{age} days ago'})"
    {"ok": st.success, "late": st.warning, "stale": st.error}[level](f"**{name}**: {when}." + ("" if level == "ok" else f" {how}"))
if st.button("Run checks", type="primary"):
    with st.spinner("Asking each source once…"):
        results = run_checks()
    for name, ok, secs, detail in results:
        (st.success if ok else st.error)(f"**{name}**: {'works' if ok else 'FAILED'} ({secs} s). {detail}")
    if any("429" in r[3] for r in results):
        st.warning("HTTP 429 means Google is limiting requests from this server. It clears by itself, usually within an hour. "
                   "Avoid repeated fetches meanwhile, or run the app on your own computer.")
    if not all(r[1] for r in results):
        st.info("A failed source usually means Google or Xotelo is blocking or has changed. Try again in a few minutes. "
                "If it keeps failing, the app needs an update.")
else:
    st.caption("Takes about 10 seconds.")

st.markdown('<p class="card-title">Hotel prices with taxes</p>', unsafe_allow_html=True)
if st.button("Check whether this server gets prices with taxes", key="tax-probe"):
    with st.spinner("Asking Google Hotels twice…"):
        import hotels as _h  # sets the country on Google's requests
        from stays.search import client as _c
        import stays as _stays
        got = {}
        for label, q in (("with the country", f"?hl=en&gl={_h.COUNTRY}"), ("without", "")):
            _c.Client.ENDPOINT = _c.Client.ENDPOINT.split("?")[0] + q
            _c._shared_client = None
            try:
                r = _stays.search_hotels("Amrâth Grand Hotel De L'Empereur Maastricht", check_in="2026-12-04", check_out="2026-12-07",
                                         adults=2, currency="EUR")
                got[label] = {x["name"]: x["display_price"] for x in r["hotels"]}.get("Amrâth Grand Hotel de l'Empereur")
            except Exception as e:  # noqa: BLE001
                got[label] = f"error: {e}"
        _c.Client.ENDPOINT = _c.Client.ENDPOINT.split("?")[0] + f"?hl=en&gl={_h.COUNTRY}"
        _c._shared_client = None
    st.write(f"Amrâth Maastricht, 4–7 Dec, 2 adults, a night: {got['with the country']} with the country, {got['without']} without. "
             + ("Different: this server gets prices with taxes." if got["with the country"] != got["without"] else
                "The same: Google ignores the country here, so prices from this server are before taxes."))
