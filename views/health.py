import streamlit as st

from checks import run_checks
from ui import route

route("Health", None, "Check that each data source answers right now. Run this first if results look odd or empty.")
if st.button("Run checks", type="primary"):
    with st.spinner("Asking each source once…"):
        results = run_checks()
    for name, ok, secs, detail in results:
        (st.success if ok else st.error)(f"**{name}**: {'works' if ok else 'FAILED'} ({secs} s). {detail}")
    if not all(r[1] for r in results):
        st.info("A failed source usually means Google or Xotelo is blocking or has changed. Try again in a few minutes. "
                "If it keeps failing, the app needs an update.")
else:
    st.caption("Takes about 10 seconds.")
