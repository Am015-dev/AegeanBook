import streamlit as st

from ui import CSS

st.set_page_config(page_title="AEGEAN fare tracker", page_icon="✈️", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
st.logo("static/logo.svg", size="large")
st.navigation([st.Page("views/flights.py", title="Flights", icon=":material/flight_takeoff:", default=True),
               st.Page("views/routes.py", title="Routes", icon=":material/compare_arrows:"),
               st.Page("views/hotel.py", title="Hotels", icon=":material/hotel:"),
               st.Page("views/stats.py", title="Statistics", icon=":material/bar_chart:"),
               st.Page("views/database.py", title="Database", icon=":material/database:"),
               st.Page("views/health.py", title="Health", icon=":material/monitor_heart:")], position="top").run()
