"""The same top for every page: a compact title and one line on what the page is for. A module of its own, so Streamlit Cloud loads it
fresh on a redeploy instead of an older ui.py that lacks it."""
import html

import streamlit as st

from ui import FONT, INK, MUTED

STYLE = f"""<style>
.page-head h1 {{ font: 700 1.6rem/1.2 {FONT}; letter-spacing:-0.02em; color:{INK}; margin:0 0 .25rem; padding:0; }}
.page-head p {{ color:{MUTED}; font-size:.95rem; margin:0 0 1rem; max-width:72ch; }}
</style>"""


def page_head(title, sub):
    """The page's name and what it is for, the same size and place on every page."""
    st.markdown(STYLE + f'<div class="page-head"><h1>{html.escape(title)}</h1><p>{html.escape(sub)}</p></div>', unsafe_allow_html=True)
