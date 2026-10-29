import streamlit as st

from ui import CSS

st.set_page_config(page_title="AEGEAN fare tracker", page_icon="✈️", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
st.navigation([st.Page("views/flights.py", title="Flights", icon="✈️", default=True),
               st.Page("views/hotel.py", title="Hotels", icon="🏨"),
               st.Page("views/stats.py", title="Statistics", icon="📊"),
               st.Page("views/health.py", title="Health", icon="🩺")]).run()
